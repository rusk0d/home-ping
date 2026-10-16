"""#61: constant-time credentials check; fail2ban filter matches API 401s only.
"""

from support import BACKEND, check


def test_issue61_api_auth(env):
    backend = str(BACKEND)
    import os, sys, re, base64, tempfile, configparser
    from unittest import mock
    os.environ.update(DASH_USER="user", DASH_PASS="pässwörd", MQTT_USER="m", MQTT_PASS="m")
    import server, api
    server.prepare()

    check("right login", api.credentials_ok("user", "pässwörd"))
    for u, p in [("user", "wrong"), ("wrong", "pässwörd"), ("", ""), (None, None), ("user", "pässwörd "), ("USER", "pässwörd")]:
        check(f"rejected: {u!r}/{p!r}", not api.credentials_ok(u, p))
    with mock.patch.object(api.hmac, "compare_digest", wraps=api.hmac.compare_digest) as cd:
        api.credentials_ok("wrong", "wrong")
    check("both values always compared (no short-circuit)", cd.call_count == 2)

    c = api.app.test_client()
    hdr = lambda u, p: {"Authorization": "Basic " + base64.b64encode(f"{u}:{p}".encode()).decode()}
    check("API: right login -> 200", c.get("/api/occupancy_status", headers=hdr("user", "pässwörd")).status_code == 200)
    check("API: wrong password -> 401", c.get("/api/occupancy_status", headers=hdr("user", "nope")).status_code == 401)
    check("API: no login -> 401", c.get("/api/occupancy_status").status_code == 401)

    # fail2ban filter: <HOST> stands for the client address
    cfg = configparser.ConfigParser(interpolation=None)
    cfg.read(os.path.join(backend, "fail2ban", "home-ping-api.conf"), encoding="utf-8")
    rx = re.compile(cfg["Definition"]["failregex"].strip().replace("<HOST>", r"(?P<host>[0-9a-fA-F.:]+)"))
    L = '{ip} - {user} [16/Oct/2026:14:02:11 +0200] "{req}" {status} 37 "-" "okhttp/4.12.0"'
    cases = [
        (L.format(ip="203.0.113.7", user="user", req="GET /api/latest HTTP/1.1", status=401), "203.0.113.7"),
        (L.format(ip="203.0.113.7", user="-", req="POST /api/occupancy_arm HTTP/2.0", status=401), "203.0.113.7"),
        (L.format(ip="2001:db8::1", user="x", req="GET /api/hourly?days=7 HTTP/1.1", status=401), "2001:db8::1"),
        (L.format(ip="203.0.113.7", user="user", req="GET /api/latest HTTP/1.1", status=200), None),
        (L.format(ip="203.0.113.7", user="-", req="GET /api/health HTTP/1.1", status=503), None),
        (L.format(ip="203.0.113.7", user="-", req="GET /index.html HTTP/1.1", status=401), None),
        (L.format(ip="203.0.113.7", user="-", req="GET /mqtt HTTP/1.1", status=401), None),
    ]
    # fail2ban removes the date it found before matching (a real line has "[]" left), so
    # test both forms; the first version of the filter needed text inside the brackets
    cases += [(re.sub(r"\[[^\]]*\]", "[]", line, count=1), host) for line, host in cases]
    cases.append(('94.21.99.133 - wrong [] "GET /api/occupancy_status HTTP/1.1" 401 23 "-" "curl/8.21.0"', "94.21.99.133"))
    for line, host in cases:
        m = rx.search(line)
        got = m.group("host") if m else None
        check(f"filter: {line.split(chr(34))[0][-12:]}{line.split(chr(34))[1]} {line.split(chr(34))[2].split()[0]} -> {host or 'no match'}", got == host)
