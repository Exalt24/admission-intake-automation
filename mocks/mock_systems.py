"""Three tiny stand-ins for the systems the workflow talks to, so the automation can be run and tested with nothing cloud-side.

System A and system B are the two places a care coordinator used to re-key every admission. The dead-letter receiver is where a
failed hand-off is parked. Each one dedupes on the Idempotency-Key header, the way a real system should, and can be told to fail
its next N requests so a test can prove the retry and dead-letter paths.

    python mocks/mock_systems.py            # listens on 7801 (A), 7802 (B), 7803 (dead letter)

Control endpoints on every port:  POST /_reset   POST /_fail?n=3   GET /_state
"""
import json
import sys
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlparse


class Store:
    def __init__(self, name):
        self.name = name
        self.lock = threading.Lock()
        self.reset()

    def reset(self):
        with self.lock:
            self.records = {}      # idempotency key -> record
            self.attempts = 0      # every POST that reached us, failed or not
            self.fail_next = 0

    def state(self):
        with self.lock:
            return {"name": self.name, "attempts": self.attempts, "records": list(self.records.values()), "failNext": self.fail_next}


def make_handler(store: Store):
    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *_):
            pass

        def _send(self, code, payload):
            body = json.dumps(payload).encode()
            self.send_response(code)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def do_GET(self):
            if urlparse(self.path).path == "/_state":
                return self._send(200, store.state())
            self._send(404, {"error": "not_found"})

        def do_POST(self):
            parsed = urlparse(self.path)
            length = int(self.headers.get("Content-Length", 0) or 0)
            raw = self.rfile.read(length) if length else b""
            if parsed.path == "/_reset":
                store.reset()
                return self._send(200, {"ok": True})
            if parsed.path == "/_fail":
                n = int(parse_qs(parsed.query).get("n", ["1"])[0])
                with store.lock:
                    store.fail_next = n
                return self._send(200, {"failNext": n})
            expected = "/dead-letter" if store.name == "dead-letter" else "/admissions"
            if parsed.path != expected:
                return self._send(404, {"error": "not_found", "expected": expected})
            with store.lock:
                store.attempts += 1
                if store.fail_next > 0:
                    store.fail_next -= 1
                    return self._send(503, {"error": "temporarily_unavailable"})
                try:
                    record = json.loads(raw or b"{}")
                except json.JSONDecodeError:
                    return self._send(400, {"error": "bad_json"})
                # Dedupe ONLY on the header. A request without one is treated as new every time, which is what a real system does.
                key = self.headers.get("Idempotency-Key") or f"no-key-{store.attempts}"
                duplicate = key in store.records
                if not duplicate:
                    store.records[key] = record
            self._send(200, {"stored": not duplicate, "duplicate": duplicate})

    return Handler


def serve(port, name):
    store = Store(name)
    server = ThreadingHTTPServer(("127.0.0.1", port), make_handler(store))
    threading.Thread(target=server.serve_forever, daemon=True).start()
    return server


if __name__ == "__main__":
    serve(7801, "system-a")
    serve(7802, "system-b")
    serve(7803, "dead-letter")
    print("mock systems up: A=7801 B=7802 dead-letter=7803", flush=True)
    try:
        threading.Event().wait()
    except KeyboardInterrupt:
        sys.exit(0)
