"""Creates the Airtable base the Zap writes leads into: a base "Lead intake lab" with one table, Leads (Name, Email, Message, Source). Uses the Metadata API,
so it needs a personal access token with schema.bases:write and data.records:write. Idempotent: if a base with that name exists it is reused.

    AIRTABLE_TOKEN=... python scripts/airtable_setup.py <workspace id> [<workspace id> ...]    # tries each id until one accepts the base

Prints the base id and table id. Nothing secret is stored in the repo; the token comes from the environment or AIRTABLE_TOKEN_FILE.
"""
import json
import os
import sys
import urllib.error
import urllib.request

BASE_NAME = "Lead intake lab"


def token():
    value = os.environ.get("AIRTABLE_TOKEN")
    path = os.environ.get("AIRTABLE_TOKEN_FILE")
    if not value and path and os.path.exists(path):
        value = open(path, encoding="utf-8").readline().strip()
    if not value:
        sys.exit("missing AIRTABLE_TOKEN (or AIRTABLE_TOKEN_FILE)")
    return value


def call(method, path, body=None):
    req = urllib.request.Request("https://api.airtable.com" + path, method=method,
                                 data=json.dumps(body).encode() if body is not None else None,
                                 headers={"Authorization": f"Bearer {token()}", "Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=30) as r:
            return r.status, json.load(r)
    except urllib.error.HTTPError as e:
        try:
            return e.code, json.loads(e.read())
        except Exception:
            return e.code, {}


def main():
    status, data = call("GET", "/v0/meta/bases")
    existing = next((b for b in data.get("bases", []) if b["name"] == BASE_NAME), None)
    if existing:
        base_id = existing["id"]
    else:
        base_id = None
        for ws in sys.argv[1:]:
            status, data = call("POST", "/v0/meta/bases", {
                "name": BASE_NAME,
                "workspaceId": ws,
                "tables": [{
                    "name": "Leads",
                    "fields": [
                        {"name": "Name", "type": "singleLineText"},
                        {"name": "Email", "type": "email"},
                        {"name": "Message", "type": "multilineText"},
                        {"name": "Source", "type": "singleLineText"},
                    ],
                }],
            })
            if status == 200:
                base_id = data["id"]
                break
        if not base_id:
            sys.exit(f"no workspace accepted the base (last status {status}: {data})")
    status, data = call("GET", f"/v0/meta/bases/{base_id}/tables")
    table = next(t for t in data["tables"] if t["name"] == "Leads")
    print(json.dumps({"baseId": base_id, "tableId": table["id"], "fields": [f["name"] for f in table["fields"]]}))


if __name__ == "__main__":
    main()
