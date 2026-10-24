# Third-party notices

Copyright (C) 2026 fanzipei. Original Desk Companion code is licensed under Apache-2.0; see LICENSE and NOTICE. This does **not** replace the licenses of the components below.

## Vendored firmware components

### U8g2

- Path: `device/components/u8g2/`
- Upstream: https://github.com/olikraus/u8g2
- Source supplied with the Waveshare example: https://github.com/waveshareteam/ESP32-S3-RLCD-4.2/tree/d8bf7813d3b53251577ff03c03596ad6ad3acae0/examples/esp-idf/11_U8G2_Test/components/u8g2
- Core license: BSD-2-Clause; original copyright and complete terms remain in `device/components/u8g2/LICENSE` and source headers.
- Local changes: ESP-IDF CMake integration selects generic rendering sources, defines `U8X8_WITH_USER_PTR`, and excludes unused display drivers. `u8g2_fonts.c` is reduced to the three fonts actually used. Their C initializers and glyph bytes are unchanged; unused font definitions are omitted. The upstream README is retained.

### Fonts (separate from the U8g2 code license)

- `u8g2_font_wqy12_t_gb2312b` and `u8g2_font_wqy16_t_gb2312b`: WenQuanYi Bitmap Song. Copyright (C) 2004–2010, WenQuanYi Project Board of Trustees and Qianqian Fang. GPL v2 with font embedding exception. See `LICENSES/GPL-2.0-only.txt` and `LICENSES/WenQuanYi-font-exception.txt`.
- Upstream font provenance: https://github.com/olikraus/u8g2/wiki/fntgrpwqy and https://github.com/larryli/u8g2_wqy . The font sources and transformation tools are available there; the original font license takes precedence over licenses of wrapper/conversion code.
- `u8g2_font_5x7_tf`: Misc Fixed 5×7, identified by its upstream header as a public domain font. The original notice remains beside its initializer.
- Font embedding exceptions have their own scope. A top-level Apache-2.0 notice does not relicense these GPLv2 fonts or make all combined firmware permissively licensed. Review the retained font terms for any distribution of modified fonts or combined firmware.

### Waveshare ST7305 driver

- Path: `device/components/u8g2_st7305/`
- Upstream: https://github.com/waveshareteam/ESP32-S3-RLCD-4.2/tree/d8bf7813d3b53251577ff03c03596ad6ad3acae0/examples/esp-idf/11_U8G2_Test/components/u8g2_st7305
- License: Apache-2.0, copied verbatim from the upstream repository LICENSE into this component and `LICENSES/Apache-2.0.txt`.
- The C source and header match that upstream revision. Board pins and display polarity are configured by the project application.

## Dependencies installed separately

The following are not copied into this repository. Their installed distributions retain their own license files:

| Dependency | Upstream | License |
| --- | --- | --- |
| bleak | https://github.com/hbldh/bleak | MIT |
| pystray | https://github.com/moses-palmer/pystray | LGPL-3.0 |
| Pillow | https://github.com/python-pillow/Pillow | Installed Pillow 13: MIT-CMU; consult each installed version's LICENSE and included component notices |
| Python / Tk | https://www.python.org/ | Python and Tcl/Tk licenses |
| ESP-IDF | https://github.com/espressif/esp-idf | Apache-2.0 for ESP-IDF code; bundled components have their own licenses |
| esptool | https://github.com/espressif/esptool | GPL-2.0-or-later |

When packaging or distributing installed dependencies or firmware binaries, also include the applicable upstream notices and satisfy their source/redistribution requirements. Hardware design files, Waveshare trademarks, news sources, and user content are not relicensed by this repository.
