#include <assert.h>
#include <stdio.h>
#include "../device/main/display_logic.h"
#include "../device/main/battery_logic.h"
int main(void) {
    display_settings_t settings={.magic=DISPLAY_MAGIC,.hints=1,.font_size=16,.line_spacing=7};
    assert(display_settings_valid(&settings));
    int sizes[]={12,16,24,32};
    for(int i=0;i<4;i++) for(int gap=0;gap<=16;gap++) for(int mode=0;mode<2;mode++) {
        settings.font_size=sizes[i]; settings.line_spacing=gap; settings.hints=1;
        int shown=display_lines(&settings,mode);
        settings.hints=0; int hidden=display_lines(&settings,mode);
        assert(shown>0 && hidden>=shown);
        int bottom=mode?277:288;
        assert(53+(hidden-1)*(sizes[i]+gap)+sizes[i]<=bottom);
    }
    settings.font_size=20; assert(!display_settings_valid(&settings));
    int previous=-1;
    for(int voltage=2500;voltage<=4450;voltage++) {
        int level=battery_percent_from_mv(voltage);
        assert(level>=0 && level<=100 && level>=previous); previous=level;
    }
    assert(battery_percent_from_mv(3300)==0);
    assert(battery_percent_from_mv(3830)==50);
    assert(battery_percent_from_mv(4200)==100);
    assert(battery_percent_from_mv(3834)==51);
    puts("display/battery logic passed: all font/gap layouts, clipping bounds and 1% interpolation");
    return 0;
}
