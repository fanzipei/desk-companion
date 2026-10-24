/* Copyright 2026 fanzipei
 * SPDX-License-Identifier: Apache-2.0
 */
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <stdint.h>
#include <stdbool.h>
#include <inttypes.h>
#include <time.h>
#include "freertos/FreeRTOS.h"
#include "freertos/task.h"
#include "freertos/queue.h"
#include "driver/gpio.h"
#include "esp_timer.h"
#include "esp_log.h"
#include "nvs_flash.h"
#include "nvs.h"
#include "cJSON.h"
#include "nimble/nimble_port.h"
#include "nimble/nimble_port_freertos.h"
#include "host/ble_hs.h"
#include "host/ble_uuid.h"
#include "host/util/util.h"
#include "services/gap/ble_svc_gap.h"
#include "services/gatt/ble_svc_gatt.h"
#include "u8g2_st7305.h"
#include "button_logic.h"
#include "display_logic.h"
#include "battery.h"
#include "device_clock.h"
#include "reminder_logic.h"

#define NOTE_MAX 20
#define TEXT_MAX 3072
#define LEGACY_NEWS_MAX 20
#define NEWS_MAX 50
#define SUMMARY_MAX 768
#define NEWS_DELETED_MAX 256
#define NEWS_MAGIC 0x4e455732
#define SCHEDULE_MAX 64
#define SCHEDULE_DONE_MAX 128
#define SCHEDULE_MAGIC 0x53434831
#define GDELT_ID_BASE (1LL << 48)
#define RX_MAX 16384
#define DEVICE_NAME "DeskCompanion"
#define STORE_MAGIC 0x434f4d50
#define LONG_MS 1000
#define DOUBLE_MS 350
#define WIDTH 400
#define HEIGHT 300
#define BODY_FONT u8g2_font_wqy16_t_gb2312b
#define SMALL_FONT u8g2_font_wqy12_t_gb2312b

/* 9b7e0001/2/3-3c1a-4c25-bad1-6d1a9f3e6200, little endian. */
#define UUID_BYTES(x) 0x00,0x62,0x3e,0x9f,0x1a,0x6d,0xd1,0xba,0x25,0x4c,0x1a,0x3c,x,0x00,0x7e,0x9b
static const ble_uuid128_t svc_uuid = BLE_UUID128_INIT(UUID_BYTES(1));
static const ble_uuid128_t rx_uuid = BLE_UUID128_INIT(UUID_BYTES(2));
static const ble_uuid128_t tx_uuid = BLE_UUID128_INIT(UUID_BYTES(3));

typedef struct { char id[37]; int64_t created; uint8_t pinned; char text[TEXT_MAX+1]; } note_t;
typedef struct { int64_t id; int64_t timestamp; int score; int comments; char title[512]; char domain[128]; } news_t;
typedef struct {
    uint32_t magic, version;
    int count, news_count, mode;
    int64_t news_updated;
    int seen_next;
    char seen[64][37];
    note_t notes[NOTE_MAX];
    news_t news[LEGACY_NEWS_MAX];
} store_t;
/* Separate versioned storage keeps the original note blob layout unchanged. */
typedef struct { news_t article; uint8_t pinned, title_only; char summary[SUMMARY_MAX+1]; } news_item_t;
typedef struct {
    uint32_t magic, version;
    int count, limit, deleted_count, deleted_next;
    int64_t updated, deleted[NEWS_DELETED_MAX];
    news_item_t items[NEWS_MAX];
} news_store_t;
typedef struct { char *text; uint32_t generation; uint16_t conn; } packet_t;
typedef struct { int pin, stable, hits; bool long_fired; int64_t down; } key_state_t;
typedef struct { char id[33]; int64_t due; int count; char ids[NOTE_MAX][37]; } scheduled_group_t;
typedef struct {
    uint32_t magic; int count,done_count,done_next;
    scheduled_group_t groups[SCHEDULE_MAX]; char done[SCHEDULE_DONE_MAX][33];
} schedule_store_t;

static const char *TAG = "companion";
static store_t *store;
static news_store_t *news, *news_stage;
static char stage_token[37];
static int stage_expected, stage_received;
static uint32_t stage_generation;
static buttons_t buttons;
static display_settings_t display={.magic=DISPLAY_MAGIC,.hints=1,.font_size=16,.line_spacing=7};
static u8g2_st7305_t lcd;
static nvs_handle_t nvs;
static QueueHandle_t rx_queue, tx_queue;
static volatile uint16_t connection = BLE_HS_CONN_HANDLE_NONE;
static volatile uint32_t generation;
static volatile bool subscribed;
static uint16_t tx_handle;
static char rx_buffer[RX_MAX+1];
static size_t rx_length;
static int64_t rx_last;
static int note_index, news_index, page;
static uint8_t note_limit=NOTE_MAX;
static bool dirty = true;
static char toast[96];
static int64_t toast_until;
static char reminder_text[TEXT_MAX+1];
static int64_t reminder_until;
static int64_t reminder_started;
static bool reminder_flash;
static char reminder_ids[NOTE_MAX][37];
static int reminder_count,reminder_current;
static schedule_store_t *schedule,*schedule_stage;
static int schedule_expected,schedule_received;
static uint32_t schedule_generation;
static char schedule_token[37];

static int64_t now_ms(void) { return esp_timer_get_time()/1000; }
static void advertise(void);
static void action(const char *name);
static void draw(void);
static int page_count(void);

static cJSON *display_json(void) {
    cJSON *obj=cJSON_CreateObject();
    cJSON_AddStringToObject(obj,"theme",display.dark?"dark":"light");
    cJSON_AddBoolToObject(obj,"show_hints",display.hints);
    cJSON_AddNumberToObject(obj,"font_size",display.font_size);
    cJSON_AddNumberToObject(obj,"line_spacing",display.line_spacing);
    cJSON_AddNumberToObject(obj,"visible_lines",display_lines(&display,store->mode));
    return obj;
}

