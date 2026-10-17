// Home Ping dashboard, loaded by index.html. A separate file (not inline) so the page's
// Content-Security-Policy can forbid inline scripts (#168, see web/nginx/).
// Texts: t() from i18n.js (English/Hungarian); what's on screen is rendered again from the
// last data when the language changes. Topics and payloads: see contracts/mqtt.md
const TOPIC_ENV = 'home/esp32/bme280';  // main_node, every 30 s, retained
const TOPIC_CO2 = 'home/esp32/co2';     // main_node (since the node merge, #57), every 60 s, retained
const TOPIC_OCC = 'home/backend/occupancy_hourly'; // backend, every 5 min, retained
const TOPIC_HIST = 'home/backend/history_24h';     // backend, every 5 min, retained (#132)
const TOPIC_PRESENCE = 'home/esp32/presence';  // both radar nodes, every 5 s, retained
const TOPIC_STATUS = 'home/esp32/+/status';    // "online" / last will "offline", retained (#81)
const STATUS_RE = /^home\/esp32\/([^/]+)\/status$/;
// Known nodes. A node counts as online while live messages keep coming;
// the limit is ~3 missed publishes of its most frequent topic.
const NODES = {   // name: an i18n.js key
  home_ping_esp32:        { name: 'nodeMain',    staleAfter: 30 },   // radar every 5 s
  hallway_presence_esp32: { name: 'nodeHallway', staleAfter: 30 },   // radar every 5 s
};
const STALE_AFTER = { env: 90, co2: 180, occ: 900 }; // seconds, ~3 missed publishes
const DAY_MS = 24 * 3600e3;
const MAX_POINTS = 3000;  // live sparkline points kept per metric (~24 h of 30 s readings)
const MAX_HISTORY = 20;   // rows in the recent readings table

const $ = id => document.getElementById(id);
// Sparklines: hourly averages from history_24h (plotted mid-hour) followed by the live
// readings since; both as { t: ms, v }
const series = { temp: [], hum: [], pres: [], co2: [] };       // live
const hourly = { temp: [], hum: [], pres: [], co2: [] };       // from history_24h
const lastSeen = { env: null, co2: null, occ: null };   // { at: ms, retained: bool }
const history = [];

// ---------- formatting ----------
const fmt = (v, d) => (typeof v === 'number' && isFinite(v)) ? localNum(v.toFixed(d)) : '--';

function fmtUptime(s) {
  if (typeof s !== 'number') return '--';
  const d = Math.floor(s / 86400), h = Math.floor(s % 86400 / 3600), m = Math.floor(s % 3600 / 60);
  if (d) return t('uptime.d', { d, h });
  if (h) return t('uptime.h', { h, m });
  return t('uptime.m', { m, s: Math.floor(s % 60) });
}

// key: 'ago' ("5m ago") or 'noDataFor' ("no data for 5m")
function fmtAge(info, key = 'ago') {
  if (!info) return t('waiting');
  const s = Math.round((Date.now() - info.at) / 1000);
  if (key === 'ago' && info.retained && s < 5) return t('lastKnown');
  if (key === 'ago' && s < 5) return t('justNow');
  if (s < 60) return t(key + '.s', { n: s });
  if (s < 3600) return t(key + '.m', { n: Math.floor(s / 60) });
  return t(key + '.h', { n: Math.floor(s / 3600) });
}

function chip(text, color) {
  const el = document.createElement('span');
  el.className = 'chip';
  el.style.setProperty('--chip', `var(--${color})`);
  el.textContent = text;
  return el;
}

function setSub(id, text, color) {
  const el = $(id);
  el.replaceChildren(color ? chip(text, color) : document.createTextNode(text || ''));
}

