# Migration — standing this repo up on a new host

**Audience: an agent or engineer rebuilding this repository against a lab that
has moved.** Every step has a *check* that proves it worked. Do not proceed
past a failing check — the failure modes here are quiet, and two of them look
like a different problem than they are.

The repository is portable. Almost nothing in it is bound to a particular
machine: the suite runs fully offline, the fixtures are committed, and the two
host-specific URLs are environment-overridable. What does not travel is
`.env`, the virtualenv, and **the SSH host keys** — and that last one is the
one that will waste your afternoon.

---

## 0. Preconditions

```bash
git clone <remote> ios-xr-nettools && cd ios-xr-nettools
```

The lab must be reachable before steps 1 and 5. Steps 2–4 need no lab at all.

---

## 1. Re-enroll the SSH host keys — do this FIRST

**The failure this prevents, stated before the command, because it does not
look like what it is:**

XRd containers generate fresh SSH host keys when they are redeployed. This tool
pins host keys in `~/.config/nettools/known_hosts` (nine entries — one per
router), never reads `~/.ssh/known_hosts`, and treats a mismatch as
**terminal**: a host-key failure is a trust failure, never transient, and is
never retried (`network_tools._is_host_key_failure`).

So after a move, **every `nettools` command fails closed** with what looks like
a broken tool.

**And here is the trap.** `faultlab/fault_lab.py` connects with paramiko's
`AutoAddPolicy` and will keep working perfectly against the same devices. You
will see the injector succeed while the tool fails, and conclude the tool is
broken. It is not — it is the only one of the two that is checking. This is
recorded in the retained evaluation corpus for exactly this reason.

```bash
python3 scripts/enroll_host_keys.py
```

**CHECK:**

```bash
wc -l ~/.config/nettools/known_hosts     # expect: 9
nettools facts PE1                        # expect: "status": "success"
```

If `nettools facts` fails while `fault_lab.py` connects, you are in the trap
above — re-run the enrollment, do not start debugging the transport.

---

## 2. Recreate `.env`

`.env` is gitignored and does not travel. `.env.example` documents the full
surface.

```bash
cp .env.example .env
```

Fill in, at minimum:

| Variable | Why |
|---|---|
| `DEVICE_USERNAME`, `DEVICE_PASSWORD` | anything that touches a device |
| `NETTOOLS_LOKI_URL` | defaults to `http://172.20.250.103:3100` |
| `NETTOOLS_PROMETHEUS_URL` | defaults to `http://172.20.250.102:9090` |
| `TELEGRAM_BOT_TOKEN`, `TELEGRAM_CHAT_ID` | only if you want notifications |
| NetBox / neo4j credentials | only for those evidence sources |

**Set the URLs here rather than editing `settings.py`.** They are declared
defaults, not constants — overriding by environment is the supported path and
leaves the code identical to what CI tests.

**CHECK:**

```bash
nettools config check      # reports malformed or out-of-range values
```

A typo like `NETTOOLS_READ_TIMEOUT_SECONDS=1O` (letter O) silently becomes the
default and nothing else will tell you. That is what this command exists for.

---

## 3. Build the virtualenv

```bash
make setup
source .venv/bin/activate
```

`make setup` defaults to `PYTHON ?= python3.12` — **a version CI actually gates
on.** A bare `python3` is whatever the host ships, and on the previous machine
that was 3.13, which no CI leg exercises; the result was a session that
reported green all night against an interpreter the merge gate never ran
(OBS-695/OBS-697). Override deliberately if you must: `make setup
PYTHON=python3.11`.

**CHECK:**

```bash
python -V                  # expect 3.11.x or 3.12.x — NOT 3.13
nettools version
```

If you rename or move a venv afterwards, **console-script shebangs break** —
they hardcode the interpreter path. Fix with
`pip install --force-reinstall --no-deps -e .`.

---

## 4. Prove the repo works with no lab at all

This is the step that separates "the repo is fine" from "the lab is not up
yet". The whole suite is fixture-backed and needs no network, credentials, or
API key.

```bash
make test          # expect: 3604 passed, 22 skipped   (count moves; CI is authoritative)
make lint          # expect: All checks passed!
nettools investigate RR1 10.255.0.12 --from-fixtures --label broken --no-model
                   # expect: finding "interface_line_down", exit 1
```

**CHECK:** all three succeed. If they do, **the repository is correctly
installed** and everything remaining is lab-side.

Also worth running once after a move, because it is **not in CI** and a broken
anchor is therefore silent:

```bash
python3 scripts/mutate_guards.py      # expect: 71/71 guards hold
```

Never wrap that script in a `timeout` — it mutates source in place and restores
it, and killing it mid-pass leaves the tree modified (OBS-300).

---

## 5. Only if the lab's addressing changed

If containerlab comes up on the same `172.20.250.0/24` with the same node
names, **nothing in the repo needs editing.** Skip to step 6.

If it changed:

> **`inventory/lab.yaml` and `src/agent_nettools/data/lab.yaml` are
> byte-identical and both must be edited.** The second is the packaged fallback
> the wheel ships (R2); editing one and not the other produces a tool that
> behaves differently from a source checkout than from an installed wheel.
> Verify with `cmp inventory/lab.yaml src/agent_nettools/data/lab.yaml`.

