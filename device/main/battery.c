/* Copyright 2026 fanzipei
 * SPDX-License-Identifier: Apache-2.0
 */
#include <stdlib.h>
#include "battery.h"
#include "battery_logic.h"
#include "esp_adc/adc_oneshot.h"
#include "esp_adc/adc_cali_scheme.h"
#include "esp_log.h"

/* Waveshare BAT_ADC: GPIO4 / ADC1_CH3; 200 kOhm + 100 kOhm divider => VBAT = ADC * 3. */
static adc_oneshot_unit_handle_t adc;
static adc_cali_handle_t calibration;
static battery_state_t state={.percent=-1};
static int compare_int(const void *a,const void *b) { return *(const int*)a-*(const int*)b; }
void battery_init(void) {
    adc_oneshot_unit_init_cfg_t unit={.unit_id=ADC_UNIT_1};
    adc_oneshot_chan_cfg_t channel={.bitwidth=ADC_BITWIDTH_DEFAULT,.atten=ADC_ATTEN_DB_12};
    adc_cali_curve_fitting_config_t curve={.unit_id=ADC_UNIT_1,.chan=ADC_CHANNEL_3,
                                         .atten=ADC_ATTEN_DB_12,.bitwidth=ADC_BITWIDTH_DEFAULT};
    esp_err_t err=adc_oneshot_new_unit(&unit,&adc);
    if(err==ESP_OK) err=adc_oneshot_config_channel(adc,ADC_CHANNEL_3,&channel);
    if(err==ESP_OK) err=adc_cali_create_scheme_curve_fitting(&curve,&calibration);
    if(err!=ESP_OK) { ESP_LOGW("battery","ADC unavailable: %s",esp_err_to_name(err)); return; }
    state.calibrated=true; battery_update();
}
bool battery_update(void) {
    if(!adc || !calibration) return false;
    int samples[16],count=0;
    for(int i=0;i<16;i++) {
        int raw,mv;
        if(adc_oneshot_read(adc,ADC_CHANNEL_3,&raw)==ESP_OK && adc_cali_raw_to_voltage(calibration,raw,&mv)==ESP_OK) samples[count++]=mv;
    }
    bool previous=state.valid; int percent=state.percent;
    if(count<8) { state.valid=false; state.percent=-1; return previous; }
    qsort(samples,count,sizeof(int),compare_int);
    int sum=0,trim=count/4;
    for(int i=trim;i<count-trim;i++) sum+=samples[i];
    int mv=(sum*3+(count-2*trim)/2)/(count-2*trim);
    if(mv<2500 || mv>4450) { state.valid=false; state.millivolts=mv; state.percent=-1; return previous; }
    state.millivolts=state.valid?(state.millivolts*3+mv+2)/4:mv;
    state.percent=battery_percent_from_mv(state.millivolts); state.valid=true;
    return previous!=state.valid || percent!=state.percent;
}
battery_state_t battery_state(void) { return state; }
