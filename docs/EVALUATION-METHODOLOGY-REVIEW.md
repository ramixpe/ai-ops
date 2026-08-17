# Review of the Evaluation Methodology

## 1. Verdict on the method

The method is strong for **case-level causal testing** and currently inadequate for **performance estimation**.

The team has done several things correctly that many evaluations do not:

- the diagnosis is written before the tool runs;
- the injected condition is concealed from the diagnostician and tool;
- predictions and falsifiers are recorded before observation;
- restoration is independently verified;
- true negatives and abstentions are treated as first-class outcomes;
- failed trials are preserved rather than rewritten after a fix.

Those choices substantially reduce hindsight bias, label contamination, and result laundering. The standard objection that “lab faults are artificial” does not defeat the method’s internal validity. Injection is an appropriate way to test whether a specified mechanism produces a specified observation under controlled conditions.

The problem is that the evaluation has not defined the population to which an accuracy claim would apply. A result such as “94% correct” needs an estimand:

> Correct on what distribution of incidents, devices, software versions, fault multiplicities, network states and evidence failures?

Randomly drawing from a tool-aware catalogue estimates performance on that catalogue’s draw distribution. Running it continuously for 24 hours does not make the catalogue representative. The duration of a soak is not a sampling frame.

The team is right not to present `3/4` as production accuracy. It is wrong to say the number must not be reported at all. For the pre-fix system, `3/4` is the observed aggregate result under the four chosen cases. It should be reported alongside the strata:

- on-path injected faults: `3/3` correct;
- no-fault-on-path cases: `0/1` correct;
- all selected cases: `3/4` correct.

None is an estimate of field accuracy because the cases were not sampled from a defined field population. Refusing the aggregate because it combines a successful category with a structural failure is the wrong rationale. Deployment performance necessarily combines categories. The appropriate combination depends on their production prevalence, which is currently unknown.

The false-positive fix creates a new system version. The old `0/1` result remains part of the historical corpus; the fixed version needs fresh prospective evaluation. A regression test derived from the failed case demonstrates that the known input now produces the intended output. It does not establish that the false-positive class is “closed” or that paging is safe.

## 2. What the current evidence supports

### What n=4 means

`n=4` is meaningful for existence and protocol validation:

1. The complete blind-trial procedure can be executed in the lab.
2. The tested system version produced correct localisations in three specific on-path cases.
3. Those cases exercised three different reported rungs and at least two different walk behaviors.
4. One selected true-negative case produced a false positive.
5. A pre-registered prediction of that false positive was borne out.
6. The evaluation protocol was capable of exposing a defect that ordinary fixtures had missed.

These are demonstrations, not rates. The defensible wording is “the tool succeeded on these three discriminating cases,” not “fault localisation is a pattern.”

Even under an unjustifiably generous assumption that the three on-path cases were independent random draws from one stable population, `3/3` has a two-sided 95% exact binomial interval of approximately **29% to 100%**. The interval is not the main problem—the non-random selection is—but it illustrates how little rate information three successes contain.

Likewise, `3/4` has a two-sided 95% exact interval of approximately **19% to 99%** under random sampling. Quoting that interval would still overstate the evidence because the four trials were deliberately constructed rather than sampled.

There is no universal minimum `n`; it depends on the claim. Under genuinely representative, independent sampling:

| Intended claim | Approximate requirement |
|---|---:|
| Demonstrate that one named case can succeed or fail | One valid discriminating trial for that case |
| Exercise all five single-fault rungs | At least one valid case per rung, plus controls; this establishes coverage, not accuracy |
| With zero observed critical errors, place a one-sided 95% upper bound below 5% | **59** representative trials in the relevant stratum |
| With zero observed critical errors, place that upper bound below 1% | **299** representative trials in the relevant stratum |
| Estimate a proportion to roughly ±10 percentage points at 95% confidence in the worst case | About **97** independent observations per reported stratum |
| Estimate a proportion to roughly ±5 points | About **385** per stratum |
| Detect a change from 80% to 90% with 80% power at two-sided 5% significance using independent groups | About **199 per group** |

These numbers do not rescue a biased catalogue. Fifty-nine repetitions of one tool-aware true negative do not bound the false-positive rate on production incidents. Sampling validity comes before sample size.

### The prospective prediction

The prediction made before round 4 is materially more valuable than a post-hoc explanation for one purpose: it supports the claim that the team understood the mechanism of that failure before seeing the live outcome. The stated falsifiers make the evidence stronger because the prediction could have failed in recognizable ways.

It is not worth more than one observation in a performance estimate. The nine predicted fields are correlated consequences of the same state vector, not nine independent confirmations. Because much of the prediction followed deterministically from the code, the live round primarily tested whether the lab premise was true: that the IGP would reconverge and the session would stay established. It did not estimate how often similar failures occur.

