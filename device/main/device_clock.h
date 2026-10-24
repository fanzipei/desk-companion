/* Copyright 2026 fanzipei
 * SPDX-License-Identifier: Apache-2.0
 */
#pragma once
#include <stdbool.h>
#include <stdint.h>
void device_clock_init(void);
bool device_clock_set(int64_t unix_seconds);
bool device_clock_valid(void);
bool device_clock_rtc_available(void);
int64_t device_clock_last_sync(void);
const char *device_clock_source(void);
void device_clock_format(char *output,unsigned size,bool full);
