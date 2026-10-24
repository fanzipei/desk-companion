/* Copyright 2026 fanzipei
 * SPDX-License-Identifier: Apache-2.0
 */
#pragma once
#include <stdbool.h>
typedef struct { bool valid, calibrated; int millivolts, percent; } battery_state_t;
void battery_init(void);
bool battery_update(void);
battery_state_t battery_state(void);
