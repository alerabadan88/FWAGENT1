# Provenance

Where every fact in this firmware came from, so the weak ones are visible.

## Answered by a human, and unverifiable by anything here

These drive the generated code. Nothing in this tool can check them, and
each one produces firmware that builds and runs if it is wrong.

### led red (`devices[0]`, gpio)
- `pins.out` = `GPIO_12`
- `active_level` = `active low`

### led blue (`devices[1]`, gpio)
- `pins.out` = `GPIO_13`
- `active_level` = `active low`

### led green (`devices[2]`, gpio)
- `pins.out` = `GPIO_14`
- `active_level` = `active low`

### home key (`devices[3]`, gpio)
- `pins.in` = `GPIO_5`
- `active_level` = `active low`
- `pull` = `pull-up`

### ag3335a (`devices[4]`, uart)
- `bus` = `UART1`
- `baud` = `9600`

### da267 (`devices[5]`, i2c)
- `pins.int` = `GPIO_7`
- `bus` = `I2C0`
- `address` = `0x26`

## Derived from a versioned artifact

- Nothing. No SDK was present when this was generated, so no symbol
  in the porting layer rests on an artifact.

## Found by looking, but not authoritative

- `application_space` = `TBD in the source document` — cited: T106_Proudct specification V0.2-20260508.xlsx (V0.2) | 硬件定义 r51 | retrieved 2026-08-15
- `cpu_clock_hz` = `500000000` — cited: T106_Proudct specification V0.2-20260508.xlsx (V0.2) | 硬件定义 r25 | retrieved 2026-08-15
- `memory` = `SIP 16MB Flash + 16MB RAM` — cited: T106_Proudct specification V0.2-20260508.xlsx (V0.2) | 硬件定义 r49 | retrieved 2026-08-15
- `network` = `LTE FDD B1/3/5/8, TDD B34/39/40/41, VoLTE` — cited: T106_Proudct specification V0.2-20260508.xlsx (V0.2) | 硬件定义 r30 | retrieved 2026-08-15
- `os` = `RTOS` — cited: T106_Proudct specification V0.2-20260508.xlsx (V0.2) | 硬件定义 r27 | retrieved 2026-08-15

## The porting layer

Each line below is an operation whose SDK mapping a human must settle.
A single named candidate means the SDK declares a function whose *name*
fits. It does not mean the arguments match.

- hal_init -> no candidate; needs an answer
- hal_delay_ms -> no candidate; needs an answer
- hal_uptime_ms -> no candidate; needs an answer
- hal_gpio_config_output -> no candidate; needs an answer
- hal_gpio_config_input -> no candidate; needs an answer
- hal_gpio_write -> no candidate; needs an answer
- hal_gpio_read -> no candidate; needs an answer
- hal_i2c_init -> no candidate; needs an answer
- hal_i2c_write -> no candidate; needs an answer
- hal_i2c_read -> no candidate; needs an answer
- hal_i2c_write_read -> no candidate; needs an answer
- hal_uart_init -> no candidate; needs an answer
- hal_uart_write -> no candidate; needs an answer
- hal_uart_read -> no candidate; needs an answer

## Not established

- This firmware has not been compiled.
- It has not been run.
- It has not been on hardware.