Current allocation, for reference:

| Node | mgmt | Node | mgmt |
|---|---|---|---|
| P1–P4 | `.11`–`.14` | PE1–PE4 | `.21`–`.24` |
| RR1 | `.31` | | |

**CHECK:** `nettools inventory` lists nine devices, and `nettools facts PE1`
reaches the right one.

---

## 6. Re-derive the baselines — do not trust the committed ones

`inventory/lab.yaml` carries `expected.isis_adjacencies` and
`expected.bgp_peers` per device. **These were learned from the previous
fabric**, and the file's own comment records that some were originally captured
while the fabric was broken — a baseline learned from a broken state is the
defect `checks._suspicious_baseline` exists to catch.

```bash
# Derive from the LIVE fabric into a scratch file. Both flags matter.
nettools learn-topology --live --out /tmp/derived-inventory.yaml
diff <(grep -E 'name:|isis_adjacencies:|bgp_peers:' inventory/lab.yaml) \
     <(grep -E 'name:|isis_adjacencies:|bgp_peers:' /tmp/derived-inventory.yaml)
nettools health --all
```

> **Two traps in that one command, both of which produce a wrong answer
> silently.**
>
> **`--from-fixtures` is the DEFAULT.** Running `nettools learn-topology`
> without `--live` re-derives from the committed fixtures — that is, from the
> *old* fabric — and will cheerfully confirm the baselines you are trying to
> re-verify. After a migration that is exactly backwards. Pass `--live`.
>
> **Do not use `--write`.** Its own help says it *"rewrites the resolved
> inventory in place. Discards its comments."* `inventory/lab.yaml`'s comments
> are not decoration — they record why PE3 is excluded from the IS-IS baseline
> (B-496), which values were once learned from a broken fabric, and which were
> re-derived and when. `--write` also touches only the resolved inventory, so
> the packaged copy at `src/agent_nettools/data/lab.yaml` would silently drift
> out of step. **Diff the draft, then hand-edit the numbers into both files,
> keeping the prose.**

Expected on a correctly rebuilt fabric:

| Devices | `isis_adjacencies` | `bgp_peers` |
|---|---|---|
| P1–P4 | 5 | — |
| PE1–PE4, RR1 | 2 | 1 per PE (RR1: 4) |

**Known and NOT a migration failure**, if they reappear:

- **PE3 shows 1 IS-IS adjacency, not 2** — B-496. LLDP is up at both ends of
  PE3↔P2 while IS-IS is adjacent at neither. Pre-existing.
- **`Gi0/0/0/2.300` admin-up / line-down on PE1 and PE3** — pre-existing.
- **SR-TE policy `20:10.255.0.13` down** — pre-existing (B-515 exposed the
  detail; the policy itself is genuinely down).
- **"BGP peer X is Established with 0 prefixes received"** — normal on this
  lab, recorded as an operator note in the inventory.

**What would be a real problem:** a `bgp_peers` count above 1 on any PE. That
means a direct PE↔PE session exists alongside the route reflector, which
defeats the point of having one — B-699 removed exactly that.

---

## 7. Live verification

```bash
NETTOOLS_LIVE_LAB=1 pytest -m live_lab -q
nettools investigate RR1 10.255.0.12 --no-model
```

The second should return `all_layers_healthy` with five of five rungs healthy
on a golden fabric. **If it returns a fault, check the fabric before checking
the tool** — twice now a leftover injected fault from an earlier chaos round
was read as a genuine finding, once by a model under evaluation and once by
this project's own orchestrator (OBS-690, OBS-692).

---

## What is *not* this repo's problem

- **`stackctl`** — the up/down controller for both container layers lives in
  `~/network_lab/`, not here, and hardcodes that path. It moves with the lab.
- **The GPU.** `ollama` runs as a **host systemd service, not a container**, so
  `docker compose down` does not release the card and nothing in
  `docker-compose.yml` requests one. `./stackctl gpu-swap-up` verifies the card
  before bringing the stack up.
- **`faultlab/`** — its own git repository, with its own `.env`. Note
  `fault_lab.py` writes to devices; nothing in *this* repo ever does.

---

## Acceptance gate

The migration is done when all of these pass:

- [ ] `wc -l ~/.config/nettools/known_hosts` → 9
- [ ] `nettools config check` → no problems reported
- [ ] `python -V` → 3.11.x or 3.12.x
- [ ] `make test` → green, `make lint` → clean
- [ ] `python3 scripts/mutate_guards.py` → 71/71 hold
- [ ] `nettools investigate RR1 10.255.0.12 --from-fixtures --label broken --no-model` → `interface_line_down`, exit 1
- [ ] `nettools health --all` → no peer-count drift, no unexpected baseline warnings
- [ ] `nettools investigate RR1 10.255.0.12 --no-model` → `all_layers_healthy`

The first six need no lab. If those six pass and the last two do not, the
repository is fine and the fabric is not yet golden — which is a different
problem, and a better one to have.
