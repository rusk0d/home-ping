"""Pushes go through one worker thread (#464): a slow FCM send is reported as failed after
PUSH_WAIT instead of holding the caller, and a push still waiting behind it is dropped."""
import threading
import time

from support import check


def test_issue464_push_worker(env):
    import server, alerts
    server.prepare()
    sent, release = [], threading.Event()

    def fake_send(message):
        sent.append(message.data["title"])
        if message.data["title"] == "slow":
            release.wait(5)
        return "id"
    alerts.messaging.send = fake_send
    alerts.messaging.Message = lambda data, android, topic: type("M", (), {"data": data})()

    check("a normal push is confirmed", alerts.send_push("fast", "b") is True)
    check("sent on the worker thread", sent == ["fast"])

    alerts.PUSH_WAIT = 0.2
    t0 = time.time()
    check("a slow push counts as failed", alerts.send_push("slow", "b") is False)
    check("the next one, waiting behind it, too", alerts.send_push("queued", "b") is False)
    check("the callers waited about PUSH_WAIT each, not for FCM", time.time() - t0 < 1.5)
    release.set()
    alerts.PUSH_WAIT = 3
    check("the worker goes on afterwards", alerts.send_push("after", "b") is True)
    check("the abandoned one was never sent", sent == ["fast", "slow", "after"])

    alerts.messaging.send = lambda message: (_ for _ in ()).throw(RuntimeError("fcm down"))
    check("a failing send is still False", alerts.send_push("err", "b") is False)
