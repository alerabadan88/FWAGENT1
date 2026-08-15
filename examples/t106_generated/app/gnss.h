/* fw-automation-agent -- generated, not hand-written */
#ifndef GNSS_H
#define GNSS_H

#include <stdbool.h>
#include <stdint.h>

typedef struct {
    bool    valid;      /* RMC status was 'A' */
    double  latitude;   /* degrees, north positive */
    double  longitude;  /* degrees, east positive */
    double  speed_kn;
    uint32_t utc_hhmmss;
    uint32_t date_ddmmyy;
} gnss_fix_t;

void gnss_init(uint8_t port, uint32_t baud);

/* Feeds bytes from the UART into the parser. Returns true, and fills *out,
 * when a complete and checksum-valid RMC sentence has been decoded. */
bool gnss_tick(gnss_fix_t *out);

#endif /* GNSS_H */
