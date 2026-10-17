// Main node (home_ping_esp32), merged from the old main node and the CO2 node (#57):
// LD2410C radar, BME280 and MH-Z19C on one board. Topics and payloads as before
// (contracts/mqtt.md): presence every 5 s, bme280 every 30 s, co2 every 60 s.
#include <Wire.h>
#include <WiFiClientSecure.h>
#include <ArduinoJson.h>
#include <Adafruit_BME280.h>
#include <MHZ19.h>
#include <ld2410.h>
#include "esp_system.h"
#include <HomePingRadar.h>   // firmware/libraries/HomePing (#75)
#include <HomePingTls.h>
#include <HomePingOta.h>
#include "secrets.h"

/* =======================
   CONFIG
   ======================= */

// WIFI_SSID, WIFI_PASS, MQTT_BROKER, MQTT_PORT, MQTT_USER, MQTT_PASS come from secrets.h

const char* MQTT_TOPIC_PRESENCE = "home/esp32/presence";
const char* MQTT_TOPIC_ENV      = "home/esp32/bme280";
const char* MQTT_TOPIC_CO2      = "home/esp32/co2";
const char* DEVICE_NAME = "home_ping_esp32";

const unsigned long PRESENCE_INTERVAL = 5000;   // radar: every 5s
const unsigned long ENV_INTERVAL      = 30000;  // temp/hum/pressure: every 30s
const unsigned long CO2_INTERVAL      = 60000;  // every minute is plenty for CO2

// LD2410C radar (UART2)
#define RADAR_RX   16   // RX2 <- radar TX
#define RADAR_TX   17   // TX2 -> radar RX
#define RADAR_BAUD 256000   // LD2410C factory default

// BME280 (I2C), the ESP32's default I2C pins
#define I2C_SDA 21
#define I2C_SCL 22
// The module is at 0x76 or 0x77 depending on its SDO pin; both are tried
const uint8_t BME_ADDRESSES[] = {0x76, 0x77};
// Next to the MH-Z19 (heated lamp) the sensor reads high; set after comparing with a
// reference thermometer
const float TEMP_OFFSET = 0.0;

// MH-Z19C (UART1)
#define CO2_RX   25     // <- sensor Tx
#define CO2_TX   26     // <- sensor Rx
#define CO2_BAUD 9600
// The sensor needs ~3 min after power-up before readings mean anything
const uint32_t CO2_WARMUP_S = 180;

// Calibration (#167). Automatic baseline correction (ABC) is OFF: it takes the lowest
// reading of each 24 h as 400 ppm, which only works if the room is aired to outdoor level
// every day; here it made the readings go as low as 261 ppm. Instead, a manual zero-point
// calibration now and then: run the node in fresh air (outdoors, or at a wide-open window)
// for at least CAL_MIN_UPTIME_S, then hold the BOOT button for CAL_HOLD_MS (or send
// "calibrate" on serial). The sensor then takes the current air as 400 ppm.
#define CAL_BUTTON 0     // the board's BOOT button; a normal input once the sketch runs
#define STATUS_LED 2     // the board's LED: slow blinks = calibrated, fast = refused
const uint32_t CAL_MIN_UPTIME_S = 20 * 60;   // the sensor must have settled in fresh air
const unsigned long CAL_HOLD_MS = 5000;

/* =======================
   GLOBALS
   ======================= */
WiFiClientSecure espClient;   // MQTT over TLS (#31)
PubSubClient mqttClient(espClient);
ld2410 radar;
Adafruit_BME280 bme;
MHZ19 mhz;

bool bmeReady = false;

unsigned long lastPresencePublish = 0;
unsigned long lastEnvPublish = 0;
unsigned long lastCo2Publish = 0;
unsigned long calPressedAt = 0;   // 0 = BOOT button not held
JsonDocument doc;

/* =======================
   PUBLISH
   ======================= */
// home/esp32/bme280 (contracts/mqtt.md)
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

// home/esp32/co2 (contracts/mqtt.md)
void publishCO2() {
  int co2       = mhz.getCO2();
  int sensorTmp = mhz.getTemperature();
  int err       = mhz.errorCode;

  // 64-bit uptime, so no false warm-up when millis() wraps after ~49.7 days (#119)
  bool warming = hpUptimeSeconds() < CO2_WARMUP_S;
  bool valid   = (err == RESULT_OK) && !warming && co2 > 0 && co2 <= 5000;

  doc.clear();
  doc["device"]  = DEVICE_NAME;
  doc["co2_ok"]  = valid;
  doc["warming"] = warming;
  doc["err"]     = err;
  doc["rssi"]    = WiFi.RSSI();
  doc["uptime"]  = hpUptimeSeconds();
  doc["abc"]     = mhz.getABC();   // the sensor's own setting, so "off" can be checked remotely (#167)

  // Only include a reading when it is actually trustworthy.
  // The backend stores NULL otherwise, so bad values never enter the history.
  if (valid) {
    doc["co2"]        = co2;
    doc["sensor_tmp"] = sensorTmp;
  }

  char payload[256];
  serializeJson(doc, payload);

  if (mqttClient.publish(MQTT_TOPIC_CO2, payload, true)) {
    Serial.print("CO2: ");
    Serial.println(payload);
  } else {
    Serial.println("CO2 publish failed");
  }
}