The team should give the round high **mechanistic** weight and ordinary **statistical** weight. Calling it “9/9” outside a field-by-field conformance table would over-weight it.

### “Answer weaker than the evidence” is a real distinction

Truth and informativeness are different properties. “Transport is blocked” may be true while failing to report an administrative shutdown that the available evidence identified. Operationally, the weaker answer may send the user into another investigation that the stronger answer would have avoided.

The distinction becomes over-refinement if reviewers assign it after reading the outcome without a predeclared rule. It should be scored on a second axis, not made a mutually exclusive substitute for correctness:

- **truth/localisation:** was the asserted cause or layer correct;
- **specificity/completeness:** did the answer reach the most specific claim justified by the collected evidence;
- **unsupported excess:** did it state more than the evidence justified.

Before each trial, the sealed record should state the minimally correct answer and the maximally supported answer. Independent scorers should apply that rubric without seeing whether the tool or human produced the text. Inter-rater agreement should be reported. Without this, “weaker than supported” can become a hindsight category created whenever an answer feels disappointing.

### The confusion matrix is useful, but only conditionally

A true-rung by reported-rung confusion matrix is the correct instrument for one narrow estimand:

> Given one applicable, stable, single fault whose correct cause maps to one rung, which rung did the tool report?

For that question, the standard objection that one accuracy number hides clustering is correct, and a matrix exposes the clustering.

The complete outcome space is not a flat multiclass problem:

1. Does an applicable flow exist?
2. Is there a fault on the investigated path?
3. Is the ground truth one cause or a set of causes?
4. Was the layer correct?
5. Was the device/object correct?
6. Was the answer sufficiently specific?
7. Did the tool abstain?
8. Did collection fail?

`undetermined` is a decision outcome, not a true fault class. Wrong-device is orthogonal to wrong-rung. “Too high” and “too low” have an ordered distance. Multi-fault cases have a set-valued truth and cannot occupy one matrix row honestly.

Keep the rung matrix for the conditional single-fault analysis. Add separate measurements for applicability, fault/no-fault discrimination, signed rung distance, device localisation, abstention, specificity and multi-cause set precision/recall. Do not collapse them into a single “correct” column.

## 3. What it does not support but is being claimed

### “Fault localisation is provisionally a pattern”

The evidence supports three successful demonstrations chosen to exercise the ladder. It does not support repeatability over an incident population. “Pattern” implies behavior expected to persist beyond the selected cases; `n=3` and tool-aware selection provide no basis for that inference.

The selection effect is potentially the dominant bias. A person who knows the ladder can choose cases that:

- map cleanly to one rung;
- have outputs the parsers already cover;
- avoid ambiguous ownership;
- occur after convergence has settled;
- use the lab’s router-ID and topology conventions;
- omit faults for which no flow applies;
- avoid multi-fault and pre-existing-drift states.

Tool awareness can also produce unusually adversarial cases, so the bias is not guaranteed to be optimistic. Its direction and magnitude must be measured rather than asserted.

Use two prospectively defined arms:

- **tool-aware coverage arm:** cases deliberately constructed to exercise every branch and known risk;
- **tool-blind applicability arm:** incidents selected by operators who receive only the user-facing capability statement, preferably sampled from historical tickets and alerts before anyone maps them onto the ladder.

Run the same frozen tool version on both. Estimate the difference in applicable-flow rate, correct-localisation rate, wrong-device rate, false-positive rate and abstention rate, with confidence intervals. Stratify by fault class or use a model with fault class/device as covariates. The between-arm difference is the measured selection effect. Randomizing within a catalogue does not measure catalogue-selection bias.

### “The false-positive class is closed”

The known case has been fixed. The class has not been evaluated after the fix on fresh examples. Variations involving a different redundant topology, a different device, an unrelated pre-existing down interface, partial convergence or two simultaneous conditions could still fail.

The defensible claim is “the known round-4 vector now passes its regression test.”

### “Exit 1 is now safe to page on”

This is the clearest unsupported current claim. It appears in `SESSION-HANDOVER.md` after one false-positive mechanism was fixed.

Paging safety is a decision-theoretic claim about false-positive cost at production prevalence. It requires, at minimum:

- a defined incident population;
- a false-positive tolerance chosen before testing;
- representative controls and true negatives;
- a confidence bound below that tolerance;
- tests of transient, multi-fault and pre-existing-drift conditions;
- validation of the fixed version rather than the version that produced the original four rows.

One known true-negative regression does not support paging safety.

### “The system fails toward `undetermined` rather than a wrong answer”

