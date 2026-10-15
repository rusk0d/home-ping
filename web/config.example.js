// Copy to web/config.js (gitignored) and fill in. Loaded by index.html.
// Use the read-only MQTT user: the page only subscribes, and config.js is
// readable by anyone who can open the page.
const MQTT_CONFIG = {
  url: 'wss://example.com/mqtt',   // nginx proxies /mqtt to the broker's WebSocket listener
  username: 'web',
  password: 'web-password'
};
