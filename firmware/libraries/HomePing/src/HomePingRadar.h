// LD2410C presence publish shared by the radar sketches (#75).
// Payload: contracts/mqtt.md, home/esp32/presence (retained).
#pragma once

#include <ArduinoJson.h>
#include <ld2410.h>
#include "HomePing.h"

inline void hpPublishPresence(PubSubClient& mqtt, ld2410& radar,
                              const char* device, const char* topic) {
  bool presence   = radar.presenceDetected();
  bool stationary = radar.stationaryTargetDetected();
  bool moving     = radar.movingTargetDetected();
  int distance    = 0;
  int signal      = 0;

  if (stationary) {
    distance = radar.stationaryTargetDistance();
    signal   = radar.stationaryTargetEnergy();
  } else if (moving) {
    distance = radar.movingTargetDistance();
    signal   = radar.movingTargetEnergy();
  }

  StaticJsonDocument<256> doc;
  doc["device"]          = device;
  doc["radar_connected"] = radar.isConnected();
  doc["presence"]        = presence;
  doc["stationary"]      = stationary;
  doc["moving"]          = moving;
  doc["distance"]        = distance;
  doc["signal"]          = signal;
  doc["rssi"]            = WiFi.RSSI();
  doc["uptime"]          = hpUptimeSeconds();

  char payload[256];
  serializeJson(doc, payload);

  if (mqtt.publish(topic, payload, true)) {
    Serial.print("Presence: ");
    Serial.println(payload);
  } else {
    Serial.println("Presence publish failed");
  }
}
