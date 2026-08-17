# Orchestrator examples

**The boundary rule, before anything else** (`docs/build/OPS-WAVE-PLAN.md`):

> An orchestrator — n8n, cron, systemd, Alertmanager — may **trigger** `nettools`
> and **route its structured output**. It may never touch a device, build a
> model prompt, or hold diagnostic logic. Everything that decides lives in
> `nettools`, versioned and tested; everything here is plumbing.

That rule is what keeps every guarantee the repository makes true regardless of
what is wired around it. An n8n flow that curls a router or pastes device output
into an LLM node has left the safety boundary, whatever this directory says.

## The pieces

| Piece | What it does |
|---|---|
| `nettools route-event` | Alertmanager webhook JSON or a raw IOS-XR syslog line → a typed routing decision with the exact `investigate` argv. Table lookup; unroutable is a stated answer, not an error. Exit 0 routable / 1 not / 2 unreadable |
| `nettools investigate` | The deterministic answer |
| `nettools audit` | The scheduled fabric audit (ships in the same wave; see `docs/build/OPS-WAVE-PLAN.md`) |
| Telegram relay | Built in: `--notify` (T-035) — the orchestrator does not need its own delivery step for Telegram |

## Recipe 1 — alert → investigation (n8n)

`n8n/investigate-on-alert.json`: Webhook → Execute Command (`nettools route-event`)
→ IF `routable_count > 0` → Execute Command (the `suggested_command` argv, joined
by n8n — each element is re-validated by `nettools` itself at run time) → route
the JSON wherever you like. Import it, point Alertmanager's `webhook_configs` at
the n8n webhook URL.

**The T-005 gap, which is yours to close on the Alertmanager side:** the current
alert rules carry no `device` or `subject` label, and `route-event` refuses to
guess them (the reason names this gap). A rule that carries both looks like:

```yaml
- alert: BgpSessionDown
  expr: <your per-device expression>
  labels:
    device: "{{ $labels.instance }}"   # must resolve to an inventory name
    subject: "10.255.0.31"             # or templated from the metric
```

## Recipe 2 — scheduled audit (n8n)

`n8n/scheduled-audit.json`: Cron (daily 09:00) → Execute Command
(`nettools audit --format json`) → IF exit ≠ 0 → notify. The audit's meaning
lives in `audit.py`'s reviewed rule table, not in the workflow.

## Recipe 3 — no n8n at all (systemd)

`systemd/nettools-webhook.py` + unit file: a stdlib HTTP listener, localhost
only, that pipes each POST body to `route-event` and executes the suggested
argv **as a list** (never through a shell). **An example, not shipped code** —
read `SECURITY.md`'s unsupported-deployment-modes before exposing any listener
beyond localhost; there is no authentication anywhere in this path.
