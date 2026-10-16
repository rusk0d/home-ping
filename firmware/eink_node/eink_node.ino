#include <WiFi.h>
#include <HTTPClient.h>
#include <WiFiClientSecure.h>
#include <ArduinoJson.h>
#include <GxEPD2_BW.h>
#include <Fonts/FreeSans12pt7b.h>
#include <Fonts/FreeSans9pt7b.h>
#include <SPI.h>
#include <PubSubClient.h>
#include <time.h>
#include <ld2410.h>
#include "secrets.h"

// ========================
// WIFI CONFIG
// ========================
const char* ssid     = WIFI_SSID;
const char* password = WIFI_PASS;
const unsigned long REBOOT_INTERVAL = 43200000UL; // 12 hours

// ========================
// BKK API CONFIG
// ========================
const char* bkkUrl = BKK_URL;
// The route filter is taken from BKK_URL's includeRouteId=... parameters (#70),
// so the route IDs live only in secrets.h.

// Root CA of futar.bkk.hu (DigiCert Global Root G2, valid until 2038-01-15).
// The HTTPS request is only made if the server's certificate chains up to it (#70).
// If BKK switches to another CA, the departures stop loading ("BKK API error: -1"
// on serial): replace this with the new root, e.g. from
// `openssl s_client -connect futar.bkk.hu:443 -showcerts`.
// SHA-256 fingerprint: CB:3C:CB:B7:60:31:E5:E0:13:8F:8D:D3:9A:23:F9:DE:47:FF:C3:5E:43:C1:14:4C:EA:27:D4:6A:5A:B1:CB:5F
const char* BKK_ROOT_CA = R"PEM(
-----BEGIN CERTIFICATE-----
MIIDjjCCAnagAwIBAgIQAzrx5qcRqaC7KGSxHQn65TANBgkqhkiG9w0BAQsFADBh
MQswCQYDVQQGEwJVUzEVMBMGA1UEChMMRGlnaUNlcnQgSW5jMRkwFwYDVQQLExB3
d3cuZGlnaWNlcnQuY29tMSAwHgYDVQQDExdEaWdpQ2VydCBHbG9iYWwgUm9vdCBH
MjAeFw0xMzA4MDExMjAwMDBaFw0zODAxMTUxMjAwMDBaMGExCzAJBgNVBAYTAlVT
MRUwEwYDVQQKEwxEaWdpQ2VydCBJbmMxGTAXBgNVBAsTEHd3dy5kaWdpY2VydC5j
b20xIDAeBgNVBAMTF0RpZ2lDZXJ0IEdsb2JhbCBSb290IEcyMIIBIjANBgkqhkiG
9w0BAQEFAAOCAQ8AMIIBCgKCAQEAuzfNNNx7a8myaJCtSnX/RrohCgiN9RlUyfuI
2/Ou8jqJkTx65qsGGmvPrC3oXgkkRLpimn7Wo6h+4FR1IAWsULecYxpsMNzaHxmx
1x7e/dfgy5SDN67sH0NO3Xss0r0upS/kqbitOtSZpLYl6ZtrAGCSYP9PIUkY92eQ
q2EGnI/yuum06ZIya7XzV+hdG82MHauVBJVJ8zUtluNJbd134/tJS7SsVQepj5Wz
tCO7TG1F8PapspUwtP1MVYwnSlcUfIKdzXOS0xZKBgyMUNGPHgm+F6HmIcr9g+UQ
vIOlCsRnKPZzFBQ9RnbDhxSJITRNrw9FDKZJobq7nMWxM4MphQIDAQABo0IwQDAP
BgNVHRMBAf8EBTADAQH/MA4GA1UdDwEB/wQEAwIBhjAdBgNVHQ4EFgQUTiJUIBiV
5uNu5g/6+rkS7QYXjzkwDQYJKoZIhvcNAQELBQADggEBAGBnKJRvDkhj6zHd6mcY
1Yl9PMWLSn/pvtsrF9+wX3N3KjITOYFnQoQj8kVnNeyIv/iPsGEMNKSuIEyExtv4
NeF22d+mQrvHRAiGfzZ0JFrabA0UWTW98kndth/Jsw1HKj2ZL7tcu7XUIOGZX1NG
Fdtom/DzMNU+MeKNhJ7jitralj41E6Vf8PlwUHBHQRFXGU7Aj64GxJUTFy8bJZ91
8rGOmaFvE7FBcf6IKshPECBV1/MUReXgRPTqh5Uykw7+U0b6LJ3/iyK5S9kJRaTe
pLiaWN0bfVKfjllDiIGknibVb63dDcY3fe0Dkhvld1927jyNxF1WW6LZZm6zNTfl
MrY=
-----END CERTIFICATE-----
)PEM";

