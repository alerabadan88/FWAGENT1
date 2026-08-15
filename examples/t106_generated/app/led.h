/* fw-automation-agent -- generated, not hand-written */
#ifndef LED_H
#define LED_H

#include <stdint.h>

typedef enum {
    LED_LED_RED,  /* power / battery */
    LED_LED_BLUE,  /* gps fix */
    LED_LED_GREEN,  /* network / server */
    LED_COUNT
} led_id_t;

/* The four states a single indicator can be in. Anything the product
 * specification describes as steady / slow blink / fast blink / off maps
 * onto these without further interpretation. */
typedef enum {
    LED_OFF = 0,
    LED_STEADY,
    LED_SLOW,
    LED_FAST
} led_pattern_t;

void led_init(void);
void led_set(led_id_t id, led_pattern_t pattern);
void led_tick(uint32_t now_ms);

#endif /* LED_H */
