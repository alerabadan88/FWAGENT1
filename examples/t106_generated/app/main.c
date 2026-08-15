/* fw-automation-agent -- generated, not hand-written
 *
 * Report position over the cellular network and show power, GPS and network state on a tri-colour LED.
 */

#include "app_config.h"
#include "../port/hal.h"
#include "led.h"
#include "button.h"
#include "gnss.h"
#include "i2c_devices.h"

int main(void)
{
    hal_init();

    led_init();
    button_init();
    gnss_init(AG3335A_BUS, AG3335A_BAUD);
    i2c_devices_init();

    for (;;) {
        uint32_t now = hal_uptime_ms();

        led_tick(now);
        switch (button_tick(now)) {
        case BTN_SHORT:
            /* TODO(product): what a short press does was not specified. */
            break;
        case BTN_LONG:
            /* TODO(product): what a long press does was not specified. */
            break;
        default:
            break;
        }
        gnss_fix_t fix;
        if (gnss_tick(&fix)) {
            /* A new fix arrived. fix.valid says whether it is usable. */
        }
        i2c_devices_tick(now);

        hal_delay_ms(APP_LOOP_MS);
    }
}