// ========================
// MQTT CONFIG
// ========================
// MQTT_BROKER, MQTT_PORT, MQTT_USER, MQTT_PASS come from secrets.h
const char* MQTT_TOPIC  = "home/esp32/presence";
const char* DEVICE_NAME = "hallway_presence_esp32";

// ========================
// DISPLAY PINS
// ========================
#define EPD_CS   5
#define EPD_DC   17
#define EPD_RST  16
#define EPD_BUSY 4

GxEPD2_BW<GxEPD2_290_T94_V2, GxEPD2_290_T94_V2::HEIGHT> display(
  GxEPD2_290_T94_V2(EPD_CS, EPD_DC, EPD_RST, EPD_BUSY)
);

// ========================
// LD2410C RADAR (UART2)
// 16/17 are taken by the eink, so the radar lives on 25/26
// ========================
#define RADAR_RX   25   // <- radar TX
#define RADAR_TX   26   // <- radar RX
#define RADAR_BAUD 256000

ld2410 radar;

// ========================
// TIMING
// ========================
const unsigned long UPDATE_INTERVAL   = 60000;  // BKK refresh, 1 min
const unsigned long PRESENCE_INTERVAL = 5000;   // radar publish, 5 s
unsigned long lastUpdate = 0;
unsigned long lastPresencePublish = 0;

// ========================
// NTP CONFIG
// ========================
const char* NTP_SERVER = "pool.ntp.org";
// Budapest with automatic daylight saving time (#53): CET (UTC+1), CEST (UTC+2) from
// the last Sunday of March 02:00 to the last Sunday of October 03:00. Same rule as
// the tz database's Europe/Budapest.
const char* TIME_ZONE = "CET-1CEST,M3.5.0,M10.5.0/3";
const uint32_t NTP_FIRST_SYNC_MS = 10000;  // wait this long at boot for the first sync

// ========================
// MQTT CLIENT
// ========================
WiFiClient espClient;
PubSubClient mqttClient(espClient);

// ========================
// TRAM DEPARTURE STRUCT
// ========================
struct TramDeparture {
  String route;
  String headsign;
  int minutes;
  bool valid;
};

// ========================
// HELPER FUNCTIONS
// ========================
String removeAccents(String text) {
  text.replace("á", "a");
  text.replace("Á", "A");
  text.replace("é", "e");
  text.replace("É", "E");
  text.replace("í", "i");
  text.replace("Í", "I");
  text.replace("ó", "o");
  text.replace("Ó", "O");
  text.replace("ö", "o");
  text.replace("Ö", "O");
  text.replace("ő", "o");
  text.replace("Ő", "O");
  text.replace("ú", "u");
  text.replace("Ú", "U");
  text.replace("ü", "u");
  text.replace("Ü", "U");
  text.replace("ű", "u");
  text.replace("Ű", "U");
  return text;
}

