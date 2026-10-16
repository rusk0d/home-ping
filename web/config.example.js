// Copy to web/config.js (gitignored) and fill in. Loaded by index.html.
// Only the broker URL: each person signs in on the page with their own read-only
// MQTT account (#99). config.js is readable by anyone who can open the page, so it
// must not contain credentials.
const MQTT_CONFIG = {
  url: 'wss://example.com/mqtt'   // nginx proxies /mqtt to the broker's WebSocket listener
};
