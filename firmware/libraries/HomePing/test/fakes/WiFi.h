// Host-side stand-ins for the Arduino/ESP32 API that HomePing.h uses (#120).
// Only for test/test_homeping.cpp; never built for the board.
#pragma once
#include <cstdint>
#include <cstdio>
#include <cstdarg>
#include <string>

inline unsigned long fakeMillis = 0;
inline unsigned long fakeDelayed = 0;     // total ms spent in delay()
inline unsigned long millis() { return fakeMillis; }
inline void delay(unsigned long ms) { fakeDelayed += ms; fakeMillis += ms; }

struct FakeSerial {
  void print(const char*) {}
  void println(const char* = "") {}
  template <typename T> void println(const T&) {}
  void printf(const char*, ...) {}
};
inline FakeSerial Serial;

struct FakeESP {
  int restarts = 0;
  uint64_t getEfuseMac() { return 0xABCDEFull; }
  void restart() { ++restarts; }
};
inline FakeESP ESP;

enum { WL_CONNECTED = 3, WL_DISCONNECTED = 6 };
enum { WIFI_STA = 1 };
struct FakeWiFi {
  int st = WL_CONNECTED;
  void mode(int) {}
  void begin(const char*, const char*) {}
  int status() { return st; }
  const char* localIP() { return "10.0.0.2"; }
  int RSSI() { return -60; }
};
inline FakeWiFi WiFi;
