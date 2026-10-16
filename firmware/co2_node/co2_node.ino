#include <WiFi.h>
#include <PubSubClient.h>
#include <ArduinoJson.h>
#include <MHZ19.h>
#include <Wire.h>
#include <Adafruit_BME280.h>
#include "esp_system.h"
#include <HomePing.h>   // firmware/libraries/HomePing (#75)
#include "secrets.h"

/* =======================
   CONFIG
   ======================= */

// WIFI_SSID, WIFI_PASS, MQTT_BROKER, MQTT_PORT, MQTT_USER, MQTT_PASS come from secrets.h

const char* MQTT_TOPIC     = "home/esp32/co2";
const char* MQTT_TOPIC_ENV = "home/esp32/bme280";   // same topic and payload as the old main node
const char* DEVICE_NAME    = "co2_sensor_esp32";

const unsigned long PUBLISH_INTERVAL = 60000;   // every minute is plenty for CO2
const unsigned long ENV_INTERVAL     = 30000;   // temp/hum/pressure: every 30s

// MH-Z19C on UART1
#define CO2_RX   25     // <- sensor Tx
#define CO2_TX   26     // <- sensor Rx
#define CO2_BAUD 9600

// The sensor needs ~3 min after power-up before readings mean anything
const unsigned long WARMUP_MS = 180000;

// BME280 (I2C), moved here from the old main node (node merge step 1, #57).
// GPIO 16/17 were the old main node's radar UART; the radar needs other pins here.
#define I2C_SDA 16
#define I2C_SCL 17
// The module is at 0x76 or 0x77 depending on its SDO pin; both are tried
const uint8_t BME_ADDRESSES[] = {0x76, 0x77};
// Next to the MH-Z19 (heated lamp) the sensor reads high; set after comparing with a
// reference thermometer
const float TEMP_OFFSET = 0.0;

/* =======================
   GLOBALS
   ======================= */
WiFiClient espClient;
PubSubClient mqttClient(espClient);
MHZ19 mhz;
Adafruit_BME280 bme;

bool bmeReady = false;
unsigned long lastPublish = 0;
unsigned long lastEnvPublish = 0;
StaticJsonDocument<256> doc;

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
  doc["uptime"]  = hpUptimeSeconds();

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

// Same payload as the old main node (contracts/mqtt.md, home/esp32/bme280)
void publishEnv() {
  if (!bmeReady) return;   // reported once at boot
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
  doc["uptime"]   = hpUptimeSeconds();

  char payload[256];
  serializeJson(doc, payload);

  if (mqttClient.publish(MQTT_TOPIC_ENV, payload, true)) {
    Serial.print("Env: ");
    Serial.println(payload);
  } else {
    Serial.println("Env publish failed");
  }
}

// Tries both addresses, twice: the sensor sometimes misses the very first attempt
bool initBme() {
  for (int attempt = 0; attempt < 2; attempt++) {
    for (uint8_t addr : BME_ADDRESSES) {
      if (bme.begin(addr, &Wire)) {
        Serial.printf("BME280 initialized at 0x%02X\n", addr);
        return true;
      }
    }
    delay(1000);
  }
  Serial.println("BME280 NOT found (check SDA/SCL wiring), continuing without it");
  return false;
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

  Wire.begin(I2C_SDA, I2C_SCL);
  delay(100);
  bmeReady = initBme();

  Serial1.begin(CO2_BAUD, SERIAL_8N1, CO2_RX, CO2_TX);
  mhz.begin(Serial1);
  mhz.autoCalibration(true);     // assumes the lowest reading in 24h is 400ppm
  Serial.println("MH-Z19C initialized (warming up ~3 min)");

  hpConnectWiFi(WIFI_SSID, WIFI_PASS);
  mqttClient.setServer(MQTT_BROKER, MQTT_PORT);
  mqttClient.setKeepAlive(60);
  hpConnectMQTT(mqttClient, DEVICE_NAME, MQTT_USER, MQTT_PASS);   // first attempt; loop() retries (#120)
}

/* =======================
   LOOP
   ======================= */
void loop() {
  if (WiFi.status() != WL_CONNECTED) {
    Serial.println("WiFi dropped, reconnecting...");
    hpConnectWiFi(WIFI_SSID, WIFI_PASS);
  }

  if (!mqttClient.connected()) hpConnectMQTT(mqttClient, DEVICE_NAME, MQTT_USER, MQTT_PASS);
  mqttClient.loop();

  unsigned long now = millis();
  if (now - lastPublish >= PUBLISH_INTERVAL) {
    lastPublish = now;
    Serial.printf("Free heap: %d\n", ESP.getFreeHeap());
    publishCO2();
  }

  if (now - lastEnvPublish >= ENV_INTERVAL) {
    lastEnvPublish = now;
    publishEnv();
  }

  delay(100);
}