static const char *str_field(cJSON *obj, const char *name) {
    cJSON *item = cJSON_GetObjectItemCaseSensitive(obj, name);
    return cJSON_IsString(item) ? item->valuestring : "";
}
static int64_t num_field(cJSON *obj, const char *name) {
    cJSON *item = cJSON_GetObjectItemCaseSensitive(obj, name);
    return cJSON_IsNumber(item) ? (int64_t)item->valuedouble : 0;
}
static void copy_text(char *dest, const char *src, size_t cap) {
    size_t len = strlen(src);
    if (len >= cap) {
        len = cap-1;
        while (len > 0 && ((unsigned char)src[len] & 0xc0) == 0x80) len--;
    }
    memcpy(dest, src, len); dest[len] = 0;
}
static void show_toast(const char *text) {
    copy_text(toast, text, sizeof(toast)); toast_until = now_ms()+2200; dirty = true;
}
static bool persist(void) {
    esp_err_t err = nvs_set_blob(nvs, "state", store, sizeof(*store));
    if (err == ESP_OK) err = nvs_set_blob(nvs,"news_v2",news,sizeof(*news));
    if (err == ESP_OK) err = nvs_commit(nvs);
    if (err != ESP_OK) { ESP_LOGE(TAG, "persist: %s", esp_err_to_name(err)); show_toast("保存失败，请保留 PC 备份"); }
    return err == ESP_OK;
}
static void queue_json(cJSON *obj) {
    packet_t packet = { .text = cJSON_PrintUnformatted(obj), .generation = generation, .conn = connection };
    cJSON_Delete(obj);
    if (!packet.text) return;
    if (connection == BLE_HS_CONN_HANDLE_NONE || !subscribed || xQueueSend(tx_queue, &packet, 0) != pdTRUE) free(packet.text);
}
static void event(const char *type, const char *id, int pinned) {
    cJSON *obj = cJSON_CreateObject();
    cJSON_AddStringToObject(obj, "event", type);
    if (id) cJSON_AddStringToObject(obj, "id", id);
    if (pinned >= 0) cJSON_AddBoolToObject(obj, "pinned", pinned);
    cJSON_AddNumberToObject(obj, "mode", store->mode);
    queue_json(obj);
}
static void tx_task(void *arg) {
    packet_t packet;
    for (;;) {
        xQueueReceive(tx_queue, &packet, portMAX_DELAY);
        size_t length = strlen(packet.text), offset = 0;
        /* Keep a complete frame contiguous on the notify stream. */
        while (offset <= length && packet.generation == generation && packet.conn == connection && subscribed) {
            uint16_t mtu = ble_att_mtu(packet.conn);
            size_t cap = mtu > 23 ? mtu-3 : 20;
            if (cap > 180) cap = 180;
            char chunk[180]; size_t amount = length-offset;
            if (amount > cap) amount = cap;
            if (amount) memcpy(chunk, packet.text+offset, amount);
            if (amount < cap) { chunk[amount++] = '\n'; }
            struct os_mbuf *mb = ble_hs_mbuf_from_flat(chunk, amount);
            if (!mb) { vTaskDelay(pdMS_TO_TICKS(15)); continue; }
            int rc = ble_gatts_notify_custom(packet.conn, tx_handle, mb);
            if (rc != 0) { vTaskDelay(pdMS_TO_TICKS(20)); continue; }
            offset += amount;
            vTaskDelay(pdMS_TO_TICKS(7));
        }
        free(packet.text);
    }
}

static cJSON *note_json(const note_t *note) {
    cJSON *obj = cJSON_CreateObject();
    cJSON_AddStringToObject(obj, "id", note->id);
    cJSON_AddStringToObject(obj, "text", note->text);
    cJSON_AddNumberToObject(obj, "created", note->created);
    cJSON_AddBoolToObject(obj, "pinned", note->pinned);
    return obj;
}
static cJSON *news_json(const news_item_t *item) {
    cJSON *obj=cJSON_CreateObject(); const news_t *article=&item->article;
    cJSON_AddNumberToObject(obj,"id",article->id); cJSON_AddNumberToObject(obj,"time",article->timestamp);
    cJSON_AddStringToObject(obj,"title",article->title); cJSON_AddStringToObject(obj,"domain",article->domain);
    cJSON_AddNumberToObject(obj,"score",article->score); cJSON_AddNumberToObject(obj,"comments",article->comments);
    cJSON_AddStringToObject(obj,"summary",item->summary); cJSON_AddBoolToObject(obj,"pinned",item->pinned);
    cJSON_AddStringToObject(obj,"summary_basis",item->title_only?"title":"article");
    return obj;
}
static int find_news(int64_t id) { for(int i=0;i<news->count;i++) if(news->items[i].article.id==id) return i; return -1; }
static bool news_deleted(int64_t id) { for(int i=0;i<news->deleted_count;i++) if(news->deleted[i]==id) return true; return false; }
static void news_event(const char *type,int64_t id,int pinned) {
    cJSON *obj=cJSON_CreateObject(); cJSON_AddStringToObject(obj,"event",type); cJSON_AddNumberToObject(obj,"id",id);
    if(pinned>=0) cJSON_AddBoolToObject(obj,"pinned",pinned);
    queue_json(obj);
}
static void remove_news(int index) {
    memmove(&news->items[index],&news->items[index+1],(news->count-index-1)*sizeof(news_item_t)); news->count--;
    if(news_index>=news->count) news_index=news->count?news->count-1:0;
    page=0; dirty=true;
}
static void sort_news_pins(void) {
    int64_t current=news->count?news->items[news_index].article.id:0;
    for(int i=1;i<news->count;i++) if(news->items[i].pinned) {
        news_item_t *temp=malloc(sizeof(*temp)); if(!temp) break;
        *temp=news->items[i]; int j=i;
        while(j>0 && !news->items[j-1].pinned) { news->items[j]=news->items[j-1]; j--; }
        news->items[j]=*temp; free(temp);
    }
    int found=find_news(current); news_index=found>=0?found:0;
}
static bool stage_item(cJSON *obj) {
    int64_t id=num_field(obj,"id");
    if(!cJSON_IsObject(obj) || id<=0 || !*str_field(obj,"title") || strlen(str_field(obj,"summary"))>SUMMARY_MAX) return false;
    for(int i=0;i<news_stage->count;i++) if(news_stage->items[i].article.id==id) return false;
    if(news_stage->count>=NEWS_MAX) return false;
    news_item_t *item=&news_stage->items[news_stage->count++]; memset(item,0,sizeof(*item));
    item->article.id=id; item->article.timestamp=num_field(obj,"time");
    item->article.score=num_field(obj,"score"); item->article.comments=num_field(obj,"comments");
    copy_text(item->article.title,str_field(obj,"title"),sizeof(item->article.title));
    copy_text(item->article.domain,str_field(obj,"domain"),sizeof(item->article.domain));
    copy_text(item->summary,str_field(obj,"summary"),sizeof(item->summary));
    item->pinned=cJSON_IsTrue(cJSON_GetObjectItem(obj,"pinned"));
    item->title_only=!strcmp(str_field(obj,"summary_basis"),"title"); return true;
}
static const char *commit_news(void) {
    int64_t current=news->count?news->items[news_index].article.id:0;
    int count=0;
    /* Merge hardware mutations made during transfer; deletion cannot be undone by a stale packet. */
    for(int i=0;i<news_stage->count;i++) {
        news_item_t *item=&news_stage->items[i]; if(news_deleted(item->article.id)) continue;
        int old=find_news(item->article.id); if(old>=0) item->pinned=news->items[old].pinned;
        news_stage->items[count++]=*item;
    }
    news_stage->count=count;
    for(int i=0;i<news->count;i++) if(news->items[i].pinned && !news_deleted(news->items[i].article.id)) {
        bool found=false; for(int j=0;j<news_stage->count;j++) if(news_stage->items[j].article.id==news->items[i].article.id) found=true;
        if(!found) {
            if(news_stage->count>=news_stage->limit) {
                int j=news_stage->count-1; while(j>=0 && news_stage->items[j].pinned) j--;
                if(j<0) return "limit_below_pinned";
                memmove(&news_stage->items[j],&news_stage->items[j+1],(news_stage->count-j-1)*sizeof(news_item_t)); news_stage->count--;
            }
            news_stage->items[news_stage->count++]=news->items[i];
        }
    }
    memcpy(news_stage->deleted,news->deleted,sizeof(news->deleted));
    news_stage->deleted_count=news->deleted_count; news_stage->deleted_next=news->deleted_next;
    news_store_t *old=news; news=news_stage; news_stage=NULL;
    if(!persist()) { news_stage=news; news=old; return "storage_error"; }
    free(old); news_index=0; for(int i=0;i<news->count;i++) if(news->items[i].article.id==current) news_index=i;
    sort_news_pins(); page=0; dirty=true; return NULL;
}
static void add_state(cJSON *reply) {
    cJSON *notes = cJSON_AddArrayToObject(reply, "notes");
    for (int i=0; i<store->count; i++) cJSON_AddItemToArray(notes, note_json(&store->notes[i]));
    cJSON_AddNumberToObject(reply, "mode", store->mode);
    cJSON_AddNumberToObject(reply, "news_count", news->count);
    cJSON_AddNumberToObject(reply, "news_updated", news->updated);
    cJSON_AddNumberToObject(reply, "news_limit", news->limit);
    cJSON_AddNumberToObject(reply, "max_note_bytes", TEXT_MAX);
    cJSON_AddNumberToObject(reply, "version", 2);
    cJSON *clock=cJSON_AddObjectToObject(reply,"clock");
    cJSON_AddBoolToObject(clock,"valid",device_clock_valid());
    cJSON_AddStringToObject(clock,"source",device_clock_source());
    cJSON_AddBoolToObject(clock,"rtc_available",device_clock_rtc_available());
    cJSON_AddNumberToObject(clock,"unix",device_clock_valid()?time(NULL):0);
    cJSON_AddNumberToObject(clock,"last_pc_sync",device_clock_last_sync());
    char local_time[32]; device_clock_format(local_time,sizeof(local_time),true);
    cJSON_AddStringToObject(clock,"local",local_time);
    cJSON_AddBoolToObject(reply,"reminder_active",reminder_until!=0);
    cJSON_AddBoolToObject(reply,"reminder_flash",reminder_flash);
    cJSON_AddNumberToObject(reply,"reminder_count",reminder_count);
    cJSON_AddNumberToObject(reply,"scheduled_reminders",schedule->count);
    cJSON *completed=cJSON_AddArrayToObject(reply,"reminders_completed");
    for(int i=0;i<schedule->done_count;i++) cJSON_AddItemToArray(completed,cJSON_CreateString(schedule->done[i]));
    cJSON_AddItemToObject(reply,"display",display_json());
    cJSON_AddNumberToObject(reply,"content_pages",page_count());
    battery_state_t charge=battery_state();
    cJSON *battery=cJSON_AddObjectToObject(reply,"battery");
    cJSON_AddBoolToObject(battery,"valid",charge.valid);
    cJSON_AddBoolToObject(battery,"calibrated",charge.calibrated);
    cJSON_AddNumberToObject(battery,"millivolts",charge.millivolts);
    if(charge.valid) cJSON_AddNumberToObject(battery,"percent",charge.percent);
    else cJSON_AddNullToObject(battery,"percent");
    cJSON_AddNumberToObject(reply,"current_note_index",note_index);
    cJSON_AddNumberToObject(reply,"page",page);
    if(store->count) cJSON_AddStringToObject(reply,"current_note_id",store->notes[note_index].id);
    cJSON_AddNumberToObject(reply, "note_limit", note_limit);
    int gdelt_count=0;
    cJSON *meta=cJSON_AddObjectToObject(reply,"news_meta");
    cJSON *ids=cJSON_AddArrayToObject(meta,"ids"), *pinned=cJSON_AddArrayToObject(meta,"pinned"), *deleted=cJSON_AddArrayToObject(meta,"deleted");
    for(int i=0;i<news->count;i++) {
        int64_t id=news->items[i].article.id;
        if(id>=GDELT_ID_BASE) gdelt_count++;
        cJSON_AddItemToArray(ids,cJSON_CreateNumber(id));
        if(news->items[i].pinned) cJSON_AddItemToArray(pinned,cJSON_CreateNumber(id));
    }
    for(int i=0;i<news->deleted_count;i++) cJSON_AddItemToArray(deleted,cJSON_CreateNumber(news->deleted[i]));
    cJSON *sources=cJSON_AddObjectToObject(reply,"news_source_counts");
    cJSON_AddNumberToObject(sources,"hn",news->count-gdelt_count);
    cJSON_AddNumberToObject(sources,"gdelt",gdelt_count);
    if(news->count) {
        cJSON_AddNumberToObject(reply,"current_news_id",news->items[news_index].article.id);
        cJSON_AddStringToObject(reply,"current_news_source",news->items[news_index].article.id>=GDELT_ID_BASE?"gdelt":"hn");
    }
}
static int find_note(const char *id) {
    for (int i=0; i<store->count; i++) if (!strcmp(store->notes[i].id, id)) return i;
    return -1;
}
static void sort_note_pins(void) {
    char current[37]=""; if(store->count) copy_text(current,store->notes[note_index].id,sizeof(current));
    note_t *temp=malloc(sizeof(*temp)); if(!temp) return;
    for(int i=1;i<store->count;i++) if(store->notes[i].pinned) {
        *temp=store->notes[i]; int j=i;
        while(j>0 && !store->notes[j-1].pinned) { store->notes[j]=store->notes[j-1]; j--; }
        store->notes[j]=*temp;
    }
    free(temp); int found=find_note(current); note_index=found>=0?found:0;
}
static void remove_note(int index) {
    memmove(&store->notes[index], &store->notes[index+1], (store->count-index-1)*sizeof(note_t));
    store->count--;
    if (note_index >= store->count) note_index = store->count > 0 ? store->count-1 : 0;
    page=0; dirty=true;
}

