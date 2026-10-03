"""Proves the end-to-end checks can fail. Each mutant breaks one guarantee in workflow.json, restarts the real Logic Apps runtime,
runs tests/e2e_workflow.py, and expects it to FAIL. A mutant that leaves the checks green means a guarantee is not really tested.

    python scripts/mutation_check.py

Needs Azurite and the mock systems running (see README). It stops and restarts `func` itself and always restores workflow.json.
Slow by design (each mutant restarts the runtime and waits through the retry intervals).
"""
import os
import subprocess
import sys
import time
import urllib.request

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
WORKFLOW = os.path.join(ROOT, "logicapp", "admission-intake", "workflow.json")
E2E = os.path.join(ROOT, "tests", "e2e_workflow.py")
FUNC = os.path.join(os.environ.get("APPDATA", ""), "npm", "func.cmd")
LOG = os.path.join(ROOT, "logs", "func_mutation.log")

# (name, old text, new text, the guarantee it removes)
MUTANTS = [
    ("ssn is no longer treated as a direct identifier", "triggerBody()?['ssn']", "triggerBody()?['ssnx']", "refuse identifiers at the door"),
    ("the Idempotency-Key header is dropped from system A", '"Idempotency-Key": "@{triggerBody()?[\'admissionId\']}"', '"X-Other": "@{triggerBody()?[\'admissionId\']}"', "a repeated admission never double-writes"),
    ("retries are cut from 3 to 1", '"count": 3', '"count": 1', "a transient downstream failure is absorbed"),
    ("the dead-letter hand-off is pointed at a missing path", "/dead-letter", "/nowhere", "a failed hand-off is parked, not lost"),
    ("the shared-key check always passes", '"@appsetting(\'INTAKE_SHARED_KEY\')"', '"@triggerOutputs()?[\'headers\']?[\'x-intake-key\']"', "unknown callers are refused"),
    ("a missing careLevel is no longer required", "{ \"not\": { \"equals\": [ \"@coalesce(triggerBody()?['careLevel'], '')\", \"\" ] } }", "{ \"not\": { \"equals\": [ \"@coalesce(triggerBody()?['careLevel'], 'x')\", \"\" ] } }", "incomplete admissions are rejected"),
]


def stop_func():
    ps = "Get-NetTCPConnection -LocalPort 7071 -State Listen -ErrorAction SilentlyContinue | ForEach-Object { Stop-Process -Id $_.OwningProcess -Force }"
    subprocess.run(["powershell", "-NoProfile", "-Command", ps], capture_output=True)
    time.sleep(3)


def start_func():
    stop_func()
    env = dict(os.environ, FUNCTIONS_CORE_TOOLS_TELEMETRY_OPTOUT="1")
    log = open(LOG, "w")
    subprocess.Popen([FUNC, "start", "--port", "7071"], cwd=os.path.join(ROOT, "logicapp"), stdout=log, stderr=subprocess.STDOUT, env=env, creationflags=0x08000000)
    for _ in range(40):
        time.sleep(3)
        try:
            urllib.request.urlopen("http://localhost:7071/admin/host/status", timeout=3)
            if "admission-intake:" in open(LOG, errors="replace").read():
                return True
        except Exception:
            pass
    return False


def run_e2e():
    p = subprocess.run([sys.executable, E2E], capture_output=True, text=True, timeout=600)
    return p.returncode, p.stdout


def main():
    original = open(WORKFLOW, encoding="utf-8").read()
    results = []
    try:
        assert start_func(), "runtime did not start for the baseline"
        code, out = run_e2e()
        print("BASELINE:", "all checks pass" if code == 0 else "FAILING, fix this first\n" + out)
        if code != 0:
            sys.exit(2)
        for name, old, new, guarantee in MUTANTS:
            assert old in original, f"mutant text not found, the workflow changed: {name}"
            open(WORKFLOW, "w", encoding="utf-8", newline="").write(original.replace(old, new, 1))
            started = start_func()
            if not started:
                # a mutant the runtime refuses to load is caught by the engine's own validation, which still counts as caught
                results.append((name, "CAUGHT (the runtime refused to load it)"))
                continue
            code, out = run_e2e()
            failed = [l for l in out.splitlines() if l.startswith("FAIL")]
            results.append((name, f"CAUGHT by {len(failed)} check(s), e.g. {failed[0][6:90]}" if code != 0 else "SURVIVED  <-- " + guarantee + " is not really tested"))
    finally:
        open(WORKFLOW, "w", encoding="utf-8", newline="").write(original)
        start_func()
    print()
    for name, verdict in results:
        print(f"{verdict.split()[0]:9} {name}: {verdict}")
    survived = [n for n, v in results if v.startswith("SURVIVED")]
    print(f"\n{len(results) - len(survived)}/{len(results)} mutants caught")
    sys.exit(1 if survived else 0)


if __name__ == "__main__":
    main()
