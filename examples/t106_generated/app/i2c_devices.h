/* fw-automation-agent -- generated, not hand-written */
#ifndef I2C_DEVICES_H
#define I2C_DEVICES_H

#include <stdbool.h>
#include <stdint.h>

void i2c_devices_init(void);
void i2c_devices_tick(uint32_t now_ms);

/* da267 -- motion wake */
bool da267_read_reg(uint8_t reg, uint8_t *value);
bool da267_write_reg(uint8_t reg, uint8_t value);

#endif /* I2C_DEVICES_H */
