"""Send a test push to every app subscribed to topic "env".

Run on the server from the backend directory (it needs firebase_service_account.json):

    python3 test_push.py                       # "Test" / "Push from the server works"
    python3 test_push.py "Title" "Some text"

Same message format as send_push() in server.py: data-only, android priority high.
It doesn't touch the running backend or the database.
"""
import sys

import firebase_admin
from firebase_admin import credentials, messaging

FIREBASE_CRED = "firebase_service_account.json"

title = sys.argv[1] if len(sys.argv) > 1 else "Test"
body = sys.argv[2] if len(sys.argv) > 2 else "Push from the server works"

firebase_admin.initialize_app(credentials.Certificate(FIREBASE_CRED))
message = messaging.Message(
    data={"title": title, "body": body, "event": "TEST"},
    android=messaging.AndroidConfig(priority="high"),
    topic="env",
)
print("sent:", messaging.send(message))
