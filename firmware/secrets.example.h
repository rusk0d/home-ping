#pragma once

const char* const WIFI_SSID   = "your-ssid";
const char* const WIFI_PASS   = "your-wifi-password";

// TLS (#31): the host name in the broker's certificate (not an IP), port 8883
const char* const MQTT_BROKER = "mqtt.example.com";
const int         MQTT_PORT   = 8883;
const char* const MQTT_USER   = "mqtt-user";
const char* const MQTT_PASS   = "mqtt-password";

// eink_node only: BKK departures query for the tram schedule
const char* const BKK_URL = "https://futar.bkk.hu/api/...";