The methodology is designed to measure this, but the current evidence does not establish it. One of four trials produced a confident false positive. The direction of failure must be reported as an empirical conditional distribution among failures, with confidence intervals, not as a design intention.

### “We know the failure modes, their rates and their shapes”

That is a valid target for the planned harness, not a present result. The current four trials identify several shapes. They estimate no rates and cannot establish completeness of the failure-mode catalogue.

## 4. What no amount of this method will ever establish

The distinction here is not “more data needed.” The current data-generating process cannot support these conclusions regardless of `n`.

### Production accuracy from a tool-aware injected catalogue

Ten thousand draws from the same catalogue estimate performance on that catalogue. They do not estimate performance on production incidents unless the catalogue and its sampling weights are shown to represent production. A 24-hour duration changes neither fact.

Production performance requires an independent incident-derived sampling frame or a justified model that reweights experimental strata using measured field prevalence.

### Completeness against novel faults

An enumerated injection catalogue cannot establish performance on fault classes absent from the catalogue. A holdout containing a few unseen classes tests those classes; it does not establish open-world coverage.

The appropriate measurable quantity is the **applicable-flow rate** on independently sampled incidents, including `no_applicable_flow` in the denominator.

### A zero probability of confident error

No finite successful trial set proves that the system will never be confidently wrong. It can only place a bound on error probability under a specified distribution. Rare, correlated and previously unseen failures remain outside that bound.

### Cross-vendor, production-scale or software-release generalisation

Repeated trials on one topology, one vendor and one release cannot establish performance on 2,000 devices, other IOS-XR releases, Junos, different routing designs or different operational data quality. These require separate environments treated as sampling clusters, not more repetitions on the original lab.

### Correctness during convergence

The protocol explicitly waits until propagation settles. That is appropriate for testing steady-state localisation. It can never establish correctness while the network is changing, no matter how many settled trials are run. A separate dynamic protocol must invoke the tool at randomized offsets during failure propagation and recovery.

### Operational usefulness

Rung accuracy does not establish that operators resolve incidents faster, escalate correctly, or avoid harmful actions. That requires a human-factors comparison against the current runbook using time to correct decision, unnecessary device accesses, wrong-team escalations and operator override rates.

### Root-cause construct validity from injection identity alone

The injected command is the intervention, not automatically the operational root cause. Round 4 proved the distinction: a real interface change occurred without causing a path fault. If the expected rung is assigned by the same person who designed the ladder, scoring can become circular.

Ground truth should include verified post-injection network effect and a counterfactual statement: what service/session behavior changed because of the intervention, and what restoration reverses it. For ambiguous cases, an independent adjudication panel or post-restoration verification is required. More repetitions do not repair a circular label definition.

## 5. Specific measurement recommendations, ranked

### 1. Define the estimands and decision thresholds before the harness runs

Write a versioned analysis plan stating exactly which claims will be made. At minimum:

- conditional single-fault rung accuracy;
- end-to-end applicable-case accuracy;
- false-positive rate on controls and resilient changes;
- false-negative rate;
- wrong-device rate;
- abstention rate;
- error rate;
- specificity shortfall rate;
- time to answer.

For each, name the target population, unit of analysis, exclusions, confidence interval, and acceptance threshold. Without this, the team will choose the denominator after seeing the results.

### 2. Separate coverage testing from field-representative testing

Keep the tool-aware catalogue; it is valuable for branch coverage and adversarial mechanism tests. Do not use it to estimate field accuracy.

Create an independent arm from historical incidents, alerts and operator-written scenarios. Select cases before deciding whether the current flow can answer them. Record `no_applicable_flow` rather than removing them. The fraction excluded by scope is itself a primary result.

### 3. Use three datasets, not two

- **Development set:** visible and repeatedly used for fixing.
- **Regression set:** visible after first failure and rerun after every change. It prevents known defects from returning.
- **One-shot audit set:** inaccessible to developers, evaluated once on a frozen release.

The planned holdout cannot also be rerun “after every subsequent change.” Once its results influence a fix, model choice, parser or prompt, it is a regression set. Replace spent audit cases with newly authored cases and report how many audit looks occurred.

### 4. Make holdout governance concrete

Common leakage routes that apply here include:

- the same tool-aware person defining both development and holdout catalogues;
- developers knowing the holdout fault classes even if exact parameters are hidden;
- repeated aggregate holdout scores guiding changes;
- using the same devices, topology and output shapes in both sets;
- inspecting a failed holdout case and then testing the fix on the remaining holdout;
- parser and prompt work informed by outputs from the same lab;
- injector usernames, commit records or timing signatures revealing that a drill is running;
- locally rewriteable version-control history being treated as an immutable seal.

The current separate injector, pre-run diagnosis and control-arm proposal mitigate direct label leakage. They do not mitigate catalogue-level or topology-level leakage.

