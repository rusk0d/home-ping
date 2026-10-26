// LD2410C presence publish shared by the radar sketches (#75).
// Payload: contracts/mqtt.md, home/esp32/presence (retained).
#pragma once

#include <ArduinoJson.h>
#include <ld2410.h>
#include <esp_system.h>
#include "HomePing.h"

inline void hpPublishPresence(PubSubClient& mqtt, ld2410& radar,
                              const char* device, const char* topic) {
  if (!mqtt.connected()) return;   // broker down: hpConnectMQTT() retries in loop()
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

  JsonDocument doc;
  doc["device"]          = device;
  doc["radar_connected"] = radar.isConnected();
  doc["presence"]        = presence;
  doc["stationary"]      = stationary;
  doc["moving"]          = moving;
  doc["distance"]        = distance;
  doc["signal"]          = signal;
  doc["rssi"]            = WiFi.RSSI();
  doc["uptime"]          = hpUptimeSeconds();
  // Why the node last booted (#228): the ESP-IDF esp_reset_reason_t code, e.g. 1 power-on,
  // 3 software (ESP.restart(): WiFi timeout, OTA), 4 panic, 6 task watchdog, 9 brownout
  doc["reset_reason"]    = (int)esp_reset_reason();
  // Memory health for the metrics: free heap now, the lowest it has been since boot, and
  // the largest block that can still be allocated (fragmentation; TLS needs ~40 KB in one)
  doc["heap_free"]       = ESP.getFreeHeap();
  doc["heap_min"]        = ESP.getMinFreeHeap();
  doc["heap_block"]      = ESP.getMaxAllocHeap();

  char payload[HP_MQTT_BUFFER - 64];   // room for the topic and the MQTT header
  serializeJson(doc, payload);

  if (mqtt.publish(topic, payload, true)) {
    Serial.print("Presence: ");
    Serial.println(payload);
  } else {
    Serial.println("Presence publish failed");
  }
}
