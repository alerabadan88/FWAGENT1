/* fw-automation-agent -- generated, not hand-written
 *
 * Debounce plus press classification. The long-press threshold is a
 * product decision, not a technical one; it is defined here so there is
 * exactly one place to change it.
 */

#include "button.h"
#include "app_config.h"
#include "../port/hal.h"

#define BTN_DEBOUNCE_MS    40u
#define BTN_LONG_MS      1200u

static uint8_t  s_stable;      /* last debounced level */
static uint8_t  s_candidate;
static uint32_t s_changed_ms;
static uint32_t s_pressed_ms;

void button_init(void)
{
    hal_gpio_config_input(HOME_KEY_IN_PIN, HOME_KEY_PULL);
    s_stable = 0u;
    s_candidate = 0u;
    s_changed_ms = 0u;
    s_pressed_ms = 0u;
}

btn_event_t button_tick(uint32_t now_ms)
{
    bool raw = hal_gpio_read(HOME_KEY_IN_PIN);
    uint8_t level = (HOME_KEY_ACTIVE_HIGH ? raw : !raw) ? 1u : 0u;

    if (level != s_candidate) {
        s_candidate = level;
        s_changed_ms = now_ms;
        return BTN_NONE;
    }
    if (level == s_stable || (now_ms - s_changed_ms) < BTN_DEBOUNCE_MS) {
        return BTN_NONE;
    }

    s_stable = level;
    if (level) {
        s_pressed_ms = now_ms;
        return BTN_NONE;   /* classified on release */
    }
    return ((now_ms - s_pressed_ms) >= BTN_LONG_MS) ? BTN_LONG : BTN_SHORT;
}
