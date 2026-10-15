#include <Wire.h>
#include <WiFi.h>
#include <PubSubClient.h>
#include <ArduinoJson.h>
#include <Adafruit_BME280.h>
#include <ld2410.h>
#include "esp_system.h"
#include "secrets.h"

/* =======================
   CONFIG
   ======================= */

// WIFI_SSID, WIFI_PASS, MQTT_BROKER, MQTT_PORT, MQTT_USER, MQTT_PASS come from secrets.h

const char* MQTT_TOPIC_PRESENCE = "home/esp32/presence";
const char* MQTT_TOPIC_ENV      = "home/esp32/bme280";
const char* DEVICE_NAME = "home_ping_esp32";

const unsigned long PRESENCE_INTERVAL = 5000;   // radar: every 5s
const unsigned long ENV_INTERVAL      = 30000;  // temp/hum/pressure: every 30s

// LD2410C radar (UART2)
#define RADAR_RX   16
#define RADAR_TX   17
#define RADAR_BAUD 256000   // LD2410C factory default

// BME280 (I2C)
#define I2C_SDA     21
#define I2C_SCL     22
#define BME_ADDRESS 0x76
// The sensor sits near the board and reads a bit high; subtract if needed.
const float TEMP_OFFSET = 0.0;

/* =======================
   GLOBALS
   ======================= */
WiFiClient espClient;
PubSubClient mqttClient(espClient);
ld2410 radar;
Adafruit_BME280 bme;

bool bmeReady = false;

unsigned long lastPresencePublish = 0;
unsigned long lastEnvPublish = 0;
StaticJsonDocument<256> doc;

/* =======================
   WIFI / MQTT
   ======================= */
void connectWiFi() {
  WiFi.mode(WIFI_STA);
  WiFi.begin(WIFI_SSID, WIFI_PASS);
  Serial.println("Connecting to WiFi...");
  int attempts = 0;
  while (WiFi.status() != WL_CONNECTED) {
    delay(500);
    Serial.print(".");
    // Never hang forever: reboot and start fresh after ~30s
    if (++attempts > 60) {
      Serial.println("\nWiFi timeout, restarting...");
      ESP.restart();
    }
  }
  Serial.println("\nWiFi connected");
  Serial.println(WiFi.localIP());
}

void connectMQTT() {
  while (!mqttClient.connected()) {
    char clientId[64];
    snprintf(clientId, sizeof(clientId), "%s_%llx", DEVICE_NAME, ESP.getEfuseMac());
    Serial.println("Connecting to MQTT...");
    if (mqttClient.connect(clientId, MQTT_USER, MQTT_PASS)) {
      Serial.println("MQTT connected");
    } else {
      Serial.printf("MQTT failed, rc=%d, retrying in 5s\n", mqttClient.state());
      delay(5000);
    }
  }
}

/* =======================
   PUBLISH
   ======================= */
void publishPresence() {
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

  doc.clear();
  doc["device"]          = DEVICE_NAME;
  doc["radar_connected"] = radar.isConnected();
  doc["presence"]        = presence;
  doc["stationary"]      = stationary;
  doc["moving"]          = moving;
  doc["distance"]        = distance;
  doc["signal"]          = signal;
  doc["rssi"]            = WiFi.RSSI();
  doc["uptime"]          = millis() / 1000;

  char payload[256];
  serializeJson(doc, payload);

  if (mqttClient.publish(MQTT_TOPIC_PRESENCE, payload, true)) {
    Serial.print("Presence: ");
    Serial.println(payload);
  } else {
    Serial.println("Presence publish failed");
  }
}

void publishEnv() {
  if (!bmeReady) {
    Serial.println("BME280 not ready, skipping env publish");
    return;
  }

  float tempC    = bme.readTemperature() + TEMP_OFFSET;
  float humidity = bme.readHumidity();
  float pressure = bme.readPressure() / 100.0F;

  if (isnan(tempC) || isnan(humidity) || isnan(pressure)) {
    Serial.println("BME280 read failed, skipping env publish");
    return;
  }

  doc.clear();
  doc["device"]   = DEVICE_NAME;
  doc["temp_c"]   = tempC;
  doc["humidity"] = humidity;
  doc["pressure"] = pressure;
  doc["uptime"]   = millis() / 1000;

  char payload[256];
  serializeJson(doc, payload);

  if (mqttClient.publish(MQTT_TOPIC_ENV, payload, true)) {
    Serial.print("Env: ");
    Serial.println(payload);
  } else {
    Serial.println("Env publish failed");
  }
}

/* =======================
   SETUP
   ======================= */
void setup() {
  Serial.begin(115200);
  delay(1000);
  Serial.printf("Boot reason: %d\n", esp_reset_reason());
  Serial.printf("Free heap: %d\n", ESP.getFreeHeap());
  Serial.println("Booting...");

  // BME280 first — it needs a moment to settle after power-up anyway
  Wire.begin(I2C_SDA, I2C_SCL);
  delay(1000);
  if (bme.begin(BME_ADDRESS, &Wire)) {
    bmeReady = true;
    Serial.println("BME280 initialized");
  } else {
    // Retry once: the sensor sometimes misses the very first attempt
    delay(1000);
    bmeReady = bme.begin(BME_ADDRESS, &Wire);
    Serial.println(bmeReady ? "BME280 initialized (2nd try)"
                            : "BME280 NOT found, continuing without it");
  }

  // Network next, so nothing blocks once the radar starts streaming
  connectWiFi();
  mqttClient.setServer(MQTT_BROKER, MQTT_PORT);
  mqttClient.setKeepAlive(60);
  connectMQTT();

  // Radar last
  Serial2.setRxBufferSize(1024);
  Serial2.begin(RADAR_BAUD, SERIAL_8N1, RADAR_RX, RADAR_TX);
  delay(500);
  while (Serial2.available()) Serial2.read();  // drain any junk
  radar.begin(Serial2, false);
  Serial.println("LD2410C initialized");
}

/* =======================
   LOOP
   ======================= */
void loop() {
  // WiFi watchdog
  if (WiFi.status() != WL_CONNECTED) {
    Serial.println("WiFi dropped, reconnecting...");
    connectWiFi();
  }

  if (!mqttClient.connected()) connectMQTT();
  mqttClient.loop();

  radar.read();

  unsigned long now = millis();

  if (now - lastPresencePublish >= PRESENCE_INTERVAL) {
    lastPresencePublish = now;
    publishPresence();
  }

  if (now - lastEnvPublish >= ENV_INTERVAL) {
    lastEnvPublish = now;
    Serial.printf("Free heap: %d\n", ESP.getFreeHeap());
    publishEnv();
  }

  delay(50);
}