Store audit definitions under a separate principal. Commit hashes are useful provenance, but a local Git history can be amended or rebased; use an append-only remote log, signed timestamp or independent witness for sealing. Predeclare what summary, if any, may be revealed before the audit set is considered spent.

### 5. Use a layered scorecard around the confusion matrix

For each trial report:

1. applicability;
2. valid/invalid trial and why;
3. fault present versus absent;
4. true cause set and reported cause set;
5. rung and signed rung distance;
6. device/object localisation;
7. correctness;
8. specificity relative to the sealed maximum-supported answer;
9. abstention or execution error;
10. evidence and timeline coverage;
11. latency.

For multi-fault trials use exact-set accuracy plus cause precision and recall. A single rung confusion matrix cannot represent them.

### 6. Size each stratum from the claim, not from a 24-hour clock

If paging requires the true-negative false-positive rate to be below 5%, and the team observes zero false positives, collect at least 59 representative, independent true-negative/control trials for a one-sided 95% bound below 5%. If the required bound is 1%, collect at least 299.

Repeat the calculation for each safety-critical stratum. Account for repeated devices and fault families as clusters; fifty trials on one device with one template are not fifty independent observations. Report cluster-robust or hierarchical intervals rather than naïve binomial intervals when cases share device, session or injected mechanism.

### 7. Re-evaluate every fix prospectively

Preserve the original failure. Add the known case to regression. Then test the frozen fix on fresh, independently authored variants before claiming the class resolved.

For B-428, that means new resilient-change cases across other devices and redundancy shapes, including pre-existing unrelated faults. Do not count the original case or its direct regression as fresh validation.

### 8. Maintain a prediction registry separate from the accuracy corpus

Record prediction, rationale, falsifiers, timestamp and outcome. Score prediction specificity: “the tool will fail somehow” should not receive the same credit as a full state vector with named falsifiers.

Use this registry to evaluate mechanistic understanding and prospective reasoning. Do not add its field-level matches to the trial count or accuracy denominator.

### 9. Measure selective risk and operator utility

For refusals, report:

- coverage: fraction of valid cases receiving a conclusive answer;
- selective risk: error rate among conclusive answers;
- recovery: fraction of abstentions that become correct after recollection rather than code changes.

This directly tests whether `undetermined` buys safety or merely reflects brittle collection.

Separately run a randomized operator study comparing tool-assisted and normal runbook handling. Measure time to correct diagnosis, commands/logins used, wrong-device investigation, escalation accuracy and confidence calibration. Rung accuracy alone cannot answer whether the tool helps an operator.

### 10. Publish every exclusion and invalid trial

Record failed injection, incomplete propagation, supervisor interruption, restoration failure, evidence-collection failure and scoring ambiguity. Report counts before and after exclusion by arm.

An evaluation that silently retries invalid or unfavorable trials selects for clean successes. The invalid-trial rate is itself evidence about whether a continuous harness can measure what it claims.

## 6. What would make you trust the results

I would trust a narrowly stated result if all of the following were true:

1. The tool version, prompts, parsers, inventory and analysis plan were frozen before the audit.
2. The claim named its target population and acceptance threshold in advance.
3. Tool-aware coverage cases and independently sourced incident cases were reported separately.
4. A development set, reusable regression set and one-shot audit set had distinct governance.
5. Audit cases were authored and held by someone who did not build or tune the tool.
6. The audit was run once, with all exclusions and invalid trials reported.
7. Ground-truth labels described verified network effect, not merely the injected command or the rung the designer expected.
8. Single-fault, true-negative, multi-fault and dynamic-convergence protocols were separate strata.
9. Sample sizes were derived from stated error bounds, with clustering by device and fault family handled explicitly.
10. Results included confidence intervals, applicability, abstention, wrong-device, false-positive and specificity-shortfall rates—not only rung accuracy.
11. A fresh prospective set validated fixes; known failures served only as regression cases.
12. At least one second topology or software-release cluster reproduced the central result.
13. Operator-assisted trials showed an improvement in correct decision time without increased wrong escalation or over-trust.

Under those conditions, a claim such as the following would be supportable:

> On the frozen IOS-XR BGP-session flow, across an independently authored audit set of specified single-fault and true-negative scenarios, the tool produced a conclusive answer in X% of valid cases. Among conclusive answers, Y% localised the correct rung and Z% the correct device; the one-sided 95% upper bound on false positives in the declared true-negative stratum was P%. These results do not cover multi-fault incidents, convergence-time observations, other flows, vendors or topologies.

That is less impressive than “the tool works” and much more useful. It tells a reader exactly what was measured, how uncertain it remains, and where the result stops.
