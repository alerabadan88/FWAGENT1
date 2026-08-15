/* fw-automation-agent -- generated, not hand-written
 *
 * NMEA 0183 line assembly and RMC decoding.
 *
 * This is receiver-independent: any module that emits standard NMEA works
 * without change. Vendor-specific configuration sentences (fix rate, which
 * constellations, low-power modes) are NOT here, because they differ per part
 * and inventing them would produce a device that silently keeps its defaults.
 *
 * The checksum is verified before a sentence is used. A corrupt sentence that
 * parses is worse than one that is dropped: it yields a position.
 */

#include "gnss.h"
#include "../port/hal.h"

#include <stdlib.h>
#include <string.h>

#define GNSS_LINE_MAX 96

static uint8_t s_port;
static char    s_line[GNSS_LINE_MAX];
static size_t  s_len;

void gnss_init(uint8_t port, uint32_t baud)
{
    s_port = port;
    s_len = 0u;
    (void)hal_uart_init(port, baud);
}

static int hex_value(char c)
{
    if (c >= '0' && c <= '9') return c - '0';
    if (c >= 'A' && c <= 'F') return c - 'A' + 10;
    if (c >= 'a' && c <= 'f') return c - 'a' + 10;
    return -1;
}

/* $....*HH -- XOR of everything between '$' and '*'. */
static bool checksum_ok(const char *line, size_t len)
{
    if (len < 4u || line[0] != '$') return false;

    size_t star = 0u;
    for (size_t i = 0u; i < len; i++) {
        if (line[i] == '*') { star = i; }
    }
    if (star == 0u || (star + 2u) >= len) return false;

    int hi = hex_value(line[star + 1u]);
    int lo = hex_value(line[star + 2u]);
    if (hi < 0 || lo < 0) return false;

    uint8_t sum = 0u;
    for (size_t i = 1u; i < star; i++) {
        sum ^= (uint8_t)line[i];
    }
    return sum == (uint8_t)((hi << 4) | lo);
}

/* ddmm.mmmm -> degrees. The degree part is a fixed 2 or 3 digits depending on
 * whether this is latitude or longitude, which is why width is passed in. */
static double to_degrees(const char *field, int degree_digits, char hemisphere)
{
    if (field == NULL || field[0] == '\0') return 0.0;

    char degrees[4] = {0};
    for (int i = 0; i < degree_digits && field[i] != '\0'; i++) {
        degrees[i] = field[i];
    }
    double value = atof(degrees) + (atof(field + degree_digits) / 60.0);
    if (hemisphere == 'S' || hemisphere == 'W') {
        value = -value;
    }
    return value;
}

/* Splits in place. Returns how many fields were found. */
static int split(char *line, char **fields, int max_fields)
{
    int count = 0;
    fields[count++] = line;
    for (char *p = line; *p != '\0' && count < max_fields; p++) {
        if (*p == ',' || *p == '*') {
            *p = '\0';
            fields[count++] = p + 1;
        }
    }
    return count;
}

static bool decode_rmc(char *line, gnss_fix_t *out)
{
    char *f[16];
    int n = split(line, f, 16);
    if (n < 10) return false;

    /* Field 0 is $--RMC; the talker prefix varies by constellation. */
    if (strlen(f[0]) < 6 || strcmp(f[0] + 3, "RMC") != 0) return false;

    memset(out, 0, sizeof(*out));
    out->valid       = (f[2][0] == 'A');
    out->utc_hhmmss  = (uint32_t)atol(f[1]);
    out->latitude    = to_degrees(f[3], 2, f[4][0]);
    out->longitude   = to_degrees(f[5], 3, f[6][0]);
    out->speed_kn    = atof(f[7]);
    out->date_ddmmyy = (uint32_t)atol(f[9]);
    return true;
}

bool gnss_tick(gnss_fix_t *out)
{
    uint8_t byte;

    while (hal_uart_read(s_port, &byte, 1u, 0u) == 1) {
        if (byte == '\r') {
            continue;
        }
        if (byte == '\n') {
            bool decoded = false;
            if (s_len > 0u && s_len < GNSS_LINE_MAX) {
                s_line[s_len] = '\0';
                if (checksum_ok(s_line, s_len)) {
                    decoded = decode_rmc(s_line, out);
                }
            }
            s_len = 0u;
            if (decoded) {
                return true;
            }
            continue;
        }
        if (s_len < (GNSS_LINE_MAX - 1u)) {
            s_line[s_len++] = (char)byte;
        } else {
            /* Overlong sentence: drop it rather than truncate into a parse. */
            s_len = 0u;
        }
    }
    return false;
}
