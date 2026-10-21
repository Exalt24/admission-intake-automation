"""Creates the Jotform form that feeds the second Zap: a "Lead intake" form (Name, Email, Message), with a webhook pointing at the Zap's Catch Hook URL.
Idempotent: a form with that title is reused, and a webhook is only added if that URL is not already attached.

    JOTFORM_API_KEY=... python scripts/jotform_setup.py <catch hook url>             # create or reuse the form and attach the webhook
    JOTFORM_API_KEY=... python scripts/jotform_setup.py --submit <name> <email> <message>   # submit a test lead through the API

Prints the form id and its public URL. The key comes from the environment or JOTFORM_KEY_FILE; nothing secret is stored in the repo.
"""
import json
import os
import sys
import urllib.parse
import urllib.request

TITLE = "Lead intake"
API = "https://api.jotform.com"


def key():
    value = os.environ.get("JOTFORM_API_KEY")
    path = os.environ.get("JOTFORM_KEY_FILE")
    if not value and path and os.path.exists(path):
        value = open(path, encoding="utf-8").readline().strip()
    if not value:
        sys.exit("missing JOTFORM_API_KEY (or JOTFORM_KEY_FILE)")
    return value


def call(method, path, params=None):
    data = urllib.parse.urlencode(params or {}).encode() if method in ("POST", "PUT") else None
    url = f"{API}{path}?apiKey={key()}"
    req = urllib.request.Request(url, data=data, method=method)
    with urllib.request.urlopen(req, timeout=30) as r:
        return json.load(r)["content"]


def find_or_create_form():
    forms = call("GET", "/user/forms")
    for f in forms:
        if f["title"] == TITLE and f["status"] != "DELETED":
            return f["id"]
    params = {"properties[title]": TITLE}
    questions = [
        ("control_textbox", "Name", "name", 1, "Yes"),
        ("control_email", "Email", "email", 2, "Yes"),
        ("control_textarea", "Message", "message", 3, "No"),
    ]
    for i, (qtype, text, name, order, required) in enumerate(questions):
        params[f"questions[{i}][type]"] = qtype
        params[f"questions[{i}][text]"] = text
        params[f"questions[{i}][name]"] = name
        params[f"questions[{i}][order]"] = str(order)
        params[f"questions[{i}][required]"] = required
    params["questions[3][type]"] = "control_button"
    params["questions[3][text]"] = "Submit"
    params["questions[3][name]"] = "submit"
    params["questions[3][order]"] = "4"
    return call("POST", "/form", params)["id"]


def qids(form_id):
    out = {}
    for qid, q in call("GET", f"/form/{form_id}/questions").items():
        if q.get("name") in ("name", "email", "message"):
            out[q["name"]] = qid
    return out


def main():
    args = sys.argv[1:]
    form_id = find_or_create_form()
    if args and args[0] == "--submit":
        name, email, message = args[1:4]
        ids = qids(form_id)
        res = call("POST", f"/form/{form_id}/submissions", {f"submission[{ids['name']}]": name, f"submission[{ids['email']}]": email, f"submission[{ids['message']}]": message})
        print(json.dumps({"submitted": True, "submissionID": res.get("submissionID")}))
        return
    if not args:
        sys.exit("give the Catch Hook URL, or --submit <name> <email> <message>")
    hook = args[0]
    existing = call("GET", f"/form/{form_id}/webhooks")
    attached = list(existing.values()) if isinstance(existing, dict) else list(existing or [])
    if hook not in attached:
        call("POST", f"/form/{form_id}/webhooks", {"webhookURL": hook})
    print(json.dumps({"formId": form_id, "url": f"https://form.jotform.com/{form_id}", "webhook": hook}))


if __name__ == "__main__":
    main()