static void start_reminder(const char *text,cJSON *ids) {
    reminder_count=reminder_current=0;
    if(ids) { cJSON *id; cJSON_ArrayForEach(id,ids) {
        bool duplicate=false; for(int i=0;i<reminder_count;i++) if(!strcmp(reminder_ids[i],id->valuestring)) duplicate=true;
        if(!duplicate && find_note(id->valuestring)>=0) copy_text(reminder_ids[reminder_count++],id->valuestring,37);
    } }
    copy_text(reminder_text,text,sizeof(reminder_text));
    if(reminder_count) { note_index=find_note(reminder_ids[0]); copy_text(reminder_text,store->notes[note_index].text,sizeof(reminder_text)); }
    store->mode=0; page=0; reminder_started=now_ms();
    reminder_until=reminder_started+(reminder_count>12?reminder_count*5000:60000);
    reminder_flash=false; buttons_reset(&buttons); dirty=true;
}
static bool schedule_completed(const char *id) {
    for(int i=0;i<schedule->done_count;i++) if(!strcmp(schedule->done[i],id)) return true;
    return false;
}
static bool save_schedule(const schedule_store_t *value) {
    esp_err_t err=nvs_set_blob(nvs,"reminders_v1",value,sizeof(*value));
    if(err==ESP_OK) err=nvs_commit(nvs);
    if(err!=ESP_OK) ESP_LOGE(TAG,"schedule persist: %s",esp_err_to_name(err));
    return err==ESP_OK;
}
static const char *schedule_request(const char *op,cJSON *request,cJSON *reply) {
    if(!strcmp(op,"schedule_get")) {
        cJSON *items=cJSON_AddArrayToObject(reply,"items");
        for(int i=0;i<schedule->count;i++) {
            scheduled_group_t *group=&schedule->groups[i];
            cJSON *item=cJSON_CreateObject(), *ids=cJSON_AddArrayToObject(item,"ids");
            cJSON_AddStringToObject(item,"id",group->id); cJSON_AddNumberToObject(item,"due",group->due);
            for(int n=0;n<group->count;n++) cJSON_AddItemToArray(ids,cJSON_CreateString(group->ids[n]));
            cJSON_AddItemToArray(items,item);
        }
        return NULL;
    }
    if(!strcmp(op,"schedule_begin")) {
        int count=num_field(request,"count");
        if(count<0 || count>SCHEDULE_MAX) return "invalid_schedule";
        free(schedule_stage); schedule_stage=calloc(1,sizeof(*schedule_stage));
        if(!schedule_stage) return "out_of_memory";
        schedule_stage->magic=SCHEDULE_MAGIC; schedule_expected=count; schedule_received=0;
        schedule_generation=generation; copy_text(schedule_token,str_field(request,"token"),sizeof(schedule_token)); return NULL;
    }
    if(!schedule_stage || schedule_generation!=generation || strcmp(schedule_token,str_field(request,"token"))) return "invalid_schedule_transaction";
    if(!strcmp(op,"schedule_chunk")) {
        cJSON *items=cJSON_GetObjectItem(request,"items");
        if(!cJSON_IsArray(items) || num_field(request,"offset")!=schedule_received || schedule_received+cJSON_GetArraySize(items)>schedule_expected) return "invalid_schedule_offset";
        cJSON *item; cJSON_ArrayForEach(item,items) {
            const char *id=str_field(item,"id"); cJSON *ids=cJSON_GetObjectItem(item,"ids");
            int64_t due=num_field(item,"due");
            if(!*id || strlen(id)>32 || due<1704067200 || due>=4102444800LL || !cJSON_IsArray(ids) || cJSON_GetArraySize(ids)<1 || cJSON_GetArraySize(ids)>NOTE_MAX) return "invalid_schedule";
            cJSON *note; cJSON_ArrayForEach(note,ids) if(!cJSON_IsString(note) || !*note->valuestring || strlen(note->valuestring)>36) return "invalid_schedule";
            for(int i=0;i<schedule_stage->count;i++) if(!strcmp(schedule_stage->groups[i].id,id)) return "duplicate_schedule";
            if(!schedule_completed(id)) {
                scheduled_group_t *group=&schedule_stage->groups[schedule_stage->count++];
                copy_text(group->id,id,33); group->due=due;
                cJSON_ArrayForEach(note,ids) copy_text(group->ids[group->count++],note->valuestring,37);
            }
            schedule_received++;
        }
        return NULL;
    }
    if(strcmp(op,"schedule_commit")) return "unknown_operation";
    if(schedule_received!=schedule_expected) return "incomplete_schedule";
    int count=0;
    for(int i=0;i<schedule_stage->count;i++) if(!schedule_completed(schedule_stage->groups[i].id)) schedule_stage->groups[count++]=schedule_stage->groups[i];
    schedule_stage->count=count; schedule_stage->done_count=schedule->done_count; schedule_stage->done_next=schedule->done_next;
    memcpy(schedule_stage->done,schedule->done,sizeof(schedule->done));
    if(!save_schedule(schedule_stage)) return "storage_error";
    free(schedule); schedule=schedule_stage; schedule_stage=NULL;
    cJSON *ids=cJSON_AddArrayToObject(reply,"ids"), *completed=cJSON_AddArrayToObject(reply,"completed");
    for(int i=0;i<schedule->count;i++) cJSON_AddItemToArray(ids,cJSON_CreateString(schedule->groups[i].id));
    for(int i=0;i<schedule->done_count;i++) cJSON_AddItemToArray(completed,cJSON_CreateString(schedule->done[i]));
    return NULL;
}
static void schedules_tick(void) {
    if(reminder_until || !device_clock_valid()) return;
    int first=-1; int64_t now=time(NULL);
    for(int i=0;i<schedule->count;i++) if(schedule->groups[i].due<=now && (first<0 || schedule->groups[i].due<schedule->groups[first].due)) first=i;
    if(first<0) return;
    scheduled_group_t group=schedule->groups[first];
    memmove(&schedule->groups[first],&schedule->groups[first+1],(schedule->count-first-1)*sizeof(scheduled_group_t)); schedule->count--;
    copy_text(schedule->done[schedule->done_next++%SCHEDULE_DONE_MAX],group.id,33); schedule->done_next%=SCHEDULE_DONE_MAX;
    if(schedule->done_count<SCHEDULE_DONE_MAX) schedule->done_count++;
    save_schedule(schedule);
    cJSON *ids=cJSON_CreateArray(); for(int i=0;i<group.count;i++) cJSON_AddItemToArray(ids,cJSON_CreateString(group.ids[i]));
    start_reminder("这条便签已不在设备中，请在 PC 查看提醒。",ids); cJSON_Delete(ids);
    event("reminder_due",group.id,-1);
}

