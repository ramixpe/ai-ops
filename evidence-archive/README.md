# Evidence archive

Committed payloads from fault-injection rounds. **This directory is evidence, not
code.** Nothing here is imported, nothing is executed by the test suite, and
`ruff` is configured to skip it — a formatter must not rewrite an artefact that
has to stay byte-identical to what ran.

## What a round is, and why the payload is here rather than in a transcript

The lab can produce faults whose ground truth is known by construction, so the
agent's answer can be **scored** rather than judged. `docs/design/chaos-harness.md`
is the protocol; `docs/build/ROUND-*.md` holds each round's sealed prediction and
its result.

Two rules govern this directory, and both were written after they were broken:

> **A round is archived when its payload is committed.** Until then it is a
> working file that happens to still exist. Round 7 lost a 158-sample result to a
> tidy-up, and a blanket `*.jsonl` in `.gitignore` silently dropped every sample
> from an archive that otherwise looked complete (OBS-131).
>
> **A parsed field is a conclusion; the input is the text it was parsed from.**
> Round 8 archived per-sample derived fields and no raw device output, so when its
> sampler's regex turned out to be wrong the round could not be rescored — 355
> copies of a broken parse and no copy of the line it came from (§6.1d, OBS-135).

The second rule is why some payloads carry raw command text that looks redundant
beside the field derived from it. It is not redundant. It is the only thing that
survives a defect in the derivation.

## What is here

| Round | Contents | Result |
|---|---|---|
| `round5/` | 13 per-probe `investigate` payloads, a timeline, the run log | Coherence passed for 50 s while traffic was blackholed. Temporal coherence and causal correctness are independent |
| `round7/` | Two runs: samples, verdicts, logs | 99 down-port samples, 0 persisting. The `151001` run is the dry-run control |
| `round8/` | Two runs plus **`round8.py`, the instrument itself** | Four claims confirmed; the primary one void, because the sampler's socket regex read `False` in all 195 Established samples |

**`round8/round8.py` is committed deliberately.** That round's numbers are wrong
because of two regexes in that file, and a payload recording what an instrument
concluded, without the instrument, cannot be re-read against a defect in it. The
parser is as much an input to the verdict as the device text is.

## Reading a payload

Samples are JSON Lines, one object per observation, in wall-clock order. Rounds 7
and 8 also write a `verdict.json` — an aggregate computed by the sampler at the
end of the run.

**Read the verdict against the samples, not instead of them.** Round 8's verdict
reports `SEPARATION_OBSERVED: false`, which is true of what the instrument
recorded and says nothing about what happened, because the field it aggregates
never read `true` in any state including a fully established session. The samples
show that; the verdict cannot.

## What is not here

Rounds 1–4 kept findings and metrics only. Their entries in
`tests/test_rounds_regression.py` assert what the finding logic does with a given
rung vector — a true and useful assertion — and **not** that the vector is still
producible from evidence. The two are different claims.