// ========================
// MQTT FUNCTIONS
// ========================
void connectMQTT() {
  while (!mqttClient.connected()) {
    Serial.print("Connecting to MQTT...");
    char clientId[64];
    snprintf(clientId, sizeof(clientId), "%s_%llx", DEVICE_NAME, ESP.getEfuseMac());

    // Last will: if this node drops off, the broker publishes "offline" (retained)
    // on home/esp32/<device>/status about 1.5 x keepalive later (#13)
    char statusTopic[64];
    snprintf(statusTopic, sizeof(statusTopic), "home/esp32/%s/status", DEVICE_NAME);
    if (mqttClient.connect(clientId, MQTT_USER, MQTT_PASS, statusTopic, 1, true, "offline")) {
      mqttClient.publish(statusTopic, "online", true);
      Serial.println(" connected!");
    } else {
      Serial.print(" failed, rc=");
      Serial.print(mqttClient.state());
      Serial.println(" retrying in 5s");
      delay(5000);
    }
  }
}

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

  StaticJsonDocument<256> doc;
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

  if (mqttClient.publish(MQTT_TOPIC, payload, true)) {
    Serial.print("Presence: ");
    Serial.println(payload);
  } else {
    Serial.println("Presence publish failed!");
  }
}

// ========================
// ICONS
// ========================
void drawTramIcon(int x, int y, int size) {
  // Tram body
  display.drawRect(x, y, size, size * 0.7, GxEPD_BLACK);
  display.drawRect(x + 1, y + 1, size - 2, size * 0.7 - 2, GxEPD_BLACK);

  // Windows
  int windowSize = size / 3;
  display.fillRect(x + 3, y + 3, windowSize, windowSize, GxEPD_BLACK);
  display.fillRect(x + size - windowSize - 3, y + 3, windowSize, windowSize, GxEPD_BLACK);

  // Wheels
  int wheelY = y + size * 0.7;
  display.drawCircle(x + size / 4, wheelY, 3, GxEPD_BLACK);
  display.drawCircle(x + 3 * size / 4, wheelY, 3, GxEPD_BLACK);
}

void drawWifiIcon(int x, int y) {
  int rssi = WiFi.RSSI();
  int bars = (rssi >= -55) ? 3 : (rssi >= -70) ? 2 : (rssi >= -85) ? 1 : 0;
  for (int i = 0; i < bars; i++) {
    display.fillRect(x + i * 6, y - (i + 1) * 6, 4, (i + 1) * 6, GxEPD_BLACK);
  }
}

// ========================
// HARD PANEL CLEAR
// ========================
void hardRefresh() {
  display.setFullWindow();
  display.firstPage();
  do { display.fillScreen(GxEPD_WHITE); } while (display.nextPage());
  delay(100);
  display.firstPage();
  do { display.fillScreen(GxEPD_BLACK); } while (display.nextPage());
  delay(100);
  display.firstPage();
  do { display.fillScreen(GxEPD_WHITE); } while (display.nextPage());
}

// ========================
// FETCH BKK DEPARTURES
// ========================
// True if BKK_URL has includeRouteId=<routeId>, or no includeRouteId at all
bool routeWanted(const String &routeId) {
  String url = bkkUrl;
  if (url.indexOf("includeRouteId=") < 0) return true;
  String key = "includeRouteId=" + routeId;
  for (int i = url.indexOf(key); i >= 0; i = url.indexOf(key, i + 1)) {
    int end = i + key.length();
    if (end == (int)url.length() || url[end] == '&') return true;
  }
  return false;
}