static void process_request(packet_t *packet) {
    if (packet->generation != generation) return;
    cJSON *request = cJSON_Parse(packet->text);
    if (!cJSON_IsObject(request)) { cJSON_Delete(request); return; }
    const char *rid = str_field(request, "rid"), *op = str_field(request, "op");
    cJSON *reply = cJSON_CreateObject();
    cJSON_AddStringToObject(reply, "rid", rid);
    cJSON_AddBoolToObject(reply, "ok", true);
    const char *error = NULL;
    bool mutated = false;
    if (!strcmp(op, "state")) {
        add_state(reply);
    } else if(!strncmp(op,"schedule_",9)) {
        error=schedule_request(op,request,reply);
    } else if(!strcmp(op,"time_sync")) {
        cJSON *value=cJSON_GetObjectItem(request,"unix");
        if(!cJSON_IsNumber(value) || value->valuedouble!=num_field(request,"unix") || !device_clock_set(num_field(request,"unix"))) error="invalid_time";
        else { dirty=true; cJSON_AddNumberToObject(reply,"unix",time(NULL)); cJSON_AddBoolToObject(reply,"rtc_available",device_clock_rtc_available()); }
    } else if(!strcmp(op,"remind")) {
        const char *text=str_field(request,"text");
        cJSON *ids=cJSON_GetObjectItem(request,"ids");
        if(!*text || strlen(text)>TEXT_MAX || (ids && (!cJSON_IsArray(ids) || cJSON_GetArraySize(ids)>NOTE_MAX))) error="invalid_reminder";
        if(!error && ids) { cJSON *id; cJSON_ArrayForEach(id,ids) if(!cJSON_IsString(id) || strlen(id->valuestring)>36) { error="invalid_reminder"; break; } }
        if(!error) {
            start_reminder(text,ids);
        }
    } else if(!strcmp(op,"display_config")) {
        display_settings_t requested=display;
        cJSON *theme=cJSON_GetObjectItem(request,"theme"), *hints=cJSON_GetObjectItem(request,"show_hints");
        cJSON *font=cJSON_GetObjectItem(request,"font_size"), *spacing=cJSON_GetObjectItem(request,"line_spacing");
        if(theme && (!cJSON_IsString(theme) || (strcmp(theme->valuestring,"light") && strcmp(theme->valuestring,"dark")))) error="invalid_theme";
        else if(hints && !cJSON_IsBool(hints)) error="invalid_hints";
        else if(font && (!cJSON_IsNumber(font) || font->valuedouble!=num_field(request,"font_size") || !display_size_valid(num_field(request,"font_size")))) error="invalid_font_size";
        else if(spacing && (!cJSON_IsNumber(spacing) || spacing->valuedouble!=num_field(request,"line_spacing") || num_field(request,"line_spacing")<0 || num_field(request,"line_spacing")>16)) error="invalid_line_spacing";
        if(!error) {
            if(theme) requested.dark=!strcmp(theme->valuestring,"dark");
            if(hints) requested.hints=cJSON_IsTrue(hints);
            if(font) requested.font_size=num_field(request,"font_size");
            if(spacing) requested.line_spacing=num_field(request,"line_spacing");
            if(memcmp(&requested,&display,sizeof(display))) {
                esp_err_t rc=nvs_set_blob(nvs,"display_v1",&requested,sizeof(requested));
                if(rc==ESP_OK) rc=nvs_commit(nvs);
                if(rc!=ESP_OK) error="storage_error";
                else { display=requested; page=0; dirty=true; }
            }
        }
        cJSON_AddItemToObject(reply,"display",display_json());
    } else if(!strcmp(op,"frame_get")) {
        draw();
        u8g2_t *u=u8g2_st7305_get_u8g2(&lcd);
        size_t bytes=u8g2_GetBufferTileWidth(u)*8*u8g2_GetBufferTileHeight(u);
        char *hex=malloc(bytes*2+1); const uint8_t *buffer=u8g2_GetBufferPtr(u);
        if(!hex) error="out_of_memory";
        else {
            static const char digits[]="0123456789abcdef";
            for(size_t i=0;i<bytes;i++) { hex[i*2]=digits[buffer[i]>>4]; hex[i*2+1]=digits[buffer[i]&15]; }
            hex[bytes*2]=0; cJSON_AddStringToObject(reply,"pixels",hex); free(hex);
            cJSON_AddNumberToObject(reply,"width",300); cJSON_AddNumberToObject(reply,"height",400);
            cJSON_AddNumberToObject(reply,"stride",u8g2_GetBufferTileWidth(u)*8);
            cJSON_AddBoolToObject(reply,"one_is_white",true);
        }
    } else if (!strcmp(op, "config")) {
        int64_t requested=num_field(request,"note_limit");
        if(requested<1 || requested>NOTE_MAX) error="invalid_limit";
        else if(requested<store->count) error="limit_below_count";
        else {
            esp_err_t err=nvs_set_u8(nvs,"note_limit",(uint8_t)requested);
            if(err==ESP_OK) err=nvs_commit(nvs);
            if(err!=ESP_OK) error="storage_error";
            else { note_limit=requested; dirty=true; }
        }
        cJSON_AddNumberToObject(reply,"note_limit",note_limit);
        cJSON_AddNumberToObject(reply,"minimum",store->count);
    } else if (!strcmp(op, "add")) {
        const char *id = str_field(request, "id"), *text = str_field(request, "text");
        if (!*id || strlen(id)>36 || !*text || strlen(text)>TEXT_MAX) error="invalid_note";
        else {
            bool seen=false;
            for (int i=0; i<64; i++) if (!strcmp(store->seen[i], id)) seen=true;
            if (seen || find_note(id)>=0) cJSON_AddBoolToObject(reply, "duplicate", true);
            else {
                int evict=-1;
                if (store->count >= note_limit) {
                    for (int i=store->count-1; i>=0; i--) if (!store->notes[i].pinned) { evict=i; break; }
                    if (evict<0) error="all_pinned";
                }
                if (!error) {
                    if (evict>=0) { cJSON_AddStringToObject(reply,"evicted",store->notes[evict].id); remove_note(evict); }
                    memmove(&store->notes[1], &store->notes[0], store->count*sizeof(note_t));
                    note_t *note=&store->notes[0]; memset(note,0,sizeof(*note));
                    copy_text(note->id,id,sizeof(note->id)); copy_text(note->text,text,sizeof(note->text));
                    note->created=num_field(request,"created"); store->count++;
                    copy_text(store->seen[store->seen_next++%64],id,37);
                    store->seen_next%=64;
                    note_index=page=0; sort_note_pins(); store->mode=0; mutated=true;
                    show_toast("便签已收到");
                }
            }
        }
    } else if (!strcmp(op,"delete") || !strcmp(op,"pin")) {
        int i=find_note(str_field(request,"id"));
        if (i<0) error="not_found";
        else if (!strcmp(op,"delete")) { remove_note(i); mutated=true; }
        else { store->notes[i].pinned=cJSON_IsTrue(cJSON_GetObjectItem(request,"pinned")); sort_note_pins(); mutated=true; }
    } else if (!strcmp(op,"mode")) {
        store->mode = num_field(request,"mode") ? 1 : 0; page=0; buttons_reset(&buttons); mutated=true;
    } else if (!strcmp(op,"navigate")) {
        action(str_field(request,"action"));
    } else if (!strcmp(op,"news_get")) {
        cJSON *items=cJSON_AddArrayToObject(reply,"items");
        for(int i=0;i<news->count;i++) cJSON_AddItemToArray(items,news_json(&news->items[i]));
        cJSON_AddNumberToObject(reply,"limit",news->limit); cJSON_AddNumberToObject(reply,"updated",news->updated);
    } else if (!strcmp(op,"news_begin") || !strcmp(op,"news")) {
        bool legacy=!strcmp(op,"news");
        cJSON *items=cJSON_GetObjectItemCaseSensitive(request,"items");
        int count=legacy?cJSON_GetArraySize(items):num_field(request,"count");
        int limit=cJSON_IsNumber(cJSON_GetObjectItem(request,"limit"))?num_field(request,"limit"):20;
        int pinned_count=0; for(int i=0;i<news->count;i++) pinned_count+=!!news->items[i].pinned;
        if(count<0 || count>NEWS_MAX || limit<1 || limit>NEWS_MAX || count>limit || (legacy && !cJSON_IsArray(items))) error="invalid_news";
        else if(limit<pinned_count) error="limit_below_pinned";
        else {
            free(news_stage); news_stage=calloc(1,sizeof(*news_stage));
            if(!news_stage) error="out_of_memory";
            else {
                news_stage->magic=NEWS_MAGIC; news_stage->version=2; news_stage->limit=limit;
                news_stage->updated=num_field(request,"updated"); stage_expected=count; stage_received=0;
                stage_generation=generation; copy_text(stage_token,str_field(request,"token"),sizeof(stage_token));
                if(legacy) {
                    cJSON *item; cJSON_ArrayForEach(item,items) if(!stage_item(item)) { error="invalid_news"; break; }
                    if(!error) error=commit_news();
                }
            }
        }
    } else if (!strcmp(op,"news_chunk")) {
        cJSON *items=cJSON_GetObjectItemCaseSensitive(request,"items");
        if(!news_stage || stage_generation!=generation || strcmp(stage_token,str_field(request,"token"))) error="invalid_news_transaction";
        else if(num_field(request,"offset")!=stage_received || !cJSON_IsArray(items) || stage_received+cJSON_GetArraySize(items)>stage_expected) error="invalid_news_offset";
        else {
            cJSON *item; cJSON_ArrayForEach(item,items) {
                if(!stage_item(item)) { error="invalid_news"; free(news_stage); news_stage=NULL; break; }
                stage_received++;
            }
        }
    } else if (!strcmp(op,"news_commit")) {
        if(!news_stage || stage_generation!=generation || strcmp(stage_token,str_field(request,"token")) || stage_received!=stage_expected) error="incomplete_news";
        else error=commit_news();
    } else error="unknown_operation";
    if (mutated) { dirty=true; if (!persist()) error="storage_error"; }
    if (error) {
        cJSON_ReplaceItemInObject(reply,"ok",cJSON_CreateBool(false));
        cJSON_AddStringToObject(reply,"error",error);
    }
    queue_json(reply); cJSON_Delete(request);
}

