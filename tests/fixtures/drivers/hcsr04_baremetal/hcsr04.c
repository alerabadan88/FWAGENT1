#include <avr/io.h>
#include <util/delay.h>
#include "hcsr04.h"
#include "config.h"

/* Bare-metal HC-SR04: 10us trigger pulse, then time the echo high phase. */
uint8_t hcsr04_measure_cm(uint16_t *out_cm)
{
    uint32_t ticks = 0;
    const uint32_t timeout = 60000;

    if (out_cm == 0) { return 1; }

    HC_SR04_TRIGGER_PORT &= (uint8_t)~(1 << HC_SR04_TRIGGER_BIT);
    _delay_us(2);
    HC_SR04_TRIGGER_PORT |= (uint8_t)(1 << HC_SR04_TRIGGER_BIT);
    _delay_us(10);
    HC_SR04_TRIGGER_PORT &= (uint8_t)~(1 << HC_SR04_TRIGGER_BIT);

    while (!(HC_SR04_ECHO_PIN & (1 << HC_SR04_ECHO_BIT))) {
        if (++ticks > timeout) { return 2; }
    }
    ticks = 0;
    while (HC_SR04_ECHO_PIN & (1 << HC_SR04_ECHO_BIT)) {
        _delay_us(1);
        if (++ticks > timeout) { return 2; }
    }

    *out_cm = (uint16_t)(ticks / 58U);
    return 0;
}
