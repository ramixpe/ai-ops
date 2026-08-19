# The reasoning gate — design for operator sign-off

**Status: awaiting review. No code written.** B-101/B-102/B-103.

This is the first place in the build where a model influences control flow. Every
existing model call is decorative: it paraphrases a conclusion code already
reached, and `grounding.py` refuses to emit the paraphrase if it fails
verification. Delete the model today and the answers are unchanged. **That stops
being true the moment this ships**, which is why it wants a signature and not
just a ticket.

## What problem it solves

The descent walks a fixed ladder and reports the lowest broken rung. When every
rung is healthy it returns `cause_not_localised` — *"the symptom is real and
every layer beneath it is healthy."* Today that is the end of the road.

A human would not stop there. They would ask a *narrowing* question: which
interface, which peer, which VRF. The gate is that step, with the model choosing
**which** narrowing question to ask and code deciding **what may be asked**.

## The shape

One typed object, two shapes only:

```
NarrowRequest(target: <one of the enumerated candidates>)   # "look closer at this"
Stop(reason: str)                                           # "nothing more to ask"
```

There is no third shape. In particular there is **no shape that carries a
verdict, a cause, or a finding.** A model physically cannot return "the cause is
X" through this interface — not because it is filtered out, but because the type
has no field for it. Same discipline as `grounding.py`, whose failure objects
have no field a model's prose can occupy.

## The part that matters: candidates are enumerated by code

`NarrowRequest.target` is not a string the model writes. **Code derives the
candidate list from objects actually observed in the evidence** — the interfaces
that appeared in this device's interface table, the peers in its BGP summary, the
adjacencies in its IS-IS output — and the model selects an index into that list.

This is B-102, and it is the half that stops *"the model chooses"* from becoming
*"the model invents"*. Without it, a model asks to narrow on `GigabitEthernet0/0/0/9`
or `10.255.0.99`, and we are back to B-459 — a fabricated argument that looks
exactly like a real one. With it, an invented target is not refused; it is
**unrepresentable**.

## Bounds

- **One narrowing pass**, then the walk terminates. Not because one is provably
  enough, but because unbounded means a model can loop until the budget dies and
  call it thoroughness. Widening this needs evidence that one pass was
  insufficient, recorded like any other measurement.
- **No new devices.** Narrowing collects detail about an object already in the
  evidence epoch. It may not reach a device the epoch never opened, or the
  coherence guarantee is silently broken.
- **The verdict is still code's.** After narrowing, the descent re-runs its own
  checks over the enlarged evidence. The model has changed *what was looked at*,
  never *what it means*.

## What the ticket must record

Instrumented, never narrated (OBS-165 — a true and a false self-report read
identically):

- the candidate list the model was offered, in full
- which index it chose, and the model's own stated reason (as free text, quoted,
  crossing the projector like any other model output)
- whether narrowing changed the finding — the number that says whether this
  feature earns its risk

## The honest risks

**1. It makes the model load-bearing.** Today a model outage degrades output
quality. After this, it changes which evidence is collected — so two runs of the
same investigation may differ. The ledger's `source` field already distinguishes
replay from live; it will need to distinguish narrowed from not.

**2. `cause_not_localised` may be the honest answer.** Several of this fabric's
open cases were *configuration*, and B-104 just shipped the config axis that
reads it — PE3's unnumbered IS-IS interface was found that way, with no model
involved. **It is worth asking whether the config axis removes most of the
demand for this gate.** I do not think it removes all of it, but I would rather
build the gate against cases that survive B-106's intent-vs-observed diff than
against today's list.

**3. The candidate list is a disclosure.** Handing a model every interface on a
device is more context than a targeted question needs, and B-501/B-518 are about
manifest size. Worth measuring before assuming it is free.

## My recommendation

Build B-101 (the typed object) and B-102 (code-enumerated candidates) **together
and now** — they are one mechanism, and the type is what makes every later
decision safe.

**Hold B-103 (the narrowing pass itself) until B-106 lands.** B-106 reconciles
intended against observed configuration. If that closes the `cause_not_localised`
cases the gate was designed for, B-103's scope changes materially — and a
narrowing pass built for cases that no longer exist is a feature we would then
have to justify keeping.

## What I need from you

Sign off on the two shapes and the code-enumerated candidate rule, or tell me
where the boundary should sit differently. Everything else here is
implementation.