static const uint8_t *body_font(void) { return display.font_size==12 || display.font_size==24?SMALL_FONT:BODY_FONT; }
static int body_scale(void) { return display.font_size>=24?2:1; }
static uint8_t glyph_display(u8x8_t *u,uint8_t message,uint8_t arg,void *data) {
    static const u8x8_display_info_t info={.tile_width=4,.tile_height=4,.pixel_width=32,.pixel_height=32};
    if(message==U8X8_MSG_DISPLAY_SETUP_MEMORY) u8x8_d_helper_display_setup_memory(u,&info);
    return 1;
}
static void body_line(u8g2_t *u,int x,int y,const char *text) {
    if(body_scale()==1) { u8g2_DrawUTF8(u,x,y,text); return; }
    /* Render real bitmap glyphs into an offscreen buffer and scale them 2x.
       The scratch display never initializes hardware or sends a frame. */
    static u8g2_t glyphs; static uint8_t glyph_buffer[128]; static bool ready;
    if(!ready) {
        u8g2_SetupDisplay(&glyphs,glyph_display,u8x8_cad_empty,NULL,NULL);
        u8g2_SetupBuffer(&glyphs,glyph_buffer,4,u8g2_ll_hvline_vertical_top_lsb,U8G2_R0); ready=true;
    }
    u8g2_SetFont(&glyphs,body_font()); u8g2_SetFontPosTop(&glyphs); u8g2_SetFontMode(&glyphs,1);
    int base=display.font_size/2;
    const unsigned char *p=(const unsigned char*)text;
    while(*p) {
        int count=(*p<0x80)?1:((*p&0xe0)==0xc0?2:((*p&0xf0)==0xe0?3:4));
        char character[5]={0}; memcpy(character,p,count); p+=count;
        u8g2_ClearBuffer(&glyphs);
        /* U8g2's top font reference starts at y-1; leave that first row in the buffer. */
        int advance=u8g2_DrawUTF8(&glyphs,2,1,character);
        const uint8_t *pixels=u8g2_GetBufferPtr(&glyphs);
        for(int gy=0;gy<base;gy++) for(int gx=-2;gx<base+2;gx++) {
            if(pixels[(gy/8)*32+gx+2] & (1<<(gy%8))) u8g2_DrawBox(u,x+gx*2,y+gy*2,2,2);
        }
        x+=advance*2;
    }
}

