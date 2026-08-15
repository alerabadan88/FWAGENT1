/* fw-automation-agent -- generated, not hand-written
 *
 * Porting layer for UWS6121E.
 * This is the only file in the project that depends on the vendor SDK.
 *
 * No SDK was available when this was generated, so every function
 * below is a stub. Filling them in is the whole porting job: there
 * is no logic here, only calls into the vendor's API.
 */

#include "hal.h"

/* TODO(port): include the SDK headers this part needs. */

/* This file is incomplete by construction. Building it as-is is almost
 * certainly a mistake, so it refuses. Define HAL_PORT_INCOMPLETE_OK to
 * compile the application logic before the port is written -- useful for
 * checking the app on a host, never for producing a flashable image. */
#if !defined(HAL_PORT_INCOMPLETE_OK)
#  error "port/hal_uws6121e.c is not finished: the operations below need SDK calls."
#endif

/* Bring up clocks and anything the SDK requires before use. */
void hal_init(void)
{
    /* No catalogued SDK function matched this operation.
     * Question: which SDK call performs "Bring up clocks and anything the SDK requires before use." */
    return;
}

/* Block for a number of milliseconds. */
void hal_delay_ms(uint32_t ms)
{
    /* No catalogued SDK function matched this operation.
     * Question: which SDK call performs "Block for a number of milliseconds." */
    (void)ms;
    return;
}

/* Milliseconds since boot, free-running. Used for all scheduling. */
uint32_t hal_uptime_ms(void)
{
    /* No catalogued SDK function matched this operation.
     * Question: which SDK call performs "Milliseconds since boot, free-running. Used for all scheduling." */
    return 0;
}

/* Make a pin a push-pull output. */
void hal_gpio_config_output(hal_pin_t pin)
{
    /* No catalogued SDK function matched this operation.
     * Question: which SDK call performs "Make a pin a push-pull output." */
    (void)pin;
    return;
}

/* Make a pin an input with the given pull resistor. */
void hal_gpio_config_input(hal_pin_t pin, hal_pull_t pull)
{
    /* No catalogued SDK function matched this operation.
     * Question: which SDK call performs "Make a pin an input with the given pull resistor." */
    (void)pin;
    (void)pull;
    return;
}

/* Drive an output pin high or low. */
void hal_gpio_write(hal_pin_t pin, bool level)
{
    /* No catalogued SDK function matched this operation.
     * Question: which SDK call performs "Drive an output pin high or low." */
    (void)pin;
    (void)level;
    return;
}

/* Sample an input pin. */
bool hal_gpio_read(hal_pin_t pin)
{
    /* No catalogued SDK function matched this operation.
     * Question: which SDK call performs "Sample an input pin." */
    (void)pin;
    return false;
}

/* Bring up an I2C controller at the given bit rate. */
int hal_i2c_init(uint8_t bus, uint32_t hz)
{
    /* No catalogued SDK function matched this operation.
     * Question: which SDK call performs "Bring up an I2C controller at the given bit rate." */
    (void)bus;
    (void)hz;
    return 0;
}

/* Write bytes to an I2C slave. */
int hal_i2c_write(uint8_t bus, uint8_t addr, const uint8_t *data, size_t len)
{
    /* No catalogued SDK function matched this operation.
     * Question: which SDK call performs "Write bytes to an I2C slave." */
    (void)bus;
    (void)addr;
    (void)data;
    (void)len;
    return 0;
}

/* Read bytes from an I2C slave. */
int hal_i2c_read(uint8_t bus, uint8_t addr, uint8_t *data, size_t len)
{
    /* No catalogued SDK function matched this operation.
     * Question: which SDK call performs "Read bytes from an I2C slave." */
    (void)bus;
    (void)addr;
    (void)data;
    (void)len;
    return 0;
}

/* Register read: write the register index, repeated start, read back. */
int hal_i2c_write_read(uint8_t bus, uint8_t addr, const uint8_t *tx, size_t tx_len, uint8_t *rx, size_t rx_len)
{
    /* No catalogued SDK function matched this operation.
     * Question: which SDK call performs "Register read: write the register index, repeated start, read back." */
    (void)bus;
    (void)addr;
    (void)tx;
    (void)tx_len;
    (void)rx;
    (void)rx_len;
    return 0;
}

/* Bring up a UART at the given baud rate, 8N1. */
int hal_uart_init(uint8_t port, uint32_t baud)
{
    /* No catalogued SDK function matched this operation.
     * Question: which SDK call performs "Bring up a UART at the given baud rate, 8N1." */
    (void)port;
    (void)baud;
    return 0;
}

/* Send bytes. */
int hal_uart_write(uint8_t port, const uint8_t *data, size_t len)
{
    /* No catalogued SDK function matched this operation.
     * Question: which SDK call performs "Send bytes." */
    (void)port;
    (void)data;
    (void)len;
    return 0;
}

/* Receive up to len bytes, returning early on timeout. */
int hal_uart_read(uint8_t port, uint8_t *data, size_t len, uint32_t timeout_ms)
{
    /* No catalogued SDK function matched this operation.
     * Question: which SDK call performs "Receive up to len bytes, returning early on timeout." */
    (void)port;
    (void)data;
    (void)len;
    (void)timeout_ms;
    return 0;
}
