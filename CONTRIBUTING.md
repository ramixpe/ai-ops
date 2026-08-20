# Contributing

## What this project is, stated plainly

`ios-xr-nettools` is read-only inspection for a Cisco IOS-XR lab, with a
deterministic investigation layer that walks a protocol dependency stack and
reports the lowest broken rung. It was built alongside a book, as a worked
example of designing an agent whose action space is enumerable before the model
runs.

**That provenance matters for what the evidence is worth.** It has been evaluated
on a thirteen-node containerlab fabric, on one vendor, with a small number of
blind fault-injection trials whose cases were designed by someone who knows the
ladder. Three independent external reviews are in `docs/`, and
`docs/design/peer-review-response.md` records which of our claims they forced us
to withdraw. Nothing here has run in production.

If you are evaluating rather than contributing, `docs/README.md` has a reading
path for that.

## Before you change anything

```bash
make setup && source .venv/bin/activate
make test      # the full suite (about 3,200 tests; the exact count moves — CI is authoritative)
make lint
```

If the suite needs a lab, a credential or a key to pass, something is wrong with
the change. That property is machine-checked in CI by the `offline-demo` job,
which runs the README's opening command in a clean checkout with the credential
environment asserted empty.

## The rules that are not up for negotiation

These exist because each one was learned expensively. `docs/build/FINDINGS.md`
has the incident behind every one.

### 1. Frozen files

`tests/test_safety.py` and `tests/test_template_security.py` are **frozen**.
`platforms.py` and `templates.py` take **additions only — never a relaxed
validator**.

All four are checked byte-identical against `6629a2c` as part of accepting work.
If a change appears to require touching them, that is a signal to stop and ask,
not a step to take carefully.

### 2. The allowlist is the only enforcement

`platforms.APPROVED_COMMANDS[platform]` is an exact-match frozenset, checked
against the device's own platform and **before credentials are loaded or a socket
is opened**. The ordering is load-bearing: platform resolution reads static data
only, so that the allowlist check can happen first.

**Never make platform resolution require credentials.** It would silently move
the allowlist check after credential access, and every test would still pass.

There is no `run_command(device, command)`, no config mode, and no shell. Do not
add one.

### 3. Commands are built by reconstruction, never by interpolation

A caller-supplied value is parsed into a typed object and the command is rendered
from *that object's canonical form*. Validate-then-pass-through was considered and
rejected: a regex broad enough to accept every legitimate value is also broad
enough to admit a lookalike nobody anticipated.

### 4. No unparsed device text reaches a model

Enforced **structurally** — `prompt_library` takes a `DescentResult` and cannot
hold raw text; `mcp_server/boundary.py` sanitises at the registration decorator,
so a tool is covered by the act of being registered.

Note the shape of the near-miss: this invariant held for every internal caller
for eight phases and then broke when a new consumer arrived (OBS-111). **An
invariant that holds for every current caller is a convention, not an invariant.**
A new path to a model needs the guarantee built into it.

### 5. Guardrails are tests, not prompt text

The prompt said *quote exactly*. The model did not. A prompt constraint is a
prior; only code is a gate.

### 6. A guardrail test must fail if the guarded behaviour is removed

And more sharply:

> **A test written from the same premise as the implementation confirms the
> premise, not the implementation.**

This has happened here more than once — sixteen passing tests missed a defect
that an independently-written specification found in an afternoon. When you add a
guardrail, add the companion that fails when the guard is removed.

### 7. Absence is never a healthy value

A check may only answer `healthy` about a field it actually read. Zero IS-IS
adjacencies because the command failed must not look like zero because the device
is isolated. The third state is `unevaluated` and it is not decoration.

## Adding things

**A read-only command:** add it to `PLATFORM_INTENTS`, then the per-platform
README block. The allowlist derives itself. `tests/test_docs.py` parses the README
by literal marker sentences and will fail if the lists diverge — update code and
docs in the same change.

**A health rule:** one function plus one line in a table in `health.py`. Never a
new code path through `evaluate_device`.

**A rung:** read the rules on `flows.Rung` first. A rung needs a dependency
assertion, an observation from a *distinct subsystem*, and a captured case where
it and the rung below it separate. `test_rungs.py` enforces the checkable parts.

**A prompt change:** a **version bump, never an in-place edit**. Superseded
versions stay in the tree. Golden tests assert shape and citation integrity, never
prose — a test that pins wording fails on a harmless rewording and passes on a
fabricated citation, which is exactly backwards.

## Tests

Three SSH-free seams, in `tests/helpers.py`. Prefer them over mocking netmiko:

- `sender=` — short-circuits transport; best for asserting exact commands.
- **Fixture replay** — `load_fixture_evidence(device, label=...)` replays real
  captured output. Use this for anything needing realistic output.
- A fake `netmiko` module — when the test cares about transport behaviour.

`tests/test_live_lab.py` is the one exception and self-skips unless
`NETTOOLS_LIVE_LAB=1`.

## Writing it down

`docs/build/FINDINGS.md` is **append-only**. When something turns out to be wrong,
append the correction and leave the original visible — the order things were
learned in is most of the file's value, and this project has repeatedly found that
the wrong turn is more instructive than the destination.

If you correct a claim in any document, **grep for its other statements before
you call the correction done.** A stale constant fails a test; a stale sentence
renders identically to a true one, and we have shipped ten of those (OBS-137).

## Reporting a security issue

See [SECURITY.md](SECURITY.md) for the threat model, what is and is not
enforced, and where to report a problem.

## Licence

MIT. See [LICENSE](LICENSE).