/* UTF-8 wrapping; every byte of a long memo remains reachable via paging. */
static int wrapped_text(const char *text, int selected_page, bool draw) {
    u8g2_t *u=u8g2_st7305_get_u8g2(&lcd);
    u8g2_SetFont(u,body_font()); u8g2_SetFontPosTop(u); u8g2_SetFontMode(u,1);
    int lines=display_lines(&display,store->mode), height=display.font_size+display.line_spacing;
    char line[192]={0}; int line_no=0; size_t used=0;
    const unsigned char *p=(const unsigned char*)text;
    while (*p) {
        int count=(*p<0x80)?1:((*p&0xe0)==0xc0?2:((*p&0xf0)==0xe0?3:4));
        bool newline=*p=='\n';
        char candidate[200]; memcpy(candidate,line,used);
        if (!newline) memcpy(candidate+used,p,count);
        candidate[used+(newline?0:count)]=0;
        if (newline || used+(size_t)count>=sizeof(line)-1 || (used && u8g2_GetUTF8Width(u,candidate)*body_scale()>368)) {
            if (draw && line_no/lines==selected_page) body_line(u,16,53+(line_no%lines)*height,line);
            line_no++; used=0; line[0]=0;
            if (newline) { p++; continue; }
        }
        memcpy(line+used,p,count); used+=count; line[used]=0; p+=count;
    }
    if (used || line_no==0) {
        if(draw && line_no/lines==selected_page) body_line(u,16,53+(line_no%lines)*height,line);
        line_no++;
    }
    return (line_no+lines-1)/lines;
}
static int page_count(void) {
    if(store->mode && news->count) {
        news_item_t *item=&news->items[news_index];
        return wrapped_text(*item->summary?item->summary:item->article.title,0,false);
    }
    return store->count ? wrapped_text(store->notes[note_index].text,0,false) : 1;
}
static void action(const char *name) {
    if(reminder_until) { reminder_until=0; reminder_flash=false; reminder_count=0; dirty=true; return; }
    if (!strcmp(name,"toggle")) {
        store->mode=!store->mode; page=0; buttons_reset(&buttons); persist(); event("mode",NULL,-1);
    } else if (!strcmp(name,"next")) {
        if (store->mode) { if(news->count && ++page>=page_count()) { news_index=(news_index+1)%news->count; page=0; } }
        else if(store->count) { if(++page>=page_count()) { page=0; note_index=(note_index+1)%store->count; } }
    } else if (!strcmp(name,"previous")) {
        if(store->mode && news->count) {
            if(page>0) page--; else { news_index=(news_index+news->count-1)%news->count; page=page_count()-1; }
        } else if (!store->mode && store->count) {
            if(page>0) page--; else { note_index=(note_index+store->count-1)%store->count; page=page_count()-1; }
        }
    } else if (!strcmp(name,"delete") && !store->mode && store->count) {
        char id[37]; copy_text(id,store->notes[note_index].id,sizeof(id));
        remove_note(note_index); persist(); event("note_deleted",id,-1); show_toast("便签已删除");
    } else if (!strcmp(name,"pin") && !store->mode && store->count) {
        note_t *note=&store->notes[note_index]; note->pinned=!note->pinned;
        char id[37]; copy_text(id,note->id,sizeof(id)); bool pinned=note->pinned; sort_note_pins();
        persist(); event("note_pinned",id,pinned); show_toast(pinned?"已置顶":"已取消置顶");
    } else if (!strcmp(name,"delete") && store->mode && news->count) {
        int64_t id=news->items[news_index].article.id;
        if(!news_deleted(id)) {
            news->deleted[news->deleted_next++%NEWS_DELETED_MAX]=id; news->deleted_next%=NEWS_DELETED_MAX;
            if(news->deleted_count<NEWS_DELETED_MAX) news->deleted_count++;
        }
        remove_news(news_index); persist(); news_event("news_deleted",id,-1); show_toast("新闻已删除");
    } else if (!strcmp(name,"pin") && store->mode && news->count) {
        news_item_t *item=&news->items[news_index]; item->pinned=!item->pinned;
        int pinned=item->pinned; int64_t id=item->article.id; sort_news_pins();
        persist(); news_event("news_pinned",id,pinned); show_toast(pinned?"新闻已置顶":"已取消置顶");
    } else if (!strcmp(name,"open") && !store->mode && store->count) {
        if(connection==BLE_HS_CONN_HANDLE_NONE) show_toast("请先连接 PC 再显示便签");
        else { event("open_note",store->notes[note_index].id,-1); show_toast("已请求 PC 显示便签"); }
    } else if (!strcmp(name,"open") && store->mode && news->count) {
        if(connection==BLE_HS_CONN_HANDLE_NONE) show_toast("请先连接 PC 再打开网页");
        else {
            cJSON *obj=cJSON_CreateObject(); cJSON_AddStringToObject(obj,"event","open_news");
            cJSON_AddNumberToObject(obj,"id",news->items[news_index].article.id); queue_json(obj); show_toast("已请求 PC 打开网页");
        }
    }
    dirty=true;
}
static void draw(void) {
    u8g2_t *u=u8g2_st7305_get_u8g2(&lcd); char buf[128];
    u8g2_ClearBuffer(u); u8g2_SetDrawColor(u,1);
    u8g2_SetFont(u,BODY_FONT); u8g2_SetFontPosTop(u); u8g2_SetFontMode(u,1);
    u8g2_DrawUTF8(u,13,7,reminder_until?"提醒":(store->mode?"新闻":"便签"));
    char header_time[32]; device_clock_format(header_time,sizeof(header_time),false);
    u8g2_DrawUTF8(u,92,7,header_time);
    /* Bluetooth rune, crossed out when disconnected. No connection-status text. */
    u8g2_DrawLine(u,339,5,339,25); u8g2_DrawLine(u,339,5,347,12);
    u8g2_DrawLine(u,347,12,332,23); u8g2_DrawLine(u,332,7,347,18); u8g2_DrawLine(u,347,18,339,25);
    if(connection==BLE_HS_CONN_HANDLE_NONE) u8g2_DrawLine(u,330,4,350,26);
    battery_state_t charge=battery_state();
    u8g2_DrawFrame(u,365,9,24,13); u8g2_DrawBox(u,389,13,2,5);
    if(charge.valid && charge.percent>0) u8g2_DrawBox(u,367,11,(20*charge.percent+99)/100,9);
    u8g2_DrawHLine(u,12,31,376);
    if(reminder_until) {
        wrapped_text(reminder_text,0,true);
        if(reminder_count>1) snprintf(buf,sizeof(buf),"合并提醒  %d / %d",reminder_current+1,reminder_count);
        else snprintf(buf,sizeof(buf),"便签提醒时间已到");
    } else if (!store->mode) {
        if(store->count) {
            note_t *note=&store->notes[note_index]; int pages=wrapped_text(note->text,page,true);
            snprintf(buf,sizeof(buf),"%02d / %02d    %s    内容页 %d / %d",note_index+1,store->count,note->pinned?"已置顶":"普通便签",page+1,pages);
        } else {
            wrapped_text("还没有便签\n\n在 PC 选中文字，按 Ctrl+Alt+J\n或按 Ctrl+Alt+空格 输入。",0,true);
            snprintf(buf,sizeof(buf),"0 / %u    等待来自 PC 的便签",note_limit);
        }
    } else {
        if(news->count) {
            news_item_t *item=&news->items[news_index]; news_t *article=&item->article;
            wrapped_text(*item->summary?item->summary:article->title,page,true);
            bool gdelt=article->id>=GDELT_ID_BASE;
            u8g2_SetFont(u,SMALL_FONT);
            snprintf(buf,sizeof(buf),"%s    %.68s",*item->summary?"中文简讯":"新闻标题",article->domain);
            u8g2_DrawUTF8(u,16,display.hints?240:282,buf);
            snprintf(buf,sizeof(buf),"%02d / %02d  [%s]  %s  %d/%d",news_index+1,news->count,gdelt?"GDELT":"HN",item->pinned?"置顶":"",page+1,page_count());
        } else { wrapped_text("等待 HN / GDELT 新闻\n\n请启动 PC 后台程序。",0,true); snprintf(buf,sizeof(buf),"暂无新闻    双击 KEY 切换便签"); }
    }
    u8g2_SetFont(u,SMALL_FONT); u8g2_DrawUTF8(u,15,35,buf);
    if(display.hints) {
        u8g2_DrawHLine(u,12,262,376);
        if(reminder_until) u8g2_DrawUTF8(u,15,269,"按任意键返回");
        else if(now_ms()<toast_until) u8g2_DrawUTF8(u,15,269,toast);
        else u8g2_DrawUTF8(u,15,269,"BOOT 下一条  KEY 上一条  双击 KEY 切换");
        u8g2_SetFont(u,u8g2_font_5x7_tf);
        if(!reminder_until) u8g2_DrawStr(u,15,289,"2x BOOT: open on PC | Hold BOOT: delete | Hold KEY: pin");
    } else if(now_ms()<toast_until) {
        u8g2_SetDrawColor(u,0); u8g2_DrawBox(u,0,266,WIDTH,20); u8g2_SetDrawColor(u,1);
        u8g2_DrawUTF8(u,15,269,toast);
    }
    /* ST7305 is initialized with INVON (0x21): a set framebuffer bit is white. */
    if((!display.dark)!=reminder_flash) {
        size_t bytes=u8g2_GetBufferTileWidth(u)*8*u8g2_GetBufferTileHeight(u);
        uint8_t *buffer=u8g2_GetBufferPtr(u); for(size_t i=0;i<bytes;i++) buffer[i]^=0xff;
    }
    u8g2_SendBuffer(u); dirty=false;
}

