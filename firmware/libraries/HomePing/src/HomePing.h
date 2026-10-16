// Code shared by the Home Ping sketches (#75): WiFi, MQTT with the status last will,
// and uptime. Radar presence is in HomePingRadar.h (only for sketches with a radar).
// Topics and payloads: contracts/mqtt.md.
#pragma once

#include <WiFi.h>
#include <PubSubClient.h>
#include <esp_timer.h>

// Seconds since boot from the 64-bit timer. millis() wraps after ~49.7 days, which
// the backend would log as a restart (#8).
inline uint32_t hpUptimeSeconds() {
  return (uint32_t)(esp_timer_get_time() / 1000000ULL);
}

// Connects to WiFi; never hangs: reboots after ~30 s without a connection.
inline void hpConnectWiFi(const char* ssid, const char* pass) {
  WiFi.mode(WIFI_STA);
  WiFi.begin(ssid, pass);
  Serial.println("Connecting to WiFi...");
  int attempts = 0;
  while (WiFi.status() != WL_CONNECTED) {
    delay(500);
    Serial.print(".");
    if (++attempts > 60) {
      Serial.println("\nWiFi timeout, restarting...");
      ESP.restart();
    }
  }
  Serial.println("\nWiFi connected");
  Serial.println(WiFi.localIP());
}

// Connects to the broker (blocking, retries every 5 s). Last will: if this node
// drops off, the broker publishes "offline" (retained) on home/esp32/<device>/status
// about 1.5 x keepalive later; on connect the node publishes "online" there (#13).
inline void hpConnectMQTT(PubSubClient& mqtt, const char* device,
                          const char* user, const char* pass) {
  while (!mqtt.connected()) {
    char clientId[64];
    snprintf(clientId, sizeof(clientId), "%s_%llx", device, ESP.getEfuseMac());
    char statusTopic[64];
    snprintf(statusTopic, sizeof(statusTopic), "home/esp32/%s/status", device);
    Serial.println("Connecting to MQTT...");
    if (mqtt.connect(clientId, user, pass, statusTopic, 1, true, "offline")) {
      mqtt.publish(statusTopic, "online", true);
      Serial.println("MQTT connected");
    } else {
      Serial.printf("MQTT failed, rc=%d, retrying in 5s\n", mqtt.state());
      delay(5000);
    }
  }
}
