"""End-to-end check for the Jotform to Zapier to Airtable chain: submit a unique lead through the public Jotform form the way a visitor does, then poll the
Airtable table until exactly that lead appears, and check the mapped fields. Proves the whole path, not just that each Zap says success.

    JOTFORM_FORM_ID=... AIRTABLE_BASE_ID=... AIRTABLE_TOKEN=... python scripts/e2e_chain.py

Nothing secret is stored in the repo: the token comes from the environment or AIRTABLE_TOKEN_FILE.
"""
import json
import os
import sys
import time
import urllib.parse
import urllib.request
import uuid


def need(name, file_env=None):
    value = os.environ.get(name)
    path = os.environ.get(file_env) if file_env else None
    if not value and path and os.path.exists(path):
        value = open(path, encoding="utf-8").readline().strip()
    if not value:
        sys.exit(f"missing {name}")
    return value


FORM = need("JOTFORM_FORM_ID")
BASE = need("AIRTABLE_BASE_ID")
TOKEN = need("AIRTABLE_TOKEN", "AIRTABLE_TOKEN_FILE")


def airtable():
    req = urllib.request.Request(f"https://api.airtable.com/v0/{BASE}/Leads", headers={"Authorization": f"Bearer {TOKEN}"})
    with urllib.request.urlopen(req, timeout=30) as r:
        return json.load(r)["records"]


def submit(name, email, message):
    data = urllib.parse.urlencode({"formID": FORM, "q1_name": name, "q2_email": email, "q3_message": message}).encode()
    req = urllib.request.Request(f"https://submit.jotform.com/submit/{FORM}", data=data, headers={"User-Agent": "Mozilla/5.0 e2e-chain"})
    with urllib.request.urlopen(req, timeout=30) as r:
        return r.status


def main():
    marker = uuid.uuid4().hex[:8]
    lead = {"name": f"Chain {marker}", "email": f"chain-{marker}@example.com", "message": f"chain check {marker}"}
    status = submit(**lead)
    print("form submitted, HTTP", status)
    deadline = time.time() + 120
    found = None
    while time.time() < deadline and not found:
        time.sleep(3)  # fixed-wait-ok: the poll interval of a loop that checks the real condition (the lead is in Airtable), capped at 120 s
        found = next((r for r in airtable() if r["fields"].get("Email") == lead["email"]), None)
    results = []

    def check(label, ok):
        results.append(ok)
        print(("PASS  " if ok else "FAIL  ") + label)

    check("the lead reached Airtable within 120 seconds", found is not None)
    if found:
        f = found["fields"]
        check("name was mapped through", f.get("Name") == lead["name"])
        check("email was mapped through", f.get("Email") == lead["email"])
        check("message was mapped through", f.get("Message") == lead["message"])
        check("source is the Zap's fixed value", f.get("Source") == "jotform")
    sys.exit(0 if all(results) else 1)


if __name__ == "__main__":
    main()
