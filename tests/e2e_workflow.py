"""End-to-end tests for the admission-intake Logic App, run against the REAL Logic Apps Standard runtime on localhost.

Start the three things first (see README): the mock systems, Azurite, and `func start` in logicapp/. Then:

    python tests/e2e_workflow.py            # prints PASS/FAIL per check, exits non-zero on any failure

The mock systems stand in for the two systems a coordinator used to re-key every admission into, plus the dead-letter store.
"""
import json
import sys
import time
import urllib.error
import urllib.request

RUNTIME = "http://localhost:7071"
WORKFLOW = "admission-intake"
TRIGGER = "When_an_admission_is_received"
KEY = "local-dev-key-change-me"
A, B, DEAD = "http://127.0.0.1:7801", "http://127.0.0.1:7802", "http://127.0.0.1:7803"

failures = []


def call(url, body=None, headers=None, method="POST", timeout=90):
    data = json.dumps(body).encode() if body is not None else (b"{}" if method == "POST" else None)
    req = urllib.request.Request(url, data=data, method=method, headers={"Content-Type": "application/json", **(headers or {})})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            raw = r.read()
            return r.status, (json.loads(raw) if raw else None)
    except urllib.error.HTTPError as e:
        raw = e.read()
        try:
            return e.code, json.loads(raw)
        except Exception:
            return e.code, raw.decode(errors="replace")


def callback_url():
    status, body = call(f"{RUNTIME}/runtime/webhooks/workflow/api/management/workflows/{WORKFLOW}/triggers/{TRIGGER}/listCallbackUrl?api-version=2020-05-01-preview", {})
    assert status == 200, f"could not get the callback url: {status} {body}"
    return body["value"]


def reset():
    for base in (A, B, DEAD):
        call(f"{base}/_reset", {})


def state(base):
    return call(f"{base}/_state", method="GET")[1]


def send(url, payload, key=KEY):
    headers = {"x-intake-key": key} if key is not None else {}
    return call(url, payload, headers)


def admission(n="1"):
    return {"admissionId": f"ADM-{n}", "facilityId": "FAC-07", "residentRef": f"R-{n}", "admitDate": "2026-10-05", "careLevel": "memory-care"}


def check(name, ok, detail=""):
    print(("PASS  " if ok else "FAIL  ") + name + ("" if ok else f"   <- {detail}"))
    if not ok:
        failures.append(name)


def latest_actions():
    _, runs = call(f"{RUNTIME}/runtime/webhooks/workflow/api/management/workflows/{WORKFLOW}/runs?api-version=2020-05-01-preview", method="GET")
    run_id = runs["value"][0]["name"]
    _, acts = call(f"{RUNTIME}/runtime/webhooks/workflow/api/management/workflows/{WORKFLOW}/runs/{run_id}/actions?api-version=2020-05-01-preview", method="GET")
    return {a["name"]: a["properties"]["status"] for a in acts["value"]}


def main():
    url = callback_url()

    # 1. the happy path: one request, both systems written once, caller gets a tracking id
    reset()
    status, body = send(url, admission("1"))
    check("a valid admission is accepted with 202", status == 202, f"{status} {body}")
    check("the response carries a tracking id", isinstance(body, dict) and len(body.get("trackingId", "")) == 36, body)
    a, b = state(A), state(B)
    check("system A got exactly one record", len(a["records"]) == 1, a)
    check("system B got exactly one record", len(b["records"]) == 1, b)
    check("both systems got the same tracking id", a["records"] and b["records"] and a["records"][0]["trackingId"] == b["records"][0]["trackingId"])
    check("the stored record holds only the agreed fields (no stray input)", a["records"] and set(a["records"][0]) == {"trackingId", "admissionId", "facilityId", "residentRef", "admitDate", "careLevel", "receivedAt"}, a["records"])
    time.sleep(2)
    acts = latest_actions()
    check("run history shows the fan-out scope succeeded", acts.get("Fan_out_to_both_systems") == "Succeeded", acts)

    # 2. the front door
    reset()
    status, body = send(url, admission("2"), key="wrong")
    check("a wrong key gets 401", status == 401, f"{status} {body}")
    status, body = send(url, admission("2"), key=None)
    check("a missing key gets 401", status == 401, f"{status} {body}")
    check("a refused call writes nothing anywhere", not state(A)["records"] and not state(B)["records"])

    bad = admission("3")
    del bad["careLevel"]
    status, body = send(url, bad)
    check("a missing required field gets 400", status == 400 and body.get("error") == "invalid_admission", f"{status} {body}")
    check("a 400 writes nothing anywhere", not state(A)["records"] and not state(B)["records"])

    for field, value in [("residentName", "Jane Doe"), ("ssn", "123-45-6789"), ("dob", "1941-02-03"), ("phone", "555-0100"), ("address", "1 Main St")]:
        reset()
        payload = admission("4")
        payload[field] = value
        status, body = send(url, payload)
        check(f"a direct identifier ({field}) is refused with 422", status == 422 and body.get("error") == "direct_identifiers_not_accepted", f"{status} {body}")
        check(f"the refused {field} is not stored in either system", not state(A)["records"] and not state(B)["records"] and value not in json.dumps(state(DEAD)))

    # 3. idempotency: the same admission twice must not double-write
    reset()
    send(url, admission("5"))
    status, _ = send(url, admission("5"))
    check("a repeated admission is still accepted", status == 202)
    check("a repeated admission is stored once in A", len(state(A)["records"]) == 1, state(A))
    check("a repeated admission is stored once in B", len(state(B)["records"]) == 1, state(B))

    # 4. a flaky downstream: two 503s, the retry policy absorbs them
    reset()
    call(f"{A}/_fail?n=2", {})
    started = time.time()
    status, body = send(url, admission("6"))
    took = time.time() - started
    check("two transient failures from A are retried and the call still succeeds", status == 202, f"{status} {body}")
    check("A was attempted three times (two failures then success)", state(A)["attempts"] == 3, state(A))
    check("the retries actually waited (fixed interval, not a tight loop)", took >= 9, f"took {took:.1f}s")
    check("the record landed in A after the retries", len(state(A)["records"]) == 1)

    # 5. a downstream that stays down: dead-letter it, tell the caller the truth, keep what did land
    reset()
    call(f"{A}/_fail?n=50", {})
    status, body = send(url, admission("7"))
    check("a downstream that stays down gets 502 held", status == 502 and body.get("status") == "held", f"{status} {body}")
    dead = state(DEAD)
    check("the failed admission is parked in the dead-letter store", len(dead["records"]) == 1, dead)
    parked = json.dumps(dead["records"])
    check("the dead-letter entry names the admission and carries the failure detail", "ADM-7" in parked and "Failed" in parked, parked[:300])
    check("system B still received its record", len(state(B)["records"]) == 1, state(B))
    check("system A never received a record", len(state(A)["records"]) == 0, state(A))
    time.sleep(2)
    acts = latest_actions()
    check("run history shows the fan-out scope failed and was handled", acts.get("Fan_out_to_both_systems") == "Failed" and acts.get("Send_to_dead_letter") == "Succeeded", acts)

    print()
    if failures:
        print(f"{len(failures)} FAILED: " + "; ".join(failures))
        sys.exit(1)
    print("all checks passed")


if __name__ == "__main__":
    main()
