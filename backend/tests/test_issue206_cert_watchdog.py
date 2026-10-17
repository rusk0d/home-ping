"""#206: watchdog push before a TLS certificate expires.
"""

from support import check


def test_issue206_cert_watchdog(env):
    import shutil, socket, ssl, subprocess, threading, time
    import pytest
    import server
    import store, alerts, server_checks
    server.prepare()
    m = alerts.messaging

    def pushes():
        out = [(c.kwargs["data"]["device"], c.kwargs["data"]["title"]) for c in m.Message.call_args_list
               if c.kwargs.get("data", {}).get("event") == "WATCHDOG"]
        m.Message.reset_mock()
        return out

    calls = []
    expiry = {"value": None}
    def fake_expiry(host, port, timeout=10):
        calls.append((host, port))
        if isinstance(expiry["value"], Exception):
            raise expiry["value"]
        return expiry["value"]
    real_expiry = server_checks.cert_expiry
    server_checks.cert_expiry = fake_expiry

    def run(days=None):
        if days is not None:
            expiry["value"] = time.time() + days * 86400
        server_checks._certs_checked = 0   # skip the hourly limit
        server_checks.check_certs()

    # No CERT_CHECK (dev, tests): nothing is checked
    run(60)
    check("no targets: no check", calls == [] and pushes() == [])

    server_checks.CERT_CHECK = ["web.test:443"]
    run(60)
    check("valid for 60 days: checked, no push", calls == [("web.test", 443)] and pushes() == [])
    server_checks.check_certs()
    check("hourly: no second check within the hour", len(calls) == 1)

    run(10)
    check("10 days left: 'Certificate expires soon'", pushes() == [("cert_443", "Certificate expires soon")])
    run(9)
    check("once per excursion", pushes() == [])
    run(89)
    check("renewed: 'Certificate renewed'", pushes() == [("cert_443", "Certificate renewed")])
    check("flag cleared", store.get_state("watchdog:cert_443") == "0")

    expiry["value"] = "certificate has expired"
    run()
    check("fails verification: 'Certificate problem'", pushes() == [("cert_443", "Certificate problem")])
    expiry["value"] = ConnectionRefusedError("down")
    run()
    check("unreachable: no push, state kept", pushes() == [] and store.get_state("watchdog:cert_443") == "1")
    run(89)
    check("...and 'renewed' once it's fine", pushes() == [("cert_443", "Certificate renewed")])

    # Quiet hours postpone it, like the other watchdogs
    store.set_state("quiet_hours", "1"); store.set_state("quiet_start", "00:00"); store.set_state("quiet_end", "23:59")
    run(5)
    check("quiet hours: postponed", pushes() == [] and store.get_state("watchdog:cert_443") == "0")
    store.set_state("quiet_hours", "0")
    run(5)
    check("after quiet hours: pushed", pushes() == [("cert_443", "Certificate expires soon")])

    # The real check against a local TLS server with a self-signed certificate: untrusted,
    # so it returns the reason instead of a date; a closed port raises OSError
    server_checks.cert_expiry = real_expiry
    if not shutil.which("openssl"):
        pytest.skip("openssl not installed")
    subprocess.run(["openssl", "req", "-x509", "-newkey", "rsa:2048", "-nodes", "-keyout", "k.pem",
                    "-out", "c.pem", "-days", "1", "-subj", "/CN=localhost"], check=True, capture_output=True)
    ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
    ctx.load_cert_chain("c.pem", "k.pem")
    listener = socket.create_server(("127.0.0.1", 0))
    port = listener.getsockname()[1]
    def serve():
        conn, _ = listener.accept()
        try:
            ctx.wrap_socket(conn, server_side=True).close()
        except (ssl.SSLError, OSError):
            pass   # the client aborts the handshake on the untrusted certificate
    t = threading.Thread(target=serve); t.start()
    reason = server_checks.cert_expiry("localhost", port, timeout=5)
    t.join(); listener.close()
    check("self-signed: reason string", isinstance(reason, str) and "self" in reason.lower())
    try:
        server_checks.cert_expiry("127.0.0.1", port, timeout=2)
        check("closed port raises OSError", False)
    except OSError:
        check("closed port raises OSError", True)