static void button_action(button_action_t value) {
    static const char *names[]={"next","previous","open","toggle","delete","pin"}; action(names[value]);
}
static void keys_tick(void) {
    static key_state_t keys[2]={{.pin=0,.stable=1},{.pin=18,.stable=1}};
    int64_t ms=now_ms();
    for(int i=0;i<2;i++) {
        key_state_t *key=&keys[i]; int level=gpio_get_level(key->pin);
        if(level==key->stable) key->hits=0;
        else if(++key->hits>=3) {
            key->hits=0; key->stable=level;
        }
        button_tick(&buttons,i,key->stable==0,ms,button_action);
    }
}

static int access_rx(uint16_t conn, uint16_t attr, struct ble_gatt_access_ctxt *ctxt, void *arg) {
    if(ctxt->op!=BLE_GATT_ACCESS_OP_WRITE_CHR) return BLE_ATT_ERR_WRITE_NOT_PERMITTED;
    uint16_t size=OS_MBUF_PKTLEN(ctxt->om); char chunk[512]; uint16_t copied;
    if(size>sizeof(chunk)) return BLE_ATT_ERR_INVALID_ATTR_VALUE_LEN;
    if(ble_hs_mbuf_to_flat(ctxt->om,chunk,sizeof(chunk),&copied)!=0) return BLE_ATT_ERR_UNLIKELY;
    if(now_ms()-rx_last>5000) rx_length=0;
    rx_last=now_ms();
    for(int i=0;i<copied;i++) {
        if(chunk[i]=='\n') {
            rx_buffer[rx_length]=0;
            packet_t packet={.text=strdup(rx_buffer),.generation=generation,.conn=conn}; rx_length=0;
            if(!packet.text) return BLE_ATT_ERR_INSUFFICIENT_RES;
            if(xQueueSend(rx_queue,&packet,0)!=pdTRUE) { free(packet.text); return BLE_ATT_ERR_INSUFFICIENT_RES; }
        } else {
            if(rx_length>=RX_MAX) { rx_length=0; return BLE_ATT_ERR_INVALID_ATTR_VALUE_LEN; }
            rx_buffer[rx_length++]=chunk[i];
        }
    }
    return 0;
}
static int access_tx(uint16_t conn,uint16_t attr,struct ble_gatt_access_ctxt *ctxt,void *arg) {
    return os_mbuf_append(ctxt->om,"DeskCompanion/v1",16)==0?0:BLE_ATT_ERR_INSUFFICIENT_RES;
}
static const struct ble_gatt_svc_def services[]={
    {.type=BLE_GATT_SVC_TYPE_PRIMARY,.uuid=&svc_uuid.u,.characteristics=(struct ble_gatt_chr_def[]){
        {.uuid=&rx_uuid.u,.access_cb=access_rx,.flags=BLE_GATT_CHR_F_WRITE},
        {.uuid=&tx_uuid.u,.access_cb=access_tx,.val_handle=&tx_handle,.flags=BLE_GATT_CHR_F_NOTIFY|BLE_GATT_CHR_F_READ},
        {0}}}, {0}};
