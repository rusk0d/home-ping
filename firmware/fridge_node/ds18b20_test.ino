#include <OneWire.h>
#include <DallasTemperature.h>

#define ONE_WIRE_PIN 4   // DATA line on GPIO4

OneWire oneWire(ONE_WIRE_PIN);
DallasTemperature sensors(&oneWire);

void printAddress(DeviceAddress addr) {
  for (uint8_t i = 0; i < 8; i++) {
    if (addr[i] < 16) Serial.print("0");
    Serial.print(addr[i], HEX);
  }
}

void setup() {
  Serial.begin(115200);
  delay(1000);
  sensors.begin();

  int count = sensors.getDeviceCount();
  Serial.print("Sensors found: ");
  Serial.println(count);

  for (int i = 0; i < count; i++) {
    DeviceAddress addr;
    if (sensors.getAddress(addr, i)) {
      Serial.print("  #");
      Serial.print(i);
      Serial.print(" address: ");
      printAddress(addr);
      Serial.println();
    }
  }
  if (count == 0) {
    Serial.println("No sensors found. Check wiring and the pull-up resistor.");
  }
}

void loop() {
  sensors.requestTemperatures();

  int count = sensors.getDeviceCount();
  for (int i = 0; i < count; i++) {
    float t = sensors.getTempCByIndex(i);
    Serial.print("Sensor #");
    Serial.print(i);
    Serial.print(": ");
    if (t == DEVICE_DISCONNECTED_C) {
      Serial.println("read error");
    } else {
      Serial.print(t, 2);
      Serial.println(" C");
    }
  }
  Serial.println("---");
  delay(2000);
}