void fetchAllDepartures(TramDeparture departures[], int &count) {
  count = 0;

  WiFiClientSecure tls;
  tls.setCACert(BKK_ROOT_CA);
  HTTPClient http;
  http.begin(tls, bkkUrl);
  http.setTimeout(10000);

  int httpCode = http.GET();
  if (httpCode != HTTP_CODE_OK) {
    Serial.printf("BKK API error: %d\n", httpCode);
    http.end();
    return;
  }

  String payload = http.getString();
  http.end();

  DynamicJsonDocument doc(16384);
  DeserializationError error = deserializeJson(doc, payload);

  if (error) {
    Serial.print("JSON parse error: ");
    Serial.println(error.c_str());
    return;
  }

  if (!doc["data"]["entry"]["stopTimes"].is<JsonArray>()) {
    Serial.println("No stopTimes array");
    return;
  }

  long long currentTime = doc["currentTime"].as<long long>() / 1000;
  JsonArray stopTimes = doc["data"]["entry"]["stopTimes"];
  JsonObject references = doc["data"]["references"];

  for (JsonObject stopTime : stopTimes) {
    if (count >= 10) break; // Max 10 departures

    String tripId = stopTime["tripId"].as<String>();

    if (!references["trips"].containsKey(tripId)) continue;

    String routeId = references["trips"][tripId]["routeId"].as<String>();

    // Only the routes BKK_URL asks for
    if (!routeWanted(routeId)) continue;

    long long departureTime = stopTime["departureTime"].as<long long>();
    long long predictedDepartureTime = stopTime.containsKey("predictedDepartureTime")
                                   ? stopTime["predictedDepartureTime"].as<long long>()
                                   : departureTime;

    int diff = (int)((predictedDepartureTime - currentTime) / 60);
    if (diff < 0) diff = 0;

    String headsign = stopTime["stopHeadsign"].as<String>();
    String routeText = references["routes"][routeId]["shortName"].as<String>();

    // Remove Hungarian accents for display
    headsign = removeAccents(headsign);

    departures[count].route = routeText;
    departures[count].headsign = headsign;
    departures[count].minutes = diff;
    departures[count].valid = true;

    Serial.printf("Found: %s to %s in %d mins\n", routeText.c_str(), headsign.c_str(), diff);
    count++;
  }

  // Sort by departure time (ascending)
  for (int i = 0; i < count - 1; i++) {
    for (int j = 0; j < count - i - 1; j++) {
      if (departures[j].minutes > departures[j + 1].minutes) {
        TramDeparture temp = departures[j];
        departures[j] = departures[j + 1];
        departures[j + 1] = temp;
      }
    }
  }
}

// ========================
// SETUP
// ========================
void setup() {
  Serial.begin(115200);

  display.init();
  display.setRotation(1);
  hardRefresh();

  WiFi.begin(ssid, password);
  int attempts = 0;
  while (WiFi.status() != WL_CONNECTED) {
    delay(300);
    Serial.print(".");
    // Never hang forever: reboot and try again after ~30s
    if (++attempts > 100) {
      Serial.println("\nWiFi timeout, restarting...");
      ESP.restart();
    }
  }
  Serial.println("\nWiFi connected");

  configTzTime(TIME_ZONE, NTP_SERVER);
  // Wait for the first sync so the first screen already has the time (and the BKK
  // certificate check sees a real date). Without it, the next refresh fills it in.
  struct tm timeinfo;
  if (getLocalTime(&timeinfo, NTP_FIRST_SYNC_MS)) {
    Serial.println(&timeinfo, "Time synced: %Y-%m-%d %H:%M:%S %Z");
  } else {
    Serial.println("Time not synced yet, continuing");
  }

  mqttClient.setServer(MQTT_BROKER, MQTT_PORT);
  mqttClient.setKeepAlive(60);

  // Radar last, so nothing blocks while it is already streaming.
  // Bigger buffer than usual: an eink refresh blocks the loop for seconds.
  Serial2.setRxBufferSize(2048);
  Serial2.begin(RADAR_BAUD, SERIAL_8N1, RADAR_RX, RADAR_TX);
  delay(500);
  while (Serial2.available()) Serial2.read();  // drain any junk
  radar.begin(Serial2, false);
  Serial.println("LD2410C initialized");

  fetchAndRender();
}