// ---------- levels ----------
// Bands defined in contracts/api.md ("Level bands"); keep in sync with android ui/Levels.kt
function co2Level(ppm) {
  if (ppm < 800)  return [t('co2Fresh'), 'good'];
  if (ppm < 1200) return [t('co2Ok'), 'ok'];
  if (ppm < 2000) return [t('co2Stuffy'), 'poor'];
  return [t('co2Poor'), 'bad'];
}
// Dew point in °C (#177), Magnus formula with the Sonntag constants; contracts/api.md
// "Dew point", android ui/Levels.kt dewPoint. Null for a humidity of 0 or less.
function dewPoint(t, rh) {
  if (!(rh > 0)) return null;
  const g = Math.log(rh / 100) + 17.62 * t / (243.12 + t);
  return 243.12 * g / (17.62 - g);
}
function humLevel(h) {
  if (h < 30) return [t('humVeryDry'), 'poor'];
  if (h < 40) return [t('humDry'), 'ok'];
  if (h <= 60) return [t('humComfortable'), 'good'];
  if (h <= 70) return [t('humHumid'), 'ok'];
  return [t('humVeryHumid'), 'poor'];
}
function tempLevel(c) {
  if (c < 18) return [t('tempCold'), 'hum'];
  if (c < 20) return [t('tempCool'), 'ok'];
  if (c <= 24) return [t('tempComfortable'), 'good'];
  if (c <= 27) return [t('tempWarm'), 'ok'];
  return [t('tempHot'), 'bad'];
}

// ---------- sparklines ----------
function push(key, v) {
  if (typeof v !== 'number' || !isFinite(v)) return;
  const s = series[key], now = Date.now();
  s.push({ t: now, v });
  while (s.length && (s.length > MAX_POINTS || s[0].t < now - DAY_MS)) s.shift();
}

// history_24h (#132): replaces the hourly points; live points from before the end of
// its last hour are dropped, the hourly average covers them
const SPARKS = { temp: ['temp', 1], hum: ['humidity', 0], pres: ['pressure', 0], co2: ['co2', 0] };
// Mould risk (#177), the same rule as /api/air `mould_risk` (contracts/api.md): each of the
// last MOULD_HOURS complete hours before the topic's `updated` averaged above 60 %RH
const MOULD_HUMIDITY = 60, MOULD_HOURS = 3;
let mouldRisk = false;
function mouldRiskFrom(updated, hours) {
  const now = Date.parse(updated);
  if (!isFinite(now)) return false;
  const current = Math.floor(now / 3600e3) * 3600e3;   // start of the incomplete hour
  for (let i = 1; i <= MOULD_HOURS; i++) {
    const h = hours.find(x => x.t === current - i * 3600e3);
    if (!h || !(h.humidity > MOULD_HUMIDITY)) return false;
  }
  return true;
}

function onHistory(d) {
  if (!Array.isArray(d.hours)) return;
  const hours = d.hours.map(h => ({ ...h, t: Date.parse(h.start) })).filter(h => isFinite(h.t));
  mouldRisk = mouldRiskFrom(d.updated, hours);
  showHumSub();
  const end = hours.length ? hours[hours.length - 1].t + 3600e3 : -Infinity;
  for (const [key, [field, digits]] of Object.entries(SPARKS)) {
    hourly[key] = hours.filter(h => typeof h[field] === 'number')
                       .map(h => ({ t: h.t + 1800e3, v: h[field] }));
    series[key] = series[key].filter(p => p.t >= end);
    drawSpark(key, digits);
  }
}

function drawSpark(key, digits) {
  const pts = hourly[key].concat(series[key]);
  const svg = $(`${key}-spark`);
  if (pts.length < 2) { svg.replaceChildren(); return; }
  const vs = pts.map(p => p.v);
  let min = Math.min(...vs), max = Math.max(...vs);
  if (max - min < 1e-9) { min -= 1; max += 1; }
  // x by time, from the oldest point to the newest
  const t0 = pts[0].t, span = Math.max(pts[pts.length - 1].t - t0, 1);
  const xy = pts.map(p => [
    ((p.t - t0) / span) * 100,
    28 - ((p.v - min) / (max - min)) * 26
  ]);
  const line = 'M' + xy.map(p => p.map(n => n.toFixed(2)).join(',')).join('L');
  const ns = 'http://www.w3.org/2000/svg';
  const area = document.createElementNS(ns, 'path');
  area.setAttribute('class', 'area');
  area.setAttribute('d', `${line}L100,30L0,30Z`);
  const path = document.createElementNS(ns, 'path');
  path.setAttribute('class', 'line');
  path.setAttribute('d', line);
  svg.replaceChildren(area, path);
  $(`${key}-range`).textContent = `${fmt(Math.min(...vs), digits)} – ${fmt(Math.max(...vs), digits)}`;
}

