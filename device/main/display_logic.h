/* Copyright 2026 fanzipei
 * SPDX-License-Identifier: Apache-2.0
 */
#ifndef COMPANION_DISPLAY_LOGIC_H
#define COMPANION_DISPLAY_LOGIC_H
#include <stdbool.h>
#include <stdint.h>
#define DISPLAY_MAGIC 0x44535031
typedef struct { uint32_t magic; uint8_t dark, hints, font_size, line_spacing; } display_settings_t;
static inline bool display_size_valid(int size) { return size==12 || size==16 || size==24 || size==32; }
static inline bool display_settings_valid(const display_settings_t *value) {
    return value->magic==DISPLAY_MAGIC && value->dark<=1 && value->hints<=1 &&
           display_size_valid(value->font_size) && value->line_spacing<=16;
}
static inline int display_lines(const display_settings_t *value,bool news_mode) {
    int bottom=value->hints?(news_mode?235:254):(news_mode?277:288);
    return 1+(bottom-53-value->font_size)/(value->font_size+value->line_spacing);
}
#endif
