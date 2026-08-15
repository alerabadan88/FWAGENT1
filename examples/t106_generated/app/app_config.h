/* fw-automation-agent -- generated, not hand-written
 *
 * Board: T106 Pet Locator
 * MCU:   UWS6121EG  (family UWS6121E)
 *
 * Every value in this file was answered by a person who has seen the
 * schematic. None of it is derived from an artifact, so none of it is
 * checkable here -- if one is wrong, the firmware still builds and runs.
 */
#ifndef APP_CONFIG_H
#define APP_CONFIG_H

#include "../port/hal.h"
/* Pin names in this part's notation. Placeholders until the SDK is
 * present; see the header itself. */
#include "../port/board_pins.h"

#define BOARD_NAME       "T106 Pet Locator"
#define APP_LOOP_MS      1000u

/* led red -- power / battery  [answered by a human; nothing here can check it] */
#define LED_RED_OUT_PIN    ((hal_pin_t)GPIO_12)
#define LED_RED_ACTIVE_HIGH  0

/* led blue -- gps fix  [answered by a human; nothing here can check it] */
#define LED_BLUE_OUT_PIN    ((hal_pin_t)GPIO_13)
#define LED_BLUE_ACTIVE_HIGH  0

/* led green -- network / server  [answered by a human; nothing here can check it] */
#define LED_GREEN_OUT_PIN    ((hal_pin_t)GPIO_14)
#define LED_GREEN_ACTIVE_HIGH  0

/* home key  [answered by a human; nothing here can check it] */
#define HOME_KEY_IN_PIN    ((hal_pin_t)GPIO_5)
#define HOME_KEY_ACTIVE_HIGH  0
#define HOME_KEY_PULL         HAL_PULL_UP

/* ag3335a -- positioning  [answered by a human; nothing here can check it] */
#define AG3335A_BUS          1u  /* UART1 */
#define AG3335A_BAUD         9600u

/* da267 -- motion wake  [answered by a human; nothing here can check it] */
#define DA267_INT_PIN    ((hal_pin_t)GPIO_7)
#define DA267_BUS          0u  /* I2C0 */
#define DA267_ADDR         0x26

#endif /* APP_CONFIG_H */