// ========================
// LOOP
// ========================
void loop() {
  unsigned long now = millis();

  if (now >= REBOOT_INTERVAL) {
    Serial.println("Scheduled reboot...");
    delay(100); // let serial flush
    ESP.restart();
  }

  // WiFi watchdog
  if (WiFi.status() != WL_CONNECTED) {
    Serial.println("WiFi dropped, reconnecting...");
    WiFi.begin(ssid, password);
    int attempts = 0;
    while (WiFi.status() != WL_CONNECTED) {
      delay(300);
      Serial.print(".");
      if (++attempts > 100) ESP.restart();
    }
    Serial.println("\nWiFi reconnected");
  }

  if (!mqttClient.connected()) {
    connectMQTT();
  }
  mqttClient.loop();

  radar.read();

  if (now - lastPresencePublish >= PRESENCE_INTERVAL) {
    lastPresencePublish = now;
    publishPresence();
  }

  if (now - lastUpdate > UPDATE_INTERVAL) {
    lastUpdate = now;
    fetchAndRender();
    // The eink refresh blocked the loop for a while; clear the backlog
    // so the parser resumes on a frame boundary rather than mid-frame.
    while (Serial2.available()) Serial2.read();
  }

  delay(50);
}

// ========================
// FETCH + DRAW DASHBOARD
// ========================
void fetchAndRender() {
  // Fetch all departures and sort by time
  TramDeparture departures[10];
  int count = 0;
  fetchAllDepartures(departures, count);

  // Get current time
  struct tm timeinfo;
  bool hasTime = getLocalTime(&timeinfo);

  // Draw display
  display.setFullWindow();
  display.firstPage();
  do {
    display.fillScreen(GxEPD_WHITE);
    display.setTextColor(GxEPD_BLACK);

    // ==================
    // HEADER SECTION (simplified)
    // ==================
    // Current time
    if (hasTime) {
      display.setFont(&FreeSans9pt7b);
      display.setTextColor(GxEPD_BLACK);
      display.setCursor(5, 15);
      char timeStr[6];
      sprintf(timeStr, "%02d:%02d", timeinfo.tm_hour, timeinfo.tm_min);
      display.print(timeStr);
    }

    // Title
    display.setFont(&FreeSans9pt7b);
    display.setTextColor(GxEPD_BLACK);
    display.setCursor(80, 15);
    display.print("NEXT TRAMS");

    // WiFi indicator
    drawWifiIcon(270, 15);

    // Divider
    display.drawLine(0, 22, 296, 22, GxEPD_BLACK);

    // ==================
    // DEPARTURES SECTION
    // ==================
    int maxDisplay = min(count, 3);

    for (int i = 0; i < maxDisplay; i++) {
      int yPos = 48 + (i * 35);

      if (departures[i].valid) {
        // Tram icon
        drawTramIcon(5, yPos - 15, 18);

        // Route number
        display.setFont(&FreeSans9pt7b);
        display.setTextColor(GxEPD_BLACK);
        display.setCursor(30, yPos);
        display.print(departures[i].route);

        // Separator
        display.setCursor(50, yPos);
        display.print("-");

        // Destination
        display.setCursor(60, yPos);
        String dest = departures[i].headsign;
        if (dest.length() > 16) {
          dest = dest.substring(0, 13) + "...";
        }
        display.print(dest);

        // Departure time - medium size
        display.setFont(&FreeSans12pt7b);
        display.setTextColor(GxEPD_BLACK);
        int mins = departures[i].minutes;
        int xPos = (mins < 10) ? 260 : 245;
        display.setCursor(xPos, yPos);
        display.print(mins);
        display.print("'");
      }
    }

    // ==================
    // NO DATA MESSAGE
    // ==================
    if (count == 0) {
      display.setFont(&FreeSans9pt7b);
      display.setTextColor(GxEPD_BLACK);
      display.setCursor(60, 70);
      display.print("No departures");
    }

  } while (display.nextPage());

  Serial.printf("Display updated! Showing %d departures\n", count);
}
