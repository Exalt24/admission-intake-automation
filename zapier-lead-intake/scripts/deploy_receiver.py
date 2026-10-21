"""Deploys worker/receiver.js to a Cloudflare account, with a KV namespace and a shared key stored as a Worker secret. Prints the Worker URL.
The account comes from CLOUDFLARE_ENV_FILE (lines CLOUDFLARE_API_TOKEN=... and CLOUDFLARE_ACCOUNT_ID=...). The key is saved to CHAIN_KEY_FILE
(keep it out of git) and reused on a redeploy, so a Zap that already sends it keeps working.

    python scripts/deploy_receiver.py
"""
import json
import os
import secrets
import urllib.error
import urllib.request

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
ENV = os.environ.get("CLOUDFLARE_ENV_FILE", "cloudflare.env")
KEYFILE = os.environ.get("CHAIN_KEY_FILE", "chain_key.txt")
NAME = "nocode-chain-receiver"
KV_TITLE = "nocode-chain-records"


def env():
    out = {}
    for line in open(ENV, encoding="utf-8"):
        if "=" in line and not line.strip().startswith("#"):
            k, v = line.strip().split("=", 1)
            out[k.strip()] = v.strip().strip('"')
    return out


E = env()
TOKEN, ACCT = E["CLOUDFLARE_API_TOKEN"], E["CLOUDFLARE_ACCOUNT_ID"]
BASE = f"https://api.cloudflare.com/client/v4/accounts/{ACCT}"


def api(method, path, body=None, headers=None, raw=None):
    data = raw if raw is not None else (json.dumps(body).encode() if body is not None else None)
    h = {"Authorization": f"Bearer {TOKEN}", "User-Agent": "curl/8"}
    h.update(headers or ({"Content-Type": "application/json"} if body is not None else {}))
    req = urllib.request.Request(BASE + path, data=data, method=method, headers=h)
    try:
        with urllib.request.urlopen(req, timeout=60) as r:
            return json.load(r)
    except urllib.error.HTTPError as e:
        return {"success": False, "errors": [e.read().decode()[:400]]}


def main():
    kv = api("GET", "/storage/kv/namespaces?per_page=100")
    ns = next((n for n in kv.get("result", []) if n["title"] == KV_TITLE), None)
    if not ns:
        ns = api("POST", "/storage/kv/namespaces", {"title": KV_TITLE}).get("result")
    assert ns, "could not create the KV namespace"

    # Reuse the saved key on a redeploy so a Zap that already sends it keeps working; mint one only the first time.
    key = open(KEYFILE, encoding="utf-8").readline().strip() if os.path.exists(KEYFILE) else secrets.token_urlsafe(24)
    code = open(os.path.join(ROOT, "worker", "receiver.js"), encoding="utf-8").read()
    boundary = "----nocodechain"
    meta = {
        "main_module": "receiver.js",
        "compatibility_date": "2025-01-01",
        "bindings": [
            {"type": "kv_namespace", "name": "RECORDS", "namespace_id": ns["id"]},
            {"type": "secret_text", "name": "CHAIN_KEY", "text": key},
        ],
    }
    parts = (
        f"--{boundary}\r\nContent-Disposition: form-data; name=\"metadata\"\r\nContent-Type: application/json\r\n\r\n{json.dumps(meta)}\r\n"
        f"--{boundary}\r\nContent-Disposition: form-data; name=\"receiver.js\"; filename=\"receiver.js\"\r\nContent-Type: application/javascript+module\r\n\r\n{code}\r\n"
        f"--{boundary}--\r\n"
    ).encode()
    res = api("PUT", f"/workers/scripts/{NAME}", raw=parts, headers={"Content-Type": f"multipart/form-data; boundary={boundary}"})
    assert res.get("success"), res
    api("POST", f"/workers/scripts/{NAME}/subdomain", {"enabled": True, "previews_enabled": False})
    sub = api("GET", "/workers/subdomain").get("result", {}).get("subdomain")
    assert sub, "no workers.dev subdomain on this account"
    with open(KEYFILE, "w", encoding="utf-8") as f:
        f.write(key + "\n")
    print(f"https://{NAME}.{sub}.workers.dev")


if __name__ == "__main__":
    main()
