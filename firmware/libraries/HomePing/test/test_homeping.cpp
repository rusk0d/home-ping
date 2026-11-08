// Host-side test of HomePing.h (#120): g++ -std=c++17 -I fakes -I ../src test_homeping.cpp
#include <cassert>
#include <cstdio>
#include "HomePing.h"

static int failures = 0;
#define CHECK(label, cond) do { if (cond) std::printf("PASS %s\n", label); \
  else { std::printf("FAIL %s\n", label); ++failures; } } while (0)

int main() {
  PubSubClient mqtt;

  // Broker down: one attempt, then return at once (no waiting in the loop)
  fakeMillis = 100000;
  bool ok = hpConnectMQTT(mqtt, "node1", "u", "p");
  CHECK("broker down: returns false", !ok);
  CHECK("broker down: one attempt", mqtt.attempts == 1);
  CHECK("never waits (no delay)", fakeDelayed == 0);
  CHECK("buffer raised for the presence message with heap fields", mqtt.getBufferSize() == HP_MQTT_BUFFER);

  // Calls inside the retry window don't hit the broker
  for (int i = 0; i < 100; ++i) { fakeMillis += 40; hpConnectMQTT(mqtt, "node1", "u", "p"); }
  CHECK("within 5 s: no new attempt", mqtt.attempts == 1 && fakeMillis - 100000 == 4000);
  fakeMillis = 100000 + 4999; hpConnectMQTT(mqtt, "node1", "u", "p");
  CHECK("at 4.999 s: still no attempt", mqtt.attempts == 1);
  fakeMillis = 100000 + 5000; hpConnectMQTT(mqtt, "node1", "u", "p");
  CHECK("at 5 s: second attempt", mqtt.attempts == 2);
  CHECK("still never waits", fakeDelayed == 0);

  // Broker back: the next due attempt connects, sets the last will and says online
  mqtt.up = true;
  fakeMillis += 1000; ok = hpConnectMQTT(mqtt, "node1", "u", "p");
  CHECK("broker back, inside the window: waits for the next slot", !ok && mqtt.attempts == 2);
  fakeMillis += 5000; ok = hpConnectMQTT(mqtt, "node1", "u", "p");
  CHECK("broker back: connects", ok && mqtt.attempts == 3);
  CHECK("last will: offline, retained, on the status topic",
        mqtt.lastWillTopic == "home/esp32/node1/status" && mqtt.lastWillMsg == "offline" && mqtt.lastWillRetain);
  CHECK("client id = device + MAC", mqtt.lastClientId == "node1_abcdef");
  CHECK("publishes online, retained", !mqtt.published.empty() &&
        mqtt.published.back().topic == "home/esp32/node1/status" &&
        mqtt.published.back().payload == "online" && mqtt.published.back().retain);

  // Connected: no attempt at all
  ok = hpConnectMQTT(mqtt, "node1", "u", "p");
  CHECK("connected: returns true without an attempt", ok && mqtt.attempts == 3);

  // Dropped connection: reconnects on the next due attempt
  mqtt.isConnected = false;
  fakeMillis += 5000; ok = hpConnectMQTT(mqtt, "node1", "u", "p");
  CHECK("dropped: reconnects", ok && mqtt.attempts == 4);
  CHECK("buffer set once, not on every attempt", mqtt.bufferResizes == 1);

  // millis() wrap: the retry window still works (unsigned subtraction)
  mqtt.up = false; mqtt.isConnected = false;
  fakeMillis = 0xFFFFFFFFul - 1000; hpConnectMQTT(mqtt, "node1", "u", "p");
  int before = mqtt.attempts;
  fakeMillis = 2000;   // wrapped, 3 s later
  hpConnectMQTT(mqtt, "node1", "u", "p");
  CHECK("across the millis() wrap: no early attempt", mqtt.attempts == before);
  fakeMillis = 4500;   // 5.5 s after the last attempt
  hpConnectMQTT(mqtt, "node1", "u", "p");
  CHECK("across the millis() wrap: attempts after 5 s", mqtt.attempts == before + 1);

  // WiFi lost after boot (#383): never blocks, nudges every 30 s, restarts only after 10 min
  hpConnectWiFi("ssid", "pass");
  CHECK("boot connect turns auto-reconnect on", WiFi.autoReconnect);
  fakeDelayed = 0; ESP.restarts = 0; WiFi.reconnects = 0;
  fakeMillis = 0xFFFFFFFFul - 60000;   // the outage spans the millis() wrap
  CHECK("connected: up", hpWiFiWatchdog());
  WiFi.st = WL_DISCONNECTED;
  bool up = false;
  for (int i = 0; i < 9 * 60 * 20; ++i) { up = up || hpWiFiWatchdog(); fakeMillis += 50; }   // 9 min of 50 ms loops
  CHECK("9 min down: never up, no restart", !up && ESP.restarts == 0);
  CHECK("...and never waits", fakeDelayed == 0);
  CHECK("...nudged about every 30 s", WiFi.reconnects >= 17 && WiFi.reconnects <= 18);
  WiFi.st = WL_CONNECTED;
  CHECK("back: up again", hpWiFiWatchdog());
  WiFi.st = WL_DISCONNECTED;
  fakeMillis += 1000; hpWiFiWatchdog();   // a new outage starts its own clock
  uint32_t lost = fakeMillis;
  while ((uint32_t)(fakeMillis - lost) < HP_WIFI_RESTART_MS - 50) { hpWiFiWatchdog(); fakeMillis += 50; }
  CHECK("just under 10 min: no restart", ESP.restarts == 0);
  fakeMillis = lost + HP_WIFI_RESTART_MS; hpWiFiWatchdog();
  CHECK("10 min without WiFi: restarts", ESP.restarts == 1);
  WiFi.st = WL_CONNECTED; hpWiFiWatchdog();

  // Uptime from the 64-bit timer (#8)
  fakeTimerUs = 60LL * 86400 * 1000000;   // 60 days
  CHECK("uptime past the old 49.7-day wrap", hpUptimeSeconds() == 60u * 86400);

  std::printf("%s\n", failures ? "FAILED" : "ALL PASSED");
  return failures ? 1 : 0;
}
