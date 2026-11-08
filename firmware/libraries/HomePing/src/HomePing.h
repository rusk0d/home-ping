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

// Connects to WiFi at boot; never hangs: reboots after ~30 s without a connection. Only for
// setup(): in loop() use hpWiFiWatchdog(), which doesn't block (#383).
inline void hpConnectWiFi(const char* ssid, const char* pass) {
  WiFi.mode(WIFI_STA);
  WiFi.setAutoReconnect(true);   // the default; hpWiFiWatchdog() relies on it
  // With several access points on the same SSID, take the strongest, not the first one found
  // (the main node sat on the far router). Only at connect time: there's no roaming later.
  WiFi.setScanMethod(WIFI_ALL_CHANNEL_SCAN);
  WiFi.setSortMethod(WIFI_CONNECT_AP_BY_SIGNAL);
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

// WiFi lost after boot (#383): the ESP32 reconnects by itself, so loop() only checks and keeps
// running (radar, the fridge's condition timers). A nudge (WiFi.reconnect()) every
// HP_WIFI_KICK_MS in case the auto-reconnect gave up; a restart only after HP_WIFI_RESTART_MS.
// It used to block and restart after 30 s, so a router reboot (1-3 min) rebooted every node
// several times. Call it from every loop(); returns whether WiFi is up.
const uint32_t HP_WIFI_KICK_MS = 30000;
const uint32_t HP_WIFI_RESTART_MS = 10UL * 60 * 1000;

inline bool hpWiFiWatchdog() {
  static bool down = false;
  static uint32_t downSince = 0, lastKick = 0;   // uint32_t like millis(): the wrap works
  if (WiFi.status() == WL_CONNECTED) {
    if (down) {
      Serial.printf("WiFi back after %lus\n", (unsigned long)((uint32_t)(millis() - downSince) / 1000));
      down = false;
    }
    return true;
  }
  uint32_t now = millis();
  if (!down) {
    down = true;
    downSince = lastKick = now;
    Serial.println("WiFi dropped, waiting for it to reconnect");
  } else if ((uint32_t)(now - downSince) >= HP_WIFI_RESTART_MS) {
    Serial.println("No WiFi for 10 min, restarting...");
    ESP.restart();
  } else if ((uint32_t)(now - lastKick) >= HP_WIFI_KICK_MS) {
    lastKick = now;
    WiFi.reconnect();
  }
  return false;
}

// Connects to the broker without blocking the loop (#120): at most one attempt every
// HP_MQTT_RETRY_MS, then it returns, so radar reading and the display keep running while
// the broker is down. Call it from loop() while !mqtt.connected(); returns whether the
// client is connected now. Last will: if this node drops off, the broker publishes
// "offline" (retained) on home/esp32/<device>/status about 1.5 x keepalive later; on
// connect the node publishes "online" there (#13).
const unsigned long HP_MQTT_RETRY_MS = 5000;
// PubSubClient's default 256-byte buffer holds the whole packet (header, topic, payload);
// a bigger packet isn't sent, and publish() just returns false. The presence message
// with the heap fields is ~250 bytes on its own.
const uint16_t HP_MQTT_BUFFER = 512;

inline bool hpConnectMQTT(PubSubClient& mqtt, const char* device,
                          const char* user, const char* pass) {
  if (mqtt.connected()) return true;
  if (mqtt.getBufferSize() < HP_MQTT_BUFFER) mqtt.setBufferSize(HP_MQTT_BUFFER);
  static uint32_t lastAttempt = 0;   // uint32_t like millis() on the ESP32, so the wrap works the same in the host test
  static bool attempted = false;
  uint32_t now = millis();
  if (attempted && (uint32_t)(now - lastAttempt) < HP_MQTT_RETRY_MS) return false;
  attempted = true;
  lastAttempt = now;

  char clientId[64];
  snprintf(clientId, sizeof(clientId), "%s_%llx", device, (unsigned long long)ESP.getEfuseMac());
  char statusTopic[64];
  snprintf(statusTopic, sizeof(statusTopic), "home/esp32/%s/status", device);
  Serial.println("Connecting to MQTT...");
  if (mqtt.connect(clientId, user, pass, statusTopic, 1, true, "offline")) {
    mqtt.publish(statusTopic, "online", true);
    Serial.println("MQTT connected");
    return true;
  }
  Serial.printf("MQTT failed, rc=%d, retrying in %lus\n", mqtt.state(), HP_MQTT_RETRY_MS / 1000);
  return false;
}
