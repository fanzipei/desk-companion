/* Copyright 2026 fanzipei
 * SPDX-License-Identifier: Apache-2.0
 */
#ifndef COMPANION_BATTERY_LOGIC_H
#define COMPANION_BATTERY_LOGIC_H
/* A voltage-based estimate for a single-cell 4.2 V Li-ion battery, not a fuel gauge. */
static inline int battery_percent_from_mv(int mv) {
    static const int voltages[]={3300,3500,3600,3700,3750,3790,3830,3870,3920,3980,4060,4200};
    static const int levels[]={0,5,10,20,30,40,50,60,70,80,90,100};
    if(mv<=voltages[0]) return 0;
    for(int i=1;i<12;i++) if(mv<voltages[i]) {
        int span=voltages[i]-voltages[i-1];
        return levels[i-1]+((mv-voltages[i-1])*(levels[i]-levels[i-1])+span/2)/span;
    }
    return 100;
}
#endif