static int gap_event(struct ble_gap_event *event,void *arg) {
    switch(event->type) {
    case BLE_GAP_EVENT_CONNECT:
        if(!event->connect.status) { connection=event->connect.conn_handle; generation++; subscribed=false; rx_length=0; dirty=true; }
        else { advertise(); }
        break;
    case BLE_GAP_EVENT_DISCONNECT:
        connection=BLE_HS_CONN_HANDLE_NONE; generation++; subscribed=false; rx_length=0; dirty=true; advertise(); break;
    case BLE_GAP_EVENT_SUBSCRIBE:
        if(event->subscribe.attr_handle==tx_handle) { subscribed=event->subscribe.cur_notify; }
        break;
    default: break;
    }
    return 0;
}
static void advertise(void) {
    struct ble_hs_adv_fields fields={0};
    fields.flags=BLE_HS_ADV_F_DISC_GEN|BLE_HS_ADV_F_BREDR_UNSUP;
    fields.name=(uint8_t*)DEVICE_NAME; fields.name_len=strlen(DEVICE_NAME); fields.name_is_complete=1;
    ble_gap_adv_set_fields(&fields);
    struct ble_gap_adv_params params={0}; params.conn_mode=BLE_GAP_CONN_MODE_UND; params.disc_mode=BLE_GAP_DISC_MODE_GEN;
    int rc=ble_gap_adv_start(BLE_OWN_ADDR_PUBLIC,NULL,BLE_HS_FOREVER,&params,gap_event,NULL);
    ESP_LOGI(TAG,"advertise rc=%d",rc);
}
static void synced(void) { ble_hs_util_ensure_addr(0); advertise(); }
static void host_task(void *arg) { nimble_port_run(); nimble_port_freertos_deinit(); }

void app_main(void) {
    store=calloc(1,sizeof(*store)); assert(store);
    news=calloc(1,sizeof(*news)); assert(news);
    schedule=calloc(1,sizeof(*schedule)); assert(schedule);
    esp_err_t err=nvs_flash_init();
    if(err==ESP_ERR_NVS_NO_FREE_PAGES || err==ESP_ERR_NVS_NEW_VERSION_FOUND) ESP_ERROR_CHECK(err); /* Preserve existing NVS. */
    ESP_ERROR_CHECK(err); ESP_ERROR_CHECK(nvs_flash_init_partition("storage"));
    ESP_ERROR_CHECK(nvs_open_from_partition("storage","companion",NVS_READWRITE,&nvs));
    size_t schedule_bytes=sizeof(*schedule);
    if(nvs_get_blob(nvs,"reminders_v1",schedule,&schedule_bytes)!=ESP_OK || schedule_bytes!=sizeof(*schedule) || schedule->magic!=SCHEDULE_MAGIC || schedule->count<0 || schedule->count>SCHEDULE_MAX || schedule->done_count<0 || schedule->done_count>SCHEDULE_DONE_MAX || schedule->done_next<0 || schedule->done_next>=SCHEDULE_DONE_MAX) {
        memset(schedule,0,sizeof(*schedule)); schedule->magic=SCHEDULE_MAGIC;
    }
    display_settings_t saved_display; size_t display_bytes=sizeof(saved_display);
    if(nvs_get_blob(nvs,"display_v1",&saved_display,&display_bytes)==ESP_OK && display_bytes==sizeof(saved_display) && display_settings_valid(&saved_display)) display=saved_display;
    if(nvs_get_u8(nvs,"note_limit",&note_limit)!=ESP_OK || note_limit<1 || note_limit>NOTE_MAX) note_limit=NOTE_MAX;
    size_t size=sizeof(*store); err=nvs_get_blob(nvs,"state",store,&size);
    if(err!=ESP_OK || size!=sizeof(*store) || store->magic!=STORE_MAGIC || store->version!=1 || store->count<0 || store->count>NOTE_MAX || store->news_count<0 || store->news_count>LEGACY_NEWS_MAX) {
        memset(store,0,sizeof(*store)); store->magic=STORE_MAGIC; store->version=1;
    }
    if(note_limit<store->count) note_limit=store->count;
    size=sizeof(*news); err=nvs_get_blob(nvs,"news_v2",news,&size);
    if(err!=ESP_OK || size!=sizeof(*news) || news->magic!=NEWS_MAGIC || news->version!=2 || news->count<0 || news->count>NEWS_MAX || news->limit<1 || news->limit>NEWS_MAX || news->count>news->limit || news->deleted_count<0 || news->deleted_count>NEWS_DELETED_MAX) {
        memset(news,0,sizeof(*news)); news->magic=NEWS_MAGIC; news->version=2; news->limit=20;
        news->count=store->news_count; news->updated=store->news_updated;
        for(int i=0;i<news->count;i++) news->items[i].article=store->news[i];
    }
    rx_queue=xQueueCreate(5,sizeof(packet_t)); tx_queue=xQueueCreate(8,sizeof(packet_t)); assert(rx_queue && tx_queue);
    u8g2_st7305_config_t config=u8g2_st7305_default_config();
    config.rotation=U8G2_R1; config.tile_buf_height=U8G2_ST7305_TILE_BUF_FULL;
    config.mosi_io=12; config.sclk_io=11; config.dc_io=5; config.cs_io=40; config.reset_io=41;
    ESP_ERROR_CHECK(u8g2_st7305_init(&lcd,&config));
    gpio_config_t pins={.pin_bit_mask=(1ULL<<0)|(1ULL<<18),.mode=GPIO_MODE_INPUT,.pull_up_en=GPIO_PULLUP_ENABLE};
    ESP_ERROR_CHECK(gpio_config(&pins));
    battery_init();
    device_clock_init();
    ESP_ERROR_CHECK(nimble_port_init()); ble_hs_cfg.sync_cb=synced;
    ble_svc_gap_init(); ble_svc_gatt_init(); ble_svc_gap_device_name_set(DEVICE_NAME);
    ESP_ERROR_CHECK(ble_gatts_count_cfg(services)); ESP_ERROR_CHECK(ble_gatts_add_svcs(services));
    xTaskCreate(tx_task,"companion_tx",6144,NULL,5,NULL); nimble_port_freertos_init(host_task);
    ESP_LOGI(TAG,"COMPANION_READY notes=%d news=%d",store->count,news->count);
    int64_t battery_due=now_ms()+5000;
    int64_t shown_minute=-1;
    for(;;) {
        packet_t packet;
        if(xQueueReceive(rx_queue,&packet,0)==pdTRUE) { process_request(&packet); free(packet.text); }
        keys_tick();
        schedules_tick();
        int64_t minute=device_clock_valid()?time(NULL)/60:0;
        if(minute!=shown_minute) { shown_minute=minute; dirty=true; }
        if(reminder_until) {
            int64_t elapsed=now_ms()-reminder_started;
            bool phase=reminder_flash_phase(elapsed);
            if(phase!=reminder_flash) { reminder_flash=phase; dirty=true; }
            int current=reminder_group_index(elapsed,reminder_count);
            if(reminder_count) {
                int index=find_note(reminder_ids[current]);
                if(index>=0 && (note_index!=index || store->mode!=0)) { note_index=index; store->mode=0; dirty=true; }
            }
            if(current!=reminder_current) {
                reminder_current=current;
                int index=find_note(reminder_ids[current]);
                if(index>=0) { note_index=index; copy_text(reminder_text,store->notes[index].text,sizeof(reminder_text)); }
                page=0; dirty=true;
            }
            if(now_ms()>=reminder_until) { reminder_until=0; reminder_flash=false; reminder_count=0; dirty=true; }
        }
        if(now_ms()>=battery_due) { battery_due=now_ms()+5000; if(battery_update()) dirty=true; }
        if(toast_until && now_ms()>=toast_until) { toast_until=0; dirty=true; }
        if(dirty) draw();
        vTaskDelay(pdMS_TO_TICKS(10));
    }
}
