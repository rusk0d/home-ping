#include <WiFi.h>
#include <PubSubClient.h>
#include <ArduinoJson.h>
#include <MHZ19.h>
#include "esp_system.h"
#include "secrets.h"

/* =======================
   CONFIG
   ======================= */

const char* WIFI_SSID = WIFI_SSID;
const char* WIFI_PASS = WIFI_PASS;

const char* MQTT_BROKER = MQTT_BROKER;
const int   MQTT_PORT   = MQTT_PORT;

const char* MQTT_TOPIC  = "home/esp32/co2";
const char* DEVICE_NAME = "co2_sensor_esp32";

const unsigned long PUBLISH_INTERVAL = 60000;   // every minute is plenty for CO2

// MH-Z19C on UART1
#define CO2_RX   25     // <- sensor Tx
#define CO2_TX   26     // <- sensor Rx
#define CO2_BAUD 9600

// The sensor needs ~3 min after power-up before readings mean anything
const unsigned long WARMUP_MS = 180000;

/* =======================
   GLOBALS
   ======================= */
WiFiClient espClient;
PubSubClient mqttClient(espClient);
MHZ19 mhz;

unsigned long lastPublish = 0;
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
    if (++attempts > 60) {            // ~30s, then reboot and try fresh
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
void publishCO2() {
  int co2       = mhz.getCO2();
  int sensorTmp = mhz.getTemperature();
  int err       = mhz.errorCode;

  bool warming = millis() < WARMUP_MS;
  bool valid   = (err == RESULT_OK) && !warming && co2 > 0 && co2 <= 5000;

  doc.clear();
  doc["device"]  = DEVICE_NAME;
  doc["co2_ok"]  = valid;
  doc["warming"] = warming;
  doc["err"]     = err;
  doc["rssi"]    = WiFi.RSSI();
  doc["uptime"]  = millis() / 1000;

  // Only include a reading when it is actually trustworthy.
  // The backend stores NULL otherwise, so bad values never enter the history.
  if (valid) {
    doc["co2"]        = co2;
    doc["sensor_tmp"] = sensorTmp;
  }

  char payload[256];
  serializeJson(doc, payload);

  if (mqttClient.publish(MQTT_TOPIC, payload, true)) {
    Serial.print("Published: ");
    Serial.println(payload);
  } else {
    Serial.println("Publish failed");
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

  Serial1.begin(CO2_BAUD, SERIAL_8N1, CO2_RX, CO2_TX);
  mhz.begin(Serial1);
  mhz.autoCalibration(true);     // assumes the lowest reading in 24h is 400ppm
  Serial.println("MH-Z19C initialized (warming up ~3 min)");

  connectWiFi();
  mqttClient.setServer(MQTT_BROKER, MQTT_PORT);
  mqttClient.setKeepAlive(60);
  connectMQTT();
}

/* =======================
   LOOP
   ======================= */
void loop() {
  if (WiFi.status() != WL_CONNECTED) {
    Serial.println("WiFi dropped, reconnecting...");
    connectWiFi();
  }

  if (!mqttClient.connected()) connectMQTT();
  mqttClient.loop();

  unsigned long now = millis();
  if (now - lastPublish >= PUBLISH_INTERVAL) {
    lastPublish = now;
    Serial.printf("Free heap: %d\n", ESP.getFreeHeap());
    publishCO2();
  }

  delay(100);
}
