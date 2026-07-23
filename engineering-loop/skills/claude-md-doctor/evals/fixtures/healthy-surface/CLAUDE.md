# beeper

Firmware for the workshop door chime (ESP32).

## Build & flash

- Build: `idf.py build`
- Flash: `idf.py -p /dev/ttyUSB0 flash monitor`

## Constraints

- GPIO 12 is strapping — never assign it; the chime speaker is on GPIO 25.
- All timing uses `esp_timer`, not FreeRTOS ticks; the chime cadence spec
  lives in `main/cadence.h` and is the only authority for tone durations.

## Testing

Host-side unit tests: `idf.py --preview host-test`. Hardware-timing changes
need a bench test with the logic analyzer before merging.
