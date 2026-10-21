A Zap is built in Zapier's editor and cannot be committed as code, so this is the written spec of the two that are live, for anyone who wants to rebuild them. Both use Zapier's 14-day trial of paid features, because the Webhooks app is a premium app.

## Zap 1: Lead intake: webhook to receiver

| Step | App and event | Settings |
|---|---|---|
| 1. Trigger | Webhooks by Zapier, **Catch Hook** | No child key. Zapier gives a unique URL; a sample lead was POSTed to it so the editor could read the fields (name, email, message, source) |
| 2. Action | Webhooks by Zapier, **POST** | URL: `<receiver>/ingest`. Payload type: **Json**. Wrap request in array: No. Unflatten: Yes |

Data mapping in step 2: `name`, `email` and `message` are mapped from step 1; `source` is the fixed text `zapier-demo` (the caller's own `source` is deliberately not passed through). Header: `x-chain-key` set to the receiver's shared key.

## Zap 2: Jotform lead to Airtable

| Step | App and event | Settings |
|---|---|---|
| 1. Trigger | Webhooks by Zapier, **Catch Hook** | This URL is the webhook attached to the Jotform form (`scripts/jotform_setup.py`). A real submission through the form was used as the sample, so the editor shows Jotform's own field names |
| 2. Action | Webhooks by Zapier, **POST** | URL: `https://api.airtable.com/v0/<base id>/<table id>`. Payload type: **Json**. Unflatten: Yes |

Data mapping in step 2 (the double underscore is how Zapier nests a key, so these become `{"fields": {...}}`): `fields__Name` from the form's Name, `fields__Email` from its Email, `fields__Message` from its Message, and `fields__Source` as the fixed text `jotform`. Header: `Authorization: Bearer <Airtable personal access token>`.

## Tested

The editor's own "Test step" for each action made a real request that was stored (the Worker for Zap 1, an Airtable record for Zap 2). After publishing, `scripts/e2e_check.py` and `scripts/e2e_chain.py` fire each Zap from outside and read the result back. Both runs show as Successful in Zap history.
