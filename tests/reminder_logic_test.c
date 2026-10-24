#include <assert.h>
#include <stdio.h>
#include "../device/main/reminder_logic.h"
int main(void) {
    assert(!reminder_flash_phase(0)); assert(reminder_flash_phase(700));
    assert(!reminder_flash_phase(1400)); assert(reminder_flash_phase(6300));
    assert(!reminder_flash_phase(7000)); assert(!reminder_flash_phase(60000));
    for(int count=0;count<=20;count++) for(int ms=0;ms<=100000;ms+=100) {
        int selected=reminder_group_index(ms,count);
        assert(selected>=0 && (count==0?selected==0:selected<count));
    }
    assert(reminder_group_index(0,2)==0); assert(reminder_group_index(5000,2)==1);
    assert(reminder_group_index(10000,2)==0);
    puts("reminder logic passed: bounded gentle flashing and complete group rotation");
    return 0;
}