// ---------- message handlers ----------
// The humidity card's line: level chip ("Mould risk" instead while it applies), dew point
let lastEnv = null;
function showHumSub() {
  const d = lastEnv;
  if (!d) return;
  if (mouldRisk) setSub('hum-sub', t('mouldRisk'), 'poor');
  else setSub('hum-sub', ...humLevel(d.humidity));
  const dew = typeof d.temp_c === 'number' ? dewPoint(d.temp_c, d.humidity) : null;
  if (dew !== null) $('hum-sub').append(' ' + t('dewPoint', { v: fmt(dew, 0) }));
}

let lastEnvMsg = null;   // the last env message, to redraw after a language change
function renderEnv(d) {
  $('temp').textContent = fmt(d.temp_c, 1);
  $('hum').textContent = fmt(d.humidity, 0);
  $('pres').textContent = fmt(d.pressure, 0);

  if (typeof d.temp_c === 'number') setSub('temp-sub', ...tempLevel(d.temp_c));
  if (typeof d.humidity === 'number') { lastEnv = d; showHumSub(); }
  setSub('pres-sub', t('seaLevel'));
}

function onEnv(d, retained) {
  lastSeen.env = { at: Date.now(), retained };
  lastEnvMsg = d;
  renderEnv(d);

  push('temp', d.temp_c); push('hum', d.humidity); push('pres', d.pressure);
  drawSpark('temp', 1); drawSpark('hum', 0); drawSpark('pres', 0);

  noteNode(d.device || 'home_ping_esp32', d, retained);
  addHistory('env', d.device, { temp: d.temp_c, hum: d.humidity, pres: d.pressure });
}

let lastCo2Msg = null;
// Returns whether the reading is valid
function renderCo2(d) {
  const valid = d.co2_ok && typeof d.co2 === 'number';
  const needle = $('co2-needle');

  if (valid) {
    $('co2').textContent = d.co2;
    const [text, color] = co2Level(d.co2);
    setSub('co2-sub', text, color);
    $('card-co2').style.setProperty('--accent', `var(--${color})`);
    // Gauge spans 400–3000 ppm; segment stops match co2Level()
    const pct = Math.min(100, Math.max(0, (d.co2 - 400) / 2600 * 100));
    needle.style.left = pct + '%';
    needle.hidden = false;
  } else {
    $('co2').textContent = '--';
    needle.hidden = true;
    setSub('co2-sub', d.warming ? t('co2Warming') : t('co2Invalid', { err: d.err ?? '?' }), 'ok');
  }
  return valid;
}

function onCo2(d, retained) {
  lastSeen.co2 = { at: Date.now(), retained };
  lastCo2Msg = d;
  const valid = renderCo2(d);
  if (valid) { push('co2', d.co2); drawSpark('co2', 0); }

  noteNode(d.device || 'home_ping_esp32', d, retained);
  addHistory('co2', d.device, { co2: valid ? d.co2 : (d.warming ? 'warming' : 'invalid') });
}

// ---------- nodes ----------
// Per node: at = time of the last *live* message (retained replays can be hours old,
// so they don't prove the node is up), uptime/rssi from any message, and the
// retained status topic: "online" on connect, "offline" as the broker's last will.
const nodes = {};

function node(device) {
  if (!nodes[device]) {
    const known = NODES[device] || { name: device, staleAfter: 180 };
    const el = document.createElement('div');
    el.className = 'device';
    const dot = document.createElement('span');
    dot.className = 'dot';
    const name = document.createElement('div');
    name.className = 'name';
    const meta = document.createElement('div');
    meta.className = 'meta';
    const text = document.createElement('div');
    text.append(name, meta);
    el.append(dot, text);
    $('devices').append(el);
    nodes[device] = { ...known, el, nameEl: name, meta, at: null, uptime: null, rssi: null, status: null, statusAt: null };
  }
  return nodes[device];
}

function noteNode(device, d, retained) {
  const n = node(device);
  if (!retained) n.at = Date.now();
  if (typeof d.uptime === 'number') n.uptime = d.uptime;
  if (typeof d.rssi === 'number') n.rssi = d.rssi;
  renderNode(device);
}

