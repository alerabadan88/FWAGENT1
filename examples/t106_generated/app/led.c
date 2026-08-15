/* fw-automation-agent -- generated, not hand-written
 *
 * Blink timing is derived from now_ms rather than counters, so a missed
 * tick shifts phase instead of accumulating drift.
 */

#include "led.h"
#include "app_config.h"
#include "../port/hal.h"

#define LED_SLOW_PERIOD_MS  1000u
#define LED_FAST_PERIOD_MS   500u

static const hal_pin_t s_pin[LED_COUNT] = {
    LED_RED_OUT_PIN,
    LED_BLUE_OUT_PIN,
    LED_GREEN_OUT_PIN,
};

static const uint8_t s_active_high[LED_COUNT] = {
    LED_RED_ACTIVE_HIGH,
    LED_BLUE_ACTIVE_HIGH,
    LED_GREEN_ACTIVE_HIGH,
};

static led_pattern_t s_pattern[LED_COUNT];

void led_init(void)
{
    for (unsigned i = 0; i < (unsigned)LED_COUNT; i++) {
        hal_gpio_config_output(s_pin[i]);
        s_pattern[i] = LED_OFF;
    }
}

void led_set(led_id_t id, led_pattern_t pattern)
{
    if ((unsigned)id < (unsigned)LED_COUNT) {
        s_pattern[id] = pattern;
    }
}

void led_tick(uint32_t now_ms)
{
    for (unsigned i = 0; i < (unsigned)LED_COUNT; i++) {
        bool on;
        switch (s_pattern[i]) {
        case LED_STEADY: on = true;  break;
        case LED_SLOW:   on = ((now_ms / (LED_SLOW_PERIOD_MS / 2u)) & 1u) != 0u; break;
        case LED_FAST:   on = ((now_ms / (LED_FAST_PERIOD_MS / 2u)) & 1u) != 0u; break;
        case LED_OFF:
        default:         on = false; break;
        }
        /* Active level is applied here and nowhere else, so the rest of
         * the firmware reasons in terms of on and off. */
        hal_gpio_write(s_pin[i], s_active_high[i] ? on : !on);
    }
}
