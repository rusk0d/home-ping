// Page texts in English and Hungarian, loaded by index.html before app.js.
// The language is the one picked with the header button (kept in localStorage), else
// Hungarian when the browser prefers it, else English. Static texts in index.html carry
// data-i18n="key"; app.js uses t(key, vars). {name} in a text is replaced from vars.
const STRINGS = {
  en: {
    switchTo: 'Magyar',
    subtitle: 'Live readings over MQTT',
    signIn: 'Sign in', user: 'User', password: 'Password',
    remember: 'Remember me on this device', logOut: 'Log out',
    wrongLogin: 'Wrong user name or password.',
    temperature: 'Temperature', humidity: 'Humidity', pressure: 'Pressure',
    occupancyToday: 'Occupancy today', noData: 'no data',
    devices: 'Devices', recentReadings: 'Recent readings',
    colReceived: 'Received', colSource: 'Source', colTemp: 'Temp', colHum: 'Humidity', colPres: 'Pressure',
    firstMessage: 'Waiting for the first message…',
    waiting: 'waiting…', lastKnown: 'last known value', justNow: 'just now',
    'ago.s': '{n}s ago', 'ago.m': '{n}m ago', 'ago.h': '{n}h ago',
    'noDataFor.s': 'no data for {n}s', 'noDataFor.m': 'no data for {n}m', 'noDataFor.h': 'no data for {n}h',
    'uptime.d': '{d}d {h}h', 'uptime.h': '{h}h {m}m', 'uptime.m': '{m}m {s}s',
    connecting: 'Connecting…', live: 'Live', reconnecting: 'Reconnecting…', offline: 'Offline',
    connError: 'Connection error', signedOut: 'Signed out', notConfigured: 'Not configured',
    co2Fresh: 'Fresh', co2Ok: 'OK', co2Stuffy: 'Stuffy – open a window', co2Poor: 'Poor – ventilate now',
    humVeryDry: 'Very dry', humDry: 'Dry', humComfortable: 'Comfortable', humHumid: 'Humid', humVeryHumid: 'Very humid',
    tempCold: 'Cold', tempCool: 'Cool', tempComfortable: 'Comfortable', tempWarm: 'Warm', tempHot: 'Hot',
    mouldRisk: 'Mould risk', dewPoint: 'dew point {v} °C', seaLevel: 'Sea level ≈ 1013 hPa',
    co2Warming: 'Sensor warming up (~3 min)', co2Invalid: 'No valid reading (err {err})',
    warming: 'warming', invalid: 'invalid',
    nodeMain: 'Main node', nodeHallway: 'Hallway radar',
    nodeUp: 'up {v}', nodeOnline: 'online', nodeOffline: 'offline', nodeWaiting: 'waiting for live data',
    occHours: '≈ {v} h', occupied: ' occupied', busiest: ' · busiest {h}:00 ({v}%)',
    occTitle: '{from}:00–{to}:00 · {v}% occupied', occNoRadar: '{h}:00 · no radar data',
    now: 'now', updated: 'updated {age}',
  },
  hu: {
    switchTo: 'English',
    subtitle: 'Élő mérések MQTT-n',
    signIn: 'Bejelentkezés', user: 'Felhasználó', password: 'Jelszó',
    remember: 'Megjegyzés ezen az eszközön', logOut: 'Kijelentkezés',
    wrongLogin: 'Hibás felhasználónév vagy jelszó.',
    temperature: 'Hőmérséklet', humidity: 'Páratartalom', pressure: 'Légnyomás',
    occupancyToday: 'Jelenlét ma', noData: 'nincs adat',
    devices: 'Eszközök', recentReadings: 'Legutóbbi mérések',
    colReceived: 'Érkezett', colSource: 'Forrás', colTemp: 'Hőm.', colHum: 'Pára', colPres: 'Légnyomás',
    firstMessage: 'Várakozás az első üzenetre…',
    waiting: 'várakozás…', lastKnown: 'utolsó ismert érték', justNow: 'épp most',
    'ago.s': '{n} másodperce', 'ago.m': '{n} perce', 'ago.h': '{n} órája',
    'noDataFor.s': '{n} másodperce nincs adat', 'noDataFor.m': '{n} perce nincs adat', 'noDataFor.h': '{n} órája nincs adat',
    'uptime.d': '{d} nap {h} óra', 'uptime.h': '{h} óra {m} perc', 'uptime.m': '{m} perc {s} mp',
    connecting: 'Kapcsolódás…', live: 'Élő', reconnecting: 'Újrakapcsolódás…', offline: 'Nincs kapcsolat',
    connError: 'Kapcsolódási hiba', signedOut: 'Kijelentkezve', notConfigured: 'Nincs beállítva',
    co2Fresh: 'Friss', co2Ok: 'Megfelelő', co2Stuffy: 'Állott – nyiss ablakot', co2Poor: 'Rossz – szellőztess most',
    humVeryDry: 'Nagyon száraz', humDry: 'Száraz', humComfortable: 'Kellemes', humHumid: 'Párás', humVeryHumid: 'Nagyon párás',
    tempCold: 'Hideg', tempCool: 'Hűvös', tempComfortable: 'Kellemes', tempWarm: 'Meleg', tempHot: 'Forró',
    mouldRisk: 'Penészveszély', dewPoint: 'harmatpont {v} °C', seaLevel: 'Tengerszinten ≈ 1013 hPa',
    co2Warming: 'Az érzékelő bemelegszik (~3 perc)', co2Invalid: 'Nincs érvényes mérés (hiba: {err})',
    warming: 'bemelegszik', invalid: 'érvénytelen',
    nodeMain: 'Fő egység', nodeHallway: 'Előszobai radar',
    nodeUp: 'fut: {v}', nodeOnline: 'elérhető', nodeOffline: 'nem elérhető', nodeWaiting: 'várakozás élő adatra',
    occHours: '≈ {v} óra', occupied: ' jelenlét', busiest: ' · csúcs {h}:00 ({v}%)',
    occTitle: '{from}:00–{to}:00 · {v}% jelenlét', occNoRadar: '{h}:00 · nincs radaradat',
    now: 'most', updated: 'frissítve {age}',
  },
};

const LANG_KEY = 'homeping.lang';
let LANG = (() => {
  try {
    const saved = localStorage.getItem(LANG_KEY);
    if (STRINGS[saved]) return saved;
  } catch (e) { /* storage blocked: fall back to the browser's language */ }
  const prefs = navigator.languages || [navigator.language || ''];
  return prefs.some(l => /^hu\b/i.test(l)) ? 'hu' : 'en';
})();

function t(key, vars) {
  const s = STRINGS[LANG][key] ?? STRINGS.en[key] ?? key;
  return vars ? s.replace(/\{(\w+)\}/g, (m, k) => (k in vars ? vars[k] : m)) : s;
}

// Decimal comma in Hungarian
function localNum(text) {
  return LANG === 'hu' ? text.replace('.', ',') : text;
}

function applyStaticTexts() {
  document.documentElement.lang = LANG;
  document.querySelectorAll('[data-i18n]').forEach(el => { el.textContent = t(el.dataset.i18n); });
}

function setLang(lang) {
  LANG = lang;
  try { localStorage.setItem(LANG_KEY, lang); } catch (e) {}
  applyStaticTexts();
}

applyStaticTexts();