function onStatus(device, text) {
  const n = node(device);
  n.status = text.trim();
  n.statusAt = Date.now();
  renderNode(device);
}

// 'on' | 'off' | null (nothing live yet)
function nodeState(n) {
  const now = Date.now();
  const fresh = n.at && (now - n.at) / 1000 <= n.staleAfter;
  if (n.status === 'offline' && !(n.at && n.at > n.statusAt)) return 'off';  // last will, nothing newer
  if (fresh) return 'on';
  if (n.at) return 'off';                    // was live, gone quiet
  if (n.status === 'online') return 'on';    // the broker says it's connected
  return null;
}

function renderNode(device) {
  const n = nodes[device];
  const state = nodeState(n);
  n.el.classList.toggle('on', state === 'on');
  n.el.classList.toggle('off', state === 'off');
  let stateText;
  if (state === 'on') stateText = t('nodeOnline');
  else if (state === 'off') stateText = (n.status === 'offline' && !(n.at && n.at > n.statusAt))
    ? t('nodeOffline') : fmtAge({ at: n.at }, 'noDataFor');
  else stateText = t('nodeWaiting');
  n.nameEl.textContent = NODES[device] ? t(n.name) : n.name;
  const parts = [device];
  if (n.uptime !== null) parts.push(t('nodeUp', { v: fmtUptime(n.uptime) }));
  if (n.rssi !== null) parts.push(`${n.rssi} dBm`);
  parts.push(stateText);
  n.meta.textContent = parts.join(' · ');
}

Object.keys(NODES).forEach(node);   // show the known nodes before any message
Object.keys(nodes).forEach(renderNode);

// ---------- hourly occupancy ----------
const pad2 = n => String(n).padStart(2, '0');
const localDate = d => `${d.getFullYear()}-${pad2(d.getMonth() + 1)}-${pad2(d.getDate())}`;
let occDate = null, lastOccMsg = null;

const occCols = Array.from({ length: 24 }, (_, h) => {
  const col = document.createElement('div');
  col.className = 'col future';
  const bar = document.createElement('div');
  bar.className = 'bar';
  col.append(bar);
  return col;
});
const occLabels = Array.from({ length: 24 }, (_, h) => {
  const s = document.createElement('span');
  s.textContent = h % 3 === 0 ? pad2(h) : '';
  return s;
});
$('occ-bars').append(...occCols);
$('occ-axis').append(...occLabels);

function occColor(v) {
  if (v >= 60) return 'good';
  if (v >= 20) return 'ok';
  if (v > 0) return 'muted';
  return 'faint';
}

function onOcc(d) {
  // `updated` is server local time; assumes the browser is in the same timezone
  const updated = new Date(String(d.updated).replace(' ', 'T'));
  lastSeen.occ = { at: isNaN(updated) ? Date.now() : updated.getTime(), retained: false };
  occDate = d.date;
  lastOccMsg = d;

  const byHour = {};
  (d.hours || []).forEach(e => { byHour[parseInt(e.hour, 10)] = e.occupancy; });
  const hoursSent = Object.keys(byHour).map(Number);
  const current = hoursSent.length ? Math.max(...hoursSent) : -1;

  let sum = 0, peak = null;
  occCols.forEach((col, h) => {
    const v = byHour[h];
    const bar = col.firstChild;
    const has = typeof v === 'number';
    col.classList.toggle('future', h > current);
    col.classList.toggle('nodata', h <= current && !has);
    col.classList.toggle('now', h === current);
    occLabels[h].classList.toggle('now', h === current);
    // Hide hour labels right next to 'now' so they don't collide on narrow screens
    occLabels[h].textContent = h === current ? t('now') : (h % 3 === 0 && Math.abs(h - current) > 1 ? pad2(h) : '');
    if (has) {
      bar.style.height = Math.max(v, 3) + '%';
      bar.style.background = `var(--${occColor(v)})`;
      col.title = t('occTitle', { from: pad2(h), to: pad2(h + 1), v });
      sum += v;
      if (v > 0 && (peak === null || v > byHour[peak])) peak = h;
    } else {
      bar.style.height = '0';
      col.title = h <= current ? t('occNoRadar', { h: pad2(h) }) : '';
    }
  });

  const summary = $('occ-summary');
  const strong = document.createElement('strong');
  strong.textContent = t('occHours', { v: fmt(sum / 100, 1) });
  summary.replaceChildren(strong, t('occupied') + (peak !== null ? t('busiest', { h: pad2(peak), v: byHour[peak] }) : ''));
}

