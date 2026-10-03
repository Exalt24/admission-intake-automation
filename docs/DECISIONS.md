Why the workflow is shaped the way it is, including what the runtime taught me by refusing things.

## The brief this is built from

Care-facility staff re-key every new admission into two systems, then chase confirmation. The automation takes one intake call and writes it to both systems once, safely, and tells the caller the truth when it cannot. It is the "copying the same information into two systems" problem from a healthcare-IT job post, not a general demo.

## Decisions

**Direct identifiers are refused at the door, not filtered later.** The intake accepts `residentRef`, the facility's own opaque reference, and answers 422 to any payload carrying a name, birth date, phone number, address or social security number. The refused value is never written to either system or to the dead-letter store, and a test checks all three. Masking after storage still leaves the original in logs and run history, so the only safe place to stop it is before anything is stored.

**The caller is checked before anything else, and a refused call writes nothing.** A shared key in `x-intake-key` is compared to an app setting. The Logic Apps trigger URL carries its own signature too, so there are two layers, and a wrong or missing key gets 401 before validation runs.

**One admission is written once, even when the caller retries.** Both writes carry an `Idempotency-Key` header set to the admission id, and the mock systems dedupe only on that header (a request without one counts as new). Sending the same admission twice leaves exactly one record in each system. A mutation that drops the header is caught by the test for this.

**Retries are fixed and slow on purpose, because the runtime will not let them be fast.** I first wrote a one second retry interval. The Logic Apps runtime refused to load the workflow: the minimum retry interval is five seconds. So each write retries up to three times, five seconds apart. The test proves two transient 503s are absorbed and that the retries actually waited rather than looping tight.

**A failure that outlasts the retries is parked, not lost, and the caller is told.** The two writes sit in a scope. If the scope fails, the record and the failure detail go to a dead-letter store and the caller gets 502 with `status: held`, so nobody believes it landed when it did not. A person replays it from the dead-letter store.

**Partial success is stated, not hidden.** The two writes run in parallel. If A keeps failing and B succeeds, B keeps its record and the dead-letter entry says which side failed. A compensating delete would add its own failure mode, and the idempotency key makes a later replay safe, so I left the half-written state visible rather than clever. This is the decision a facility's vCIO should confirm, because the right answer depends on the two real systems.

**The run history is evidence.** The tests read the runtime's own run and action history and assert that the fan-out scope succeeded on the happy path and failed-then-was-handled on the broken one, so "it worked" is checked in the engine, not only from the response.

## What I did not do

- It runs on the local Logic Apps Standard runtime against mock systems. It has not been deployed to an Azure subscription.
- The two "systems" are small local servers, not a real electronic records system.
- It has no UI. A Power Apps front end for the coordinator is the obvious next layer.
- "Hours given back" in the README is arithmetic on stated assumptions, not a measurement.
- Nothing here is production, and nothing here claims HIPAA compliance. The design follows least privilege, no stored identifiers and an audit trail, which is what a HIPAA review would look for first.
