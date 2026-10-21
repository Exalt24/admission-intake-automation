"""End-to-end check for the lead-intake Zap: send a unique lead to the Zap's Catch Hook URL, then poll the receiver until that exact lead shows up,
and fail if it does not arrive or arrives with the wrong fields. This proves the path Zapier -> POST -> Cloudflare Worker -> KV, not just that the Zap says "success".

    ZAP_HOOK_URL=https://hooks.zapier.com/hooks/catch/<account>/<id>/ \
    RECEIVER_URL=https://<worker>.workers.dev  CHAIN_KEY=<shared key>  python scripts/e2e_check.py

Nothing secret is stored in the repo: the hook URL and the key come from the environment (or the two files named below).
"""
import json
import os
import sys
import time
import urllib.request
import uuid


def setting(name, filename=None):
    value = os.environ.get(name)
    if not value and filename and os.path.exists(filename):
        value = open(filename, encoding="utf-8").readline().strip()
    if not value:
        sys.exit(f"missing {name}")
    return value


HOOK = setting("ZAP_HOOK_URL")
RECEIVER = setting("RECEIVER_URL").rstrip("/")
KEY = setting("CHAIN_KEY")


def call(url, body=None, method="GET", headers=None):
    req = urllib.request.Request(url, data=json.dumps(body).encode() if body is not None else None, method=method,
                                 headers={"content-type": "application/json", "user-agent": "e2e-check", **(headers or {})})
    with urllib.request.urlopen(req, timeout=30) as r:
        return json.load(r)


def main():
    marker = uuid.uuid4().hex[:8]
    lead = {"name": f"E2E {marker}", "email": f"e2e-{marker}@example.com", "message": f"end to end check {marker}", "source": "e2e"}
    sent = call(HOOK, lead, "POST")
    print("hook accepted:", sent.get("status"))
    deadline = time.time() + 90
    found = None
    while time.time() < deadline and not found:
        time.sleep(2)  # fixed-wait-ok: the poll interval of a loop that checks the real condition (the lead is in the receiver), capped at 90 s
        records = call(f"{RECEIVER}/records", headers={"x-chain-key": KEY}).get("records", [])
        found = next((r for r in records if r.get("data", {}).get("email") == lead["email"]), None)
    results = []

    def check(name, ok):
        results.append(ok)
        print(("PASS  " if ok else "FAIL  ") + name)

    check("the lead reached the receiver within 90 seconds", found is not None)
    if found:
        data = found["data"]
        check("name was mapped through", data.get("name") == lead["name"])
        check("email was mapped through", data.get("email") == lead["email"])
        check("message was mapped through", data.get("message") == lead["message"])
        check("source is the Zap's fixed value, not the caller's", data.get("source") == "zapier-demo")
        check("the request came from Zapier", found.get("source") == "Zapier")
    sys.exit(0 if all(results) else 1)


if __name__ == "__main__":
    main()
