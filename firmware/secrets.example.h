#pragma once

const char* const WIFI_SSID   = "your-ssid";
const char* const WIFI_PASS   = "your-wifi-password";

// TLS (#31): the host name in the broker's certificate (not an IP), port 8883
const char* const MQTT_BROKER = "mqtt.example.com";
const int         MQTT_PORT   = 8883;
const char* const MQTT_USER   = "mqtt-user";
const char* const MQTT_PASS   = "mqtt-password";

// Over-the-air updates (#79): asked by the Arduino IDE on a network upload
const char* const OTA_PASS    = "ota-password";

// eink_node only: BKK departures query for the tram schedule. Use includeReferences=routes,trips,
// not =true: the node only needs those, and the full references (nearby stops, routes, alerts)
// are ~100 KB, ~1-5 KB without them. More stops: repeat stopId=...; the routes shown are the
// includeRouteId=... ones (filtered on the node), so leave out includeRouteTypes=TRAM when a bus
// is among them (the 223E at BKK_F04126 is BKK_2235).
const char* const BKK_URL = "https://futar.bkk.hu/api/...";
