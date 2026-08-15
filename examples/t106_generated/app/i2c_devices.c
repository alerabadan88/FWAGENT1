/* fw-automation-agent -- generated, not hand-written
 *
 * Register access for the I2C parts on this board. The address and bus
 * for each come from the interview and are defined in app_config.h.
 *
 * Register maps are deliberately absent: which register holds what is a
 * datasheet fact, and writing one from memory produces a driver that
 * talks successfully to the right part and configures the wrong thing.
 */

#include "i2c_devices.h"
#include "app_config.h"
#include "../port/hal.h"

bool da267_read_reg(uint8_t reg, uint8_t *value)
{
    return hal_i2c_write_read(DA267_BUS, DA267_ADDR,
                              &reg, 1u, value, 1u) == 0;
}

bool da267_write_reg(uint8_t reg, uint8_t value)
{
    uint8_t payload[2];
    payload[0] = reg;
    payload[1] = value;
    return hal_i2c_write(DA267_BUS, DA267_ADDR, payload, sizeof(payload)) == 0;
}

void i2c_devices_init(void)
{
    (void)hal_i2c_init(0u, 100000u);  /* I2C0 */
    /* TODO(driver): each part needs its configuration written here.
     * That requires its datasheet register map, which was not supplied. */
}

void i2c_devices_tick(uint32_t now_ms)
{
    (void)now_ms;
    /* TODO(driver): periodic sampling goes here. */
}
