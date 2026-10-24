#ifndef COMPANION_BUTTON_LOGIC_H
#define COMPANION_BUTTON_LOGIC_H
#include <stdbool.h>
#include <stdint.h>
#include <string.h>
#define BUTTON_DOUBLE_MS 350
#define BUTTON_LONG_MS 1000
typedef enum { BUTTON_NEXT, BUTTON_PREVIOUS, BUTTON_OPEN, BUTTON_TOGGLE, BUTTON_DELETE, BUTTON_PIN } button_action_t;
typedef struct { bool down, long_fired; int64_t pressed_at, pending_at; } button_state_t;
typedef struct { button_state_t key[2]; } buttons_t;
typedef void (*button_emit_t)(button_action_t action);
static inline void buttons_reset(buttons_t *buttons) { memset(buttons,0,sizeof(*buttons)); }
/* Called with debounced levels. Delaying singles prevents double-click side effects. */
static inline void button_tick(buttons_t *buttons,int index,bool down,int64_t ms,button_emit_t emit) {
    button_state_t *key=&buttons->key[index];
    if(down!=key->down) {
        key->down=down;
        if(down) {
            if(key->pending_at && ms-key->pending_at>BUTTON_DOUBLE_MS) {
                key->pending_at=0; emit(index?BUTTON_PREVIOUS:BUTTON_NEXT);
            }
            key->pressed_at=ms; key->long_fired=false;
        } else if(!key->long_fired) {
            if(key->pending_at && ms-key->pending_at<=BUTTON_DOUBLE_MS) {
                key->pending_at=0; emit(index?BUTTON_TOGGLE:BUTTON_OPEN);
            } else {
                if(key->pending_at) emit(index?BUTTON_PREVIOUS:BUTTON_NEXT);
                key->pending_at=ms;
            }
        }
    }
    if(key->down && !key->long_fired && ms-key->pressed_at>=BUTTON_LONG_MS) {
        key->long_fired=true; key->pending_at=0; emit(index?BUTTON_PIN:BUTTON_DELETE);
    }
    if(!key->down && key->pending_at && ms-key->pending_at>BUTTON_DOUBLE_MS) {
        key->pending_at=0; emit(index?BUTTON_PREVIOUS:BUTTON_NEXT);
    }
}
#endif
