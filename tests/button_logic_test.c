#include <assert.h>
#include <stdio.h>
#include "../device/main/button_logic.h"
static int count; static button_action_t result[20];
static void emit(button_action_t value) { result[count++]=value; }
static void reset(buttons_t *buttons) { buttons_reset(buttons); count=0; }
static void tap(buttons_t *buttons,int index,int64_t t) { button_tick(buttons,index,true,t,emit); button_tick(buttons,index,false,t+50,emit); }
int main(void) {
    buttons_t buttons; reset(&buttons);
    tap(&buttons,0,100); assert(count==0); button_tick(&buttons,0,false,501,emit);
    assert(count==1 && result[0]==BUTTON_NEXT);
    reset(&buttons); tap(&buttons,1,100); button_tick(&buttons,1,false,501,emit);
    assert(count==1 && result[0]==BUTTON_PREVIOUS);
    reset(&buttons); tap(&buttons,0,100); tap(&buttons,0,300); button_tick(&buttons,0,false,800,emit);
    assert(count==1 && result[0]==BUTTON_OPEN);
    reset(&buttons); tap(&buttons,1,100); tap(&buttons,1,300); button_tick(&buttons,1,false,800,emit);
    assert(count==1 && result[0]==BUTTON_TOGGLE);
    for(int index=0;index<2;index++) {
        reset(&buttons); button_tick(&buttons,index,true,100,emit); button_tick(&buttons,index,true,1100,emit);
        button_tick(&buttons,index,true,2100,emit); button_tick(&buttons,index,false,2200,emit); button_tick(&buttons,index,false,2800,emit);
        assert(count==1 && result[0]==(index?BUTTON_PIN:BUTTON_DELETE));
        reset(&buttons); tap(&buttons,index,100); button_tick(&buttons,index,true,300,emit);
        button_tick(&buttons,index,true,1300,emit); button_tick(&buttons,index,false,1400,emit);
        assert(count==1 && result[0]==(index?BUTTON_PIN:BUTTON_DELETE));
    }
    reset(&buttons); tap(&buttons,0,100); tap(&buttons,0,700); button_tick(&buttons,0,false,1200,emit);
    assert(count==2 && result[0]==BUTTON_NEXT && result[1]==BUTTON_NEXT);
    puts("button logic passed: directions, doubles, long presses, cancellation and no repeats");
    return 0;
}
