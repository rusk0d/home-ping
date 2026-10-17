// Over-the-air updates (#79): after one USB flash, later uploads go over WiFi from the
// Arduino IDE (Tools > Port > the node's network port, then the OTA_PASS password).
// Needs a partition scheme with two app slots; the nodes use "Minimal SPIFFS (1.9MB APP
// with OTA)". Only reachable on the home network (UDP/TCP 3232, mDNS); the password is
// checked by the ESP32 before anything is written.
#pragma once

#include <ArduinoOTA.h>

// Call once after WiFi is up; call ArduinoOTA.handle() in every loop(). The mDNS name is
// the device ID with '-' for '_' (e.g. home-ping-esp32.local): '_' isn't valid in host names.
inline void hpStartOTA(const char* device, const char* password) {
  char host[48];
  snprintf(host, sizeof(host), "%s", device);
  for (char* c = host; *c; c++) if (*c == '_') *c = '-';
  ArduinoOTA.setHostname(host);   // copied by ArduinoOTA
  ArduinoOTA.setPassword(password);
  ArduinoOTA.onStart([]() { Serial.println("OTA update starting"); });
  ArduinoOTA.onEnd([]() { Serial.println("OTA update done, restarting"); });
  ArduinoOTA.onError([](ota_error_t e) { Serial.printf("OTA error %u\n", (unsigned)e); });
  ArduinoOTA.begin();
  Serial.printf("OTA ready: %s.local\n", host);
}