// device: the node that sent it (both readings come from the main node since the merge, #57).
// values: raw numbers (co2 may be 'warming' or 'invalid'), formatted when drawn
function addHistory(source, device, values) {
  history.unshift({ at: new Date(), source, device: device || 'home_ping_esp32', ...values });
  if (history.length > MAX_HISTORY) history.pop();
  renderHistory();
}

function renderHistory() {
  if (!history.length) return;
  const withUnit = (v, d, unit) => v === undefined ? undefined : fmt(v, d) + unit;
  const rows = history.map(e => {
    const tr = document.createElement('tr');
    const src = document.createElement('td');
    const dot = document.createElement('span');
    dot.className = 'src ' + (e.source === 'env' ? 'temp' : 'co2');
    src.append(dot, NODES[e.device] ? t(NODES[e.device].name) : e.device);
    const co2 = typeof e.co2 === 'string' ? t(e.co2) : withUnit(e.co2, 0, ' ppm');
    const cells = [e.at.toLocaleTimeString(LANG === 'hu' ? 'hu' : undefined), null, withUnit(e.temp, 1, ' °C'), withUnit(e.hum, 0, ' %'),
                   withUnit(e.pres, 0, ' hPa'), co2].map((v, i) => {
      if (i === 1) return src;
      const td = document.createElement('td');
      td.textContent = v ?? '—';
      return td;
    });
    tr.append(...cells);
    return tr;
  });
  $('historyBody').replaceChildren(...rows);
}

// ---------- ages / staleness (every second) ----------
function tick() {
  const now = Date.now();
  for (const [kind, cards] of [['env', ['temp', 'hum', 'pres']], ['co2', ['co2']]]) {
    const info = lastSeen[kind];
    const stale = info && (now - info.at) / 1000 > STALE_AFTER[kind];
    cards.forEach(c => {
      $(`${c}-age`).textContent = fmtAge(info);
      $(`card-${c}`).classList.toggle('stale', !!stale);
    });
  }
  Object.keys(nodes).forEach(renderNode);
  // Occupancy: stale if the backend stopped publishing or the data is from another day
  const occ = lastSeen.occ;
  $('occ-age').textContent = occ ? t('updated', { age: fmtAge(occ) }) : t('waiting');
  $('card-occ').classList.toggle('stale',
    !!occ && ((now - occ.at) / 1000 > STALE_AFTER.occ || occDate !== localDate(new Date())));
}
setInterval(tick, 1000);

// ---------- login (#99) ----------
// Each person has their own read-only MQTT account. The login is kept only after the
// broker accepted it: in sessionStorage (this tab) or, with "remember me", localStorage.
const LOGIN_KEY = 'homeping.login';
function store(remember) {
  try { return remember ? localStorage : sessionStorage; } catch (e) { return null; }
}
function loadLogin() {
  for (const remember of [true, false]) {
    try {
      const saved = JSON.parse(store(remember)?.getItem(LOGIN_KEY) || 'null');
      if (saved && saved.username) return { ...saved, remember };
    } catch (e) { /* storage blocked or corrupt: ask again */ }
  }
  return null;
}
function saveLogin(login) {
  clearLogin();
  try { store(login.remember)?.setItem(LOGIN_KEY, JSON.stringify({ username: login.username, password: login.password })); } catch (e) {}
}
function clearLogin() {
  for (const remember of [true, false]) { try { store(remember)?.removeItem(LOGIN_KEY); } catch (e) {} }
}

// message: an i18n.js key
let loginError = '';
function showLogin(message) {
  $('login').hidden = false;
  $('who').hidden = true;
  loginError = message || '';
  $('loginError').textContent = loginError && t(loginError);
  setStatus('down', 'signedOut');
  $(message ? 'loginPass' : 'loginUser').focus();
}

