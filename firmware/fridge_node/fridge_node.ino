// Fridge temperature monitor node (#290):
// ESP32 with DS18B20 sensor, WiFi, MQTT over TLS, OTA
// Topic: home/esp32/fridge_temp (temperature + alert state)
#include <WiFi.h>
#include <WiFiClientSecure.h>
#include <PubSubClient.h>
#include <ArduinoJson.h>
#include <OneWire.h>
#include <DallasTemperature.h>
#include "esp_system.h"
#include <HomePing.h>
#include <HomePingTls.h>
#include <HomePingOta.h>
#include <HomePingCondition.h>
#include "secrets.h"

/* =======================
   CONFIG
   ======================= */

// WIFI_SSID, WIFI_PASS, MQTT_BROKER, MQTT_PORT, MQTT_USER, MQTT_PASS come from secrets.h

const char* MQTT_TOPIC_FRIDGE = "home/esp32/fridge_temp";
const char* DEVICE_NAME = "fridge_temp_esp32";

const unsigned long TEMP_INTERVAL = 60000;  // publish every 60 s

// DS18B20 (OneWire, GPIO4)
#define ONE_WIRE_PIN 4

// Alert thresholds (#290, #294): a condition starts when its threshold is crossed and ends
// HYSTERESIS past it, so a fridge hovering at 5.0 °C doesn't restart the timer on every dip
const float COLD_BELOW = 0.0;   // cold: below 0 °C for 30 min
const float WARM_ABOVE = 5.0;   // warm_low: above 5 °C for 30 min, warm_critical_duration: for 2 h
const float HOT_ABOVE  = 8.0;   // warm_critical: above 8 °C for 30 min
const float HYSTERESIS = 0.5;
const unsigned long COLD_FOR      = 30UL * 60 * 1000;
const unsigned long WARM_FOR      = 30UL * 60 * 1000;
const unsigned long WARM_LONG_FOR = 2UL * 60 * 60 * 1000;
const unsigned long HOT_FOR       = 30UL * 60 * 1000;

/* =======================
   GLOBALS
   ======================= */
WiFiClientSecure espClient;
PubSubClient mqttClient(espClient);
OneWire oneWire(ONE_WIRE_PIN);
DallasTemperature sensors(&oneWire);

unsigned long lastTempPublish = 0;

// One per condition: whether it holds, and since when (HomePingCondition.h, #306)
HpCondition cold, warm, hot;
String currentAlertType = "";   // "", "cold", "warm_low", "warm_critical", "warm_critical_duration"

JsonDocument doc;

/* =======================
   PUBLISH
   ======================= */
void publishTemp() {
  sensors.requestTemperatures();
  float tempC = sensors.getTempCByIndex(0);

  if (tempC == DEVICE_DISCONNECTED_C) {
    Serial.println("DS18B20 read error");
    return;
  }
  // 85.0 °C is the DS18B20's power-on value: after a brownout or a glitch on the data line it
  // reports that instead of a conversion (#352). No fridge gets there; skip it like a read error
  // (the backend drops it too, outside FRIDGE_VALID).
  if (tempC == 85.0f) {
    Serial.println("DS18B20 power-on value (85 C), skipped");
    return;
  }

  unsigned long now = millis();
  cold.track(tempC < COLD_BELOW, tempC >= COLD_BELOW + HYSTERESIS, now);
  warm.track(tempC > WARM_ABOVE, tempC <= WARM_ABOVE - HYSTERESIS, now);
  hot.track(tempC > HOT_ABOVE,  tempC <= HOT_ABOVE - HYSTERESIS,  now);

  // The most severe condition that has lasted long enough
  String alertType = "";
  if (hot.lasted(HOT_FOR, now))             alertType = "warm_critical";
  else if (warm.lasted(WARM_LONG_FOR, now)) alertType = "warm_critical_duration";
  else if (warm.lasted(WARM_FOR, now))      alertType = "warm_low";
  else if (cold.lasted(COLD_FOR, now))      alertType = "cold";

  if (alertType != currentAlertType) {
    Serial.printf("Alert: %s -> %s\n", currentAlertType.c_str(), alertType.c_str());
    currentAlertType = alertType;
  }

  // Publish
  doc.clear();
  doc["device"]       = DEVICE_NAME;
  doc["temp_c"]       = roundf(tempC * 10) / 10;   // 1 decimal (contract); DS18B20 steps are 0.0625 °C
  doc["alert"]        = currentAlertType;
  doc["rssi"]         = WiFi.RSSI();
  doc["uptime"]       = hpUptimeSeconds();   // 64-bit timer: doesn't wrap like millis() (#8)
  doc["reset_reason"] = (int)esp_reset_reason();
  doc["heap_free"]    = ESP.getFreeHeap();
  doc["heap_min"]     = ESP.getMinFreeHeap();
  doc["heap_block"]   = ESP.getMaxAllocHeap();

  String payload;
  serializeJson(doc, payload);

  mqttClient.publish(MQTT_TOPIC_FRIDGE, payload.c_str(), true);  // retained
  Serial.printf("Fridge: %.1f°C, alert=%s, rssi=%d, heap_free=%u\n", tempC, currentAlertType.c_str(), WiFi.RSSI(), ESP.getFreeHeap());
}

/* =======================
   SETUP
   ======================= */
void setup() {
  Serial.begin(115200);
  delay(1000);
  Serial.println("\n\nFridge temp monitor starting...");

  // Sensors
  sensors.begin();
  int count = sensors.getDeviceCount();
  Serial.printf("DS18B20 sensors found: %d\n", count);

  if (count == 0) {
    Serial.println("ERROR: No DS18B20 found. Check wiring and pull-up resistor.");
  }

  // Network
  hpConnectWiFi(WIFI_SSID, WIFI_PASS);
  hpStartOTA(DEVICE_NAME, OTA_PASS);

  // MQTT
  espClient.setCACert(HP_MQTT_ROOT_CA);
  mqttClient.setServer(MQTT_BROKER, MQTT_PORT);
  mqttClient.setKeepAlive(60);
  hpConnectMQTT(mqttClient, DEVICE_NAME, MQTT_USER, MQTT_PASS);

  Serial.println("Setup complete");
}

/* =======================
   LOOP
   ======================= */
void loop() {
  ArduinoOTA.handle();

  // WiFi watchdog: doesn't block; restarts only after 10 min without WiFi (#383)
  bool wifi = hpWiFiWatchdog();

  if (wifi && !mqttClient.connected()) {
    hpConnectMQTT(mqttClient, DEVICE_NAME, MQTT_USER, MQTT_PASS);
  }
  mqttClient.loop();

  // Temperature publish
  unsigned long now = millis();
  if (now - lastTempPublish >= TEMP_INTERVAL) {
    lastTempPublish = now;
    publishTemp();
  }

  delay(50);
}
