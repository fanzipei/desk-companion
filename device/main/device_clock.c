/* Copyright 2026 fanzipei
 * SPDX-License-Identifier: Apache-2.0
 */
#include <stdio.h>
#include <stdlib.h>
#include <time.h>
#include <sys/time.h>
#include "driver/i2c_master.h"
#include "esp_log.h"
#include "device_clock.h"

/* Waveshare PCF85063A: shared I2C SDA13/SCL14, 7-bit address 0x51. RTC stores UTC. */
static i2c_master_dev_handle_t rtc;
static bool rtc_available;
static int64_t last_sync;
static bool restored_rtc;
static int from_bcd(uint8_t v) { return (v>>4)*10+(v&15); }
static uint8_t to_bcd(int v) { return (v/10)*16+v%10; }
static bool read_registers(uint8_t address,uint8_t *data,size_t count) {
    return rtc && i2c_master_transmit_receive(rtc,&address,1,data,count,100)==ESP_OK;
}
static bool write_registers(uint8_t address,const uint8_t *data,size_t count) {
    uint8_t packet[9]; if(count>8 || !rtc) return false;
    packet[0]=address; for(size_t i=0;i<count;i++) packet[i+1]=data[i];
    return i2c_master_transmit(rtc,packet,count+1,100)==ESP_OK;
}
bool device_clock_valid(void) { return time(NULL)>=1704067200 && time(NULL)<4102444800LL; }
bool device_clock_rtc_available(void) { return rtc_available; }
int64_t device_clock_last_sync(void) { return last_sync; }
const char *device_clock_source(void) { return last_sync?"pc":(restored_rtc?"rtc":"unsynchronized"); }
void device_clock_format(char *output,unsigned size,bool full) {
    if(!device_clock_valid()) { snprintf(output,size,"---- -- -- --:--"); return; }
    time_t now=time(NULL); struct tm local; localtime_r(&now,&local);
    strftime(output,size,full?"%Y-%m-%d %H:%M:%S":"%Y-%m-%d %H:%M",&local);
}
void device_clock_init(void) {
    setenv("TZ","CST-8",1); tzset();
    i2c_master_bus_handle_t bus;
    i2c_master_bus_config_t config={.i2c_port=I2C_NUM_0,.sda_io_num=13,.scl_io_num=14,
        .clk_source=I2C_CLK_SRC_DEFAULT,.glitch_ignore_cnt=7,.flags.enable_internal_pullup=true};
    if(i2c_new_master_bus(&config,&bus)!=ESP_OK) return;
    i2c_device_config_t device={.dev_addr_length=I2C_ADDR_BIT_LEN_7,.device_address=0x51,.scl_speed_hz=100000};
    if(i2c_master_bus_add_device(bus,&device,&rtc)!=ESP_OK) return;
    uint8_t control,data[7];
    rtc_available=read_registers(0,&control,1);
    if(!rtc_available || (control&0x22) || !read_registers(4,data,7) || (data[0]&0x80)) return;
    struct tm utc={.tm_sec=from_bcd(data[0]&0x7f),.tm_min=from_bcd(data[1]&0x7f),.tm_hour=from_bcd(data[2]&0x3f),
        .tm_mday=from_bcd(data[3]&0x3f),.tm_mon=from_bcd(data[5]&0x1f)-1,.tm_year=from_bcd(data[6])+100,.tm_isdst=0};
    if(utc.tm_sec>59 || utc.tm_min>59 || utc.tm_hour>23 || utc.tm_mday<1 || utc.tm_mday>31 || utc.tm_mon<0 || utc.tm_mon>11) return;
    /* mktime uses CST; add the local UTC offset because these fields are UTC. */
    int day=utc.tm_mday, month=utc.tm_mon, year=utc.tm_year;
    time_t value=mktime(&utc)+8*3600;
    if(utc.tm_mday!=day || utc.tm_mon!=month || utc.tm_year!=year) return;
    if(value>=1704067200 && value<4102444800LL) { struct timeval tv={.tv_sec=value}; restored_rtc=settimeofday(&tv,NULL)==0; }
}
bool device_clock_set(int64_t value) {
    if(value<1704067200 || value>=4102444800LL) return false;
    struct timeval tv={.tv_sec=value}; if(settimeofday(&tv,NULL)!=0) return false;
    last_sync=value;
    if(rtc_available) {
        time_t epoch=value; struct tm utc; gmtime_r(&epoch,&utc);
        uint8_t data[]={to_bcd(utc.tm_sec),to_bcd(utc.tm_min),to_bcd(utc.tm_hour),to_bcd(utc.tm_mday),
                        to_bcd(utc.tm_wday),to_bcd(utc.tm_mon+1),to_bcd(utc.tm_year-100)};
        uint8_t control=0;
        if(read_registers(0,&control,1)) {
            uint8_t stopped=control|0x20, running=control&~0x22;
            bool success=write_registers(0,&stopped,1) && write_registers(4,data,7);
            bool started=write_registers(0,&running,1);
            if(!success || !started) ESP_LOGW("clock","RTC write failed; system clock remains synchronized");
        }
    }
    return true;
}
