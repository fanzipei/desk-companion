/* Copyright 2026 fanzipei
 * SPDX-License-Identifier: Apache-2.0
 */
#pragma once
#include <stdbool.h>
#include <stdint.h>
/* Five gentle full-screen flashes in seven seconds, then a steady reminder. */
static inline bool reminder_flash_phase(int64_t elapsed) { return elapsed>=0 && elapsed<7000 && ((elapsed/700)%2)!=0; }
static inline int reminder_group_index(int64_t elapsed,int count) { return count>0?(elapsed/5000)%count:0; }