// ---------- connection ----------
// text: an i18n.js key
let statusText = '';
function setStatus(state, text) {
  statusText = text;
  $('status').className = 'status ' + state;
  $('statusText').textContent = t(text);
}

// Back off between reconnects (5 s doubling to 30 s). mqtt.js retries every 1 s by
// default, which floods nginx with errors while the broker is down and can get the
// viewer's IP banned by fail2ban.
const RECONNECT_MIN = 5000, RECONNECT_MAX = 30000;
let client = null;

function connect(login) {
  $('login').hidden = true;
  setStatus('', 'connecting');
  let accepted = false;
  const c = client = mqtt.connect(MQTT_CONFIG.url, {
    username: login.username,
    password: login.password,
    reconnectPeriod: RECONNECT_MIN
  });

  c.on('connect', () => {
    if (!accepted) {
      accepted = true;
      saveLogin(login);
      $('whoName').textContent = login.username;
      $('who').hidden = false;
    }
    c.options.reconnectPeriod = RECONNECT_MIN;
    setStatus('live', 'live');
    c.subscribe([TOPIC_ENV, TOPIC_CO2, TOPIC_OCC, TOPIC_HIST, TOPIC_PRESENCE, TOPIC_STATUS], (err, granted) => {
      if (err) console.error('MQTT subscribe failed', err);
      // QoS 128 = refused, e.g. the user's ACL doesn't cover the topic
      (granted || []).filter(g => g.qos === 128).forEach(g => console.warn('Subscription refused by the broker:', g.topic));
    });
  });
  c.on('reconnect', () => {
    c.options.reconnectPeriod = Math.min(c.options.reconnectPeriod * 2, RECONNECT_MAX);
    setStatus('', 'reconnecting');
  });
  c.on('offline', () => { if (client === c) setStatus('down', 'offline'); });
  c.on('error', err => {
    console.error('MQTT error', err);
    // CONNACK 4 = bad user name or password, 5 = not authorized: don't retry, ask again
    if (err && (err.code === 4 || err.code === 5)) {
      c.end(true);
      client = null;
      clearLogin();
      showLogin('wrongLogin');
      return;
    }
    setStatus('down', 'connError');
  });

  c.on('message', (topic, message, packet) => {
    const status = topic.match(STATUS_RE);
    if (status) { onStatus(status[1], message.toString()); return; }
    let data;
    try { data = JSON.parse(message.toString()); }
    catch (e) { console.error('Invalid JSON on', topic, e); return; }
    if (topic === TOPIC_ENV) onEnv(data, packet.retain);
    else if (topic === TOPIC_CO2) onCo2(data, packet.retain);
    else if (topic === TOPIC_OCC) onOcc(data);
    else if (topic === TOPIC_HIST) onHistory(data);
    else if (topic === TOPIC_PRESENCE && data.device) noteNode(data.device, data, packet.retain);
  });
}

$('login').addEventListener('submit', e => {
  e.preventDefault();
  connect({
    username: $('loginUser').value.trim(),
    password: $('loginPass').value,
    remember: $('loginRemember').checked
  });
  $('loginPass').value = '';
});
// Language button: switch, then redraw everything from the last data
$('lang').addEventListener('click', () => {
  setLang(LANG === 'hu' ? 'en' : 'hu');
  if (statusText) $('statusText').textContent = t(statusText);
  if (loginError) $('loginError').textContent = t(loginError);
  if (lastEnvMsg) renderEnv(lastEnvMsg);
  if (lastCo2Msg) renderCo2(lastCo2Msg);
  if (lastOccMsg) onOcc(lastOccMsg);
  for (const [key, [, digits]] of Object.entries(SPARKS)) drawSpark(key, digits);
  renderHistory();
  tick();   // ages and devices
});
$('logout').addEventListener('click', e => {
  e.preventDefault();
  if (client) client.end(true);
  client = null;
  clearLogin();
  showLogin();
});

if (typeof MQTT_CONFIG === 'undefined') {
  $('configError').hidden = false;
  setStatus('down', 'notConfigured');
} else {
  const saved = loadLogin();
  if (saved) connect(saved); else showLogin();
}
