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

// Alert thresholds (#290)
const float ALERT_BELOW = 0.0;      // warning: below 0°C for 30+ min
const float ALERT_ABOVE_LOW = 5.0;  // warning: above 5°C for 30 min
const float ALERT_ABOVE_MID = 8.0;  // critical: above 8°C for 30 min
const float ALERT_ABOVE_HIGH = 5.0; // critical: above 5°C for 2+ hours
const unsigned long ALERT_BELOW_DUR = 1800000;     // 30 min in ms
const unsigned long ALERT_ABOVE_LOW_DUR = 1800000;   // 30 min in ms
const unsigned long ALERT_ABOVE_MID_DUR = 1800000;   // 30 min in ms
const unsigned long ALERT_ABOVE_HIGH_DUR = 7200000;  // 2 hours in ms

/* =======================
   GLOBALS
   ======================= */
WiFiClientSecure espClient;
PubSubClient mqttClient(espClient);
OneWire oneWire(ONE_WIRE_PIN);
DallasTemperature sensors(&oneWire);

unsigned long lastTempPublish = 0;
unsigned long alertStartTime = 0;  // when current alert condition started
String currentAlertType = "";      // "", "cold", "warm_low", "warm_high", "warm_critical"

JsonDocument doc;

/* =======================
   HELPERS
   ======================= */
bool initBme(bool verbose) {
  // Not used in this node, but kept for compatibility with HomePing patterns
  return true;
}

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

  // Determine alert state
  String alertType = "";
  unsigned long now = millis();
  unsigned long alertDuration = now - alertStartTime;

  if (tempC < ALERT_BELOW) {
    alertType = "cold";
  } else if (tempC > ALERT_ABOVE_HIGH && alertDuration >= ALERT_ABOVE_HIGH_DUR) {
    alertType = "warm_critical_duration";
  } else if (tempC > ALERT_ABOVE_MID && alertDuration >= ALERT_ABOVE_MID_DUR) {
    alertType = "warm_critical";
  } else if (tempC > ALERT_ABOVE_LOW && alertDuration >= ALERT_ABOVE_LOW_DUR) {
    alertType = "warm_low";
  }

  // Handle alert state transitions
  if (alertType != currentAlertType) {
    if (alertType.length() > 0) {
      // Alert started or changed
      alertStartTime = now;
      Serial.printf("Alert state: %s\n", alertType.c_str());
    } else {
      // Alert ended
      Serial.printf("Alert ended (was: %s)\n", currentAlertType.c_str());
      alertStartTime = 0;
    }
    currentAlertType = alertType;
  }

  // Publish
  doc.clear();
  doc["device"]       = DEVICE_NAME;
  doc["temp_c"]       = tempC;
  doc["alert"]        = currentAlertType;
  doc["rssi"]         = WiFi.RSSI();
  doc["uptime"]       = millis() / 1000;
  doc["reset_reason"] = esp_reset_reason();

  String payload;
  serializeJson(doc, payload);

  mqttClient.publish(MQTT_TOPIC_FRIDGE, payload.c_str(), true);  // retained
  Serial.printf("Fridge: %.1f°C, alert=%s, rssi=%d\n", tempC, currentAlertType.c_str(), WiFi.RSSI());
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

  // WiFi watchdog
  if (WiFi.status() != WL_CONNECTED) {
    Serial.println("WiFi dropped, reconnecting...");
    hpConnectWiFi(WIFI_SSID, WIFI_PASS);
  }

  if (!mqttClient.connected()) {
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
