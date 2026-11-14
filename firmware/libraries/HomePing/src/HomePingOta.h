// Over-the-air updates (#79): after one USB flash, later uploads go over WiFi from the
// Arduino IDE (Tools > Port > the node's network port, then the OTA_PASS password).
// Needs a partition scheme with two app slots; the nodes use "Minimal SPIFFS (1.9MB APP
// with OTA)". Only reachable on the home network (UDP/TCP 3232, mDNS); the password is
// checked by the ESP32 before anything is written.
#pragma once

#include <ArduinoOTA.h>
#include <esp_task_wdt.h>

// Loop watchdog (#470): the core doesn't watch loopTask unless asked, so a loop() that
// hangs (a stuck I2C/OneWire/UART read) left the node dead until a power cycle. With this
// the chip resets when loop() hasn't returned for HP_LOOP_WDT_MS. The core's default of
// 5 s is too short here: a TLS connect and handshake can block for 15 s (#394), and the
// e-ink node's fetch and refresh for longer; nothing legitimate takes a minute.
// The same timeout then applies to the idle task of CPU0, which the core already watches.
// An OTA upload blocks loop() for its whole length, so hpStartOTA() pauses the watchdog
// while one runs. Call after hpStartOTA(), at any point in setup().
const uint32_t HP_LOOP_WDT_MS = 60000;
inline bool hpLoopWatchdogOn = false;

inline void hpStartLoopWatchdog() {
  esp_task_wdt_config_t cfg = {};
  cfg.timeout_ms = HP_LOOP_WDT_MS;
  cfg.idle_core_mask = 1 << 0;
  cfg.trigger_panic = true;
  esp_err_t err = esp_task_wdt_reconfigure(&cfg);
  if (err != ESP_OK) {   // never run with the 5 s default: better no watchdog than reset loops
    Serial.printf("Loop watchdog NOT started: reconfigure failed (%d)\n", (int)err);
    return;
  }
  enableLoopWDT();
  hpLoopWatchdogOn = true;
  Serial.printf("Loop watchdog on: reset after %lu s without loop()\n", (unsigned long)(HP_LOOP_WDT_MS / 1000));
}

// Call once after WiFi is up; call ArduinoOTA.handle() in every loop(). The mDNS name is
// the device ID with '-' for '_' (e.g. home-ping-esp32.local): '_' isn't valid in host names.
inline void hpStartOTA(const char* device, const char* password) {
  char host[48];
  snprintf(host, sizeof(host), "%s", device);
  for (char* c = host; *c; c++) if (*c == '_') *c = '-';
  ArduinoOTA.setHostname(host);   // copied by ArduinoOTA
  ArduinoOTA.setPassword(password);
  ArduinoOTA.onStart([]() {
    Serial.println("OTA update starting");
    if (hpLoopWatchdogOn) disableLoopWDT();   // the upload blocks loop(); it ends in a restart
  });
  ArduinoOTA.onEnd([]() { Serial.println("OTA update done, restarting"); });
  ArduinoOTA.onError([](ota_error_t e) {
    Serial.printf("OTA error %u\n", (unsigned)e);
    if (hpLoopWatchdogOn) enableLoopWDT();   // a failed upload: loop() goes on, watched again
  });
  ArduinoOTA.begin();
  Serial.printf("OTA ready: %s.local\n", host);
}
