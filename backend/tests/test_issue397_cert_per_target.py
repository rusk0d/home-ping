"""#397: the certificate watchdog keeps one alert per target, so two hosts on the same port
don't end each other's alert."""

import time

from support import check


def test_issue397_cert_per_target(env):
    import server
    import alerts, server_checks
    server.prepare()
    m = alerts.messaging

    def pushes():
        out = [(c.kwargs["data"]["device"], c.kwargs["data"]["title"]) for c in m.Message.call_args_list
               if c.kwargs.get("data", {}).get("event") == "WATCHDOG"]
        m.Message.reset_mock()
        return out

    days = {"a.test": 60, "b.test": 60}
    server_checks.cert_expiry = lambda host, port, timeout=10: time.time() + days[host] * 86400
    server_checks.CERT_CHECK = ["a.test:443", "b.test:443"]

    def run():
        server_checks._certs_checked = 0   # skip the hourly limit
        server_checks.check_certs()

    run()
    check("both fine: no push", pushes() == [])
    days["a.test"] = 10
    run()
    check("a expires soon: its own alert", pushes() == [("cert_a.test_443", "Certificate expires soon")])
    run()
    run()
    check("b being fine doesn't end a's alert", pushes() == [])
    days["a.test"] = 89
    run()
    check("a renewed: its own all-clear", pushes() == [("cert_a.test_443", "Certificate renewed")])
