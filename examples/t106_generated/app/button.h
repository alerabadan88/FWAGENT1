/* fw-automation-agent -- generated, not hand-written */
#ifndef BUTTON_H
#define BUTTON_H

#include <stdint.h>

typedef enum {
    BTN_NONE = 0,
    BTN_SHORT,
    BTN_LONG
} btn_event_t;

void        button_init(void);
btn_event_t button_tick(uint32_t now_ms);

#endif /* BUTTON_H */