void blink(int times, int ms) {
  for (int i = 0; i < times; i++) {
    digitalWrite(STATUS_LED, HIGH); delay(ms);
    digitalWrite(STATUS_LED, LOW);  delay(ms);
  }
}

// Zero-point calibration (#167): the current air becomes 400 ppm. Refused until the node
// has run CAL_MIN_UPTIME_S, so a short press right after carrying it outside can't
// calibrate on stale indoor air.
void calibrateCO2() {
  uint32_t up = hpUptimeSeconds();
  if (up < CAL_MIN_UPTIME_S) {
    Serial.printf("CO2 calibration refused: up %us, needs %us in fresh air first\n", up, CAL_MIN_UPTIME_S);
    blink(10, 100);
    return;
  }
  Serial.printf("CO2 calibration: zero point (400 ppm) at a reading of %d ppm\n", mhz.getCO2());
  mhz.calibrate();
  blink(3, 500);
}

// BOOT held for CAL_HOLD_MS, or "calibrate" on serial
void checkCalibrationRequest() {
  if (digitalRead(CAL_BUTTON) == LOW) {
    if (calPressedAt == 0) calPressedAt = millis();
    else if (millis() - calPressedAt >= CAL_HOLD_MS) {
      calibrateCO2();
      while (digitalRead(CAL_BUTTON) == LOW) delay(10);   // once per press
      calPressedAt = 0;
    }
  } else {
    calPressedAt = 0;
  }
  if (Serial.available()) {
    String cmd = Serial.readStringUntil('\n');
    cmd.trim();
    if (cmd == "calibrate") calibrateCO2();
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

  // BME280 first; it needs a moment to settle after power-up anyway
  Wire.begin(I2C_SDA, I2C_SCL);
  delay(100);
  bmeReady = initBme();

  // MH-Z19C: warms up for ~3 min, readings are flagged invalid until then
  Serial1.begin(CO2_BAUD, SERIAL_8N1, CO2_RX, CO2_TX);
  mhz.begin(Serial1);
  mhz.autoCalibration(false);    // ABC off, manual calibration instead (#167, see CAL_*)
  Serial.printf("MH-Z19C initialized (warming up ~3 min), ABC %s\n", mhz.getABC() ? "ON" : "off");
  pinMode(CAL_BUTTON, INPUT_PULLUP);
  pinMode(STATUS_LED, OUTPUT);

  // Network next, so nothing blocks once the radar starts streaming
  hpConnectWiFi(WIFI_SSID, WIFI_PASS);
  hpStartOTA(DEVICE_NAME, OTA_PASS);
  espClient.setCACert(HP_MQTT_ROOT_CA);
  mqttClient.setServer(MQTT_BROKER, MQTT_PORT);
  mqttClient.setKeepAlive(60);
  hpConnectMQTT(mqttClient, DEVICE_NAME, MQTT_USER, MQTT_PASS);   // first attempt; loop() retries (#120)

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
  ArduinoOTA.handle();

  // WiFi watchdog
  if (WiFi.status() != WL_CONNECTED) {
    Serial.println("WiFi dropped, reconnecting...");
    hpConnectWiFi(WIFI_SSID, WIFI_PASS);
  }

  if (!mqttClient.connected()) hpConnectMQTT(mqttClient, DEVICE_NAME, MQTT_USER, MQTT_PASS);
  mqttClient.loop();

  radar.read();
  checkCalibrationRequest();

  unsigned long now = millis();
  if (now - lastPresencePublish >= PRESENCE_INTERVAL) {
    lastPresencePublish = now;
    hpPublishPresence(mqttClient, radar, DEVICE_NAME, MQTT_TOPIC_PRESENCE);
  }

  if (now - lastEnvPublish >= ENV_INTERVAL) {
    lastEnvPublish = now;
    publishEnv();
  }

  if (now - lastCo2Publish >= CO2_INTERVAL) {
    lastCo2Publish = now;
    Serial.printf("Free heap: %d\n", ESP.getFreeHeap());
    publishCO2();
  }

  delay(50);
}
