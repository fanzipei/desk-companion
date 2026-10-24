idf_component_register(SRCS "main.c" "battery.c" "device_clock.c" INCLUDE_DIRS "."
    REQUIRES u8g2_st7305 u8g2 esp_timer esp_adc driver bt nvs_flash esp_event json)
