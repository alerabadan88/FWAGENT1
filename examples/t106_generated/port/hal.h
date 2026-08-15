/* fw-automation-agent -- generated, not hand-written */
#ifndef HAL_H
#define HAL_H

#include <stdbool.h>
#include <stddef.h>
#include <stdint.h>

/* A pin, as this SoC's SDK identifies one. Widened to 32 bits so a
 * port that encodes port+pin in one value still fits. */
typedef uint32_t hal_pin_t;

typedef enum {
    HAL_PULL_NONE = 0,
    HAL_PULL_UP,
    HAL_PULL_DOWN
} hal_pull_t;

/* Bring up clocks and anything the SDK requires before use. */
void hal_init(void);

/* Block for a number of milliseconds. */
void hal_delay_ms(uint32_t ms);

/* Milliseconds since boot, free-running. Used for all scheduling. */
uint32_t hal_uptime_ms(void);

/* Make a pin a push-pull output. */
void hal_gpio_config_output(hal_pin_t pin);

/* Make a pin an input with the given pull resistor. */
void hal_gpio_config_input(hal_pin_t pin, hal_pull_t pull);

/* Drive an output pin high or low. */
void hal_gpio_write(hal_pin_t pin, bool level);

/* Sample an input pin. */
bool hal_gpio_read(hal_pin_t pin);

/* Bring up an I2C controller at the given bit rate. */
int hal_i2c_init(uint8_t bus, uint32_t hz);

/* Write bytes to an I2C slave. */
int hal_i2c_write(uint8_t bus, uint8_t addr, const uint8_t *data, size_t len);

/* Read bytes from an I2C slave. */
int hal_i2c_read(uint8_t bus, uint8_t addr, uint8_t *data, size_t len);

/* Register read: write the register index, repeated start, read back. */
int hal_i2c_write_read(uint8_t bus, uint8_t addr, const uint8_t *tx, size_t tx_len, uint8_t *rx, size_t rx_len);

/* Bring up a UART at the given baud rate, 8N1. */
int hal_uart_init(uint8_t port, uint32_t baud);

/* Send bytes. */
int hal_uart_write(uint8_t port, const uint8_t *data, size_t len);

/* Receive up to len bytes, returning early on timeout. */
int hal_uart_read(uint8_t port, uint8_t *data, size_t len, uint32_t timeout_ms);

#endif /* HAL_H */
