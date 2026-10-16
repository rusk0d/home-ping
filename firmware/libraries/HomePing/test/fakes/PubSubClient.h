// Fake broker connection for test/test_homeping.cpp (#120)
#pragma once
#include <string>
#include <vector>

struct PubSubClient {
  bool up = false;           // whether the broker accepts connections
  bool isConnected = false;
  int attempts = 0;
  std::string lastClientId, lastWillTopic, lastWillMsg;
  bool lastWillRetain = false;
  struct Pub { std::string topic, payload; bool retain; };
  std::vector<Pub> published;

  bool connected() { return isConnected; }
  bool connect(const char* id, const char*, const char*, const char* willTopic, int,
               bool willRetain, const char* willMsg) {
    ++attempts;
    lastClientId = id; lastWillTopic = willTopic; lastWillMsg = willMsg; lastWillRetain = willRetain;
    isConnected = up;
    return up;
  }
  bool publish(const char* topic, const char* payload, bool retain) {
    published.push_back({topic, payload, retain});
    return isConnected;
  }
  int state() { return up ? 0 : -2; }
};
