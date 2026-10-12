# Spec: orq-review-agent skill

Parent ticket: RES-887. Related: RES-1338, RES-1285.

## Problem Statement

Someone with a live orq agent wants to know if it is set up well: are its instructions, tools, skills, evaluators and model in line with our best practices, and where should they start fixing? Today they would have to run four or five assistant-plugins skills one after another (analyze-traces, improve-agent, recommend-evaluators, manage-skills, build-agent) and piece the answer together themselves. Several of those skills also change the agent, while all they wanted was a review. Two checks exist in no skill at all: `{{skill.X}}` references that no longer resolve (they silently render as empty text), and whether the agent's model is beaten on both intelligence and price by another enabled model.

## Solution

A new read-only skill, `orq-review-agent`. You give it an orq agent key and it:

1. resolves the run key with the shared run-key preflight (key resolution only; a draft agent is a valid target)
2. reads the agent config once and looks up each attached tool and evaluator
3. reads the context: the workspace's skills, the evaluators in the agent's project, the model catalogue (`orq models list`, which carries intelligence index, benchmark scores and price), and the last 29 days of traces if there are any
4. runs six checks: run health, instructions, tools, skills, evaluators, model
5. ranks the findings
6. writes one self-contained HTML report into a per-run directory, next to the scratch files it read
7. posts the top finding in chat and offers to run the assistant-plugins skill that fixes it, only after the user says yes

Each finding states the observation, the best practice it relates to, the evidence, a high-level suggestion of what to change, and the skill that fixes it. It never contains the change itself, such as a rewritten prompt, tool description, evaluator or diff. It never writes to the platform.

| Check | Looks at | Fixed by |
|---|---|---|
| Run health | How runs end, errors, iteration and time caps hit, truncation, fallback models firing | `orq-improve-agent` |
| Instructions | The 11 prompting guidelines; tools the instructions name vs tools attached | `orq-improve-agent`; a missing tool has no owning skill |
| Tools | Each description against the tool guide; calls and errors per tool where observable | no owning skill: a coding agent or the orq UI |
| Skills | `{{skill.X}}` / `{{snippet.X}}` references that don't resolve; skills in the agent's project that nothing references | `orq-manage-skills` |
| Evaluators | Attached evaluators, sample rate, runs, pass rate (unvalidated); project evaluators not attached | `orq-recommend-evaluators` |
| Model | Declared and fallback models vs enabled models with the same capabilities: is another one at least as smart for at least 20% less? | `orq-improve-agent` (owns `model.id`) |

## User Stories

1. As an agent owner, I want to review my orq agent by giving only its key, so that I get a full picture without knowing which skills to run.
2. As an agent owner, I want the review to change nothing on the platform, so that I can run it on a production agent without risk.
3. As an agent owner, I want one HTML report per review, so that I can read it at my own pace and share it with my team.
4. As an agent owner, I want the most important finding shown in chat, so that I know where to start without opening the report.
5. As an agent owner, I want the assistant to offer the skill that fixes the top finding, so that I can go from review to fix in one step.
6. As an agent owner, I want that skill to run only after I agree, so that the review never turns into an edit I did not ask for.
7. As an agent owner, I want every finding to show its evidence (a quoted line, a config field, trace ids or counts), so that I can check it myself.
8. As an agent owner, I want every finding to name the best practice it comes from, so that I understand why it matters.
9. As an agent owner, I want every finding to name the skill that fixes it, so that I know what to run next.
10. As an agent owner, I want findings ordered broken first, then trace-backed, then config, then best practice only, so that urgent problems are not buried under style notes.
11. As an agent owner, I want to see how my runs end (stop, length, tool calls, max iterations, max time, errors), so that I can spot caps that cut runs short.
12. As an agent owner, I want to know when a fallback model is serving my traffic, so that I learn my main model is failing without me noticing.
13. As an agent owner, I want cost and latency p50/p95 in the report, so that I have a baseline before changing anything.
14. As an agent owner, I want my instructions checked against the same 11 guidelines orq-improve-agent uses, so that the review and the fix agree.
15. As an agent owner, I want each guideline marked present, weak or missing with the quoted line, so that I can see exactly what is meant.
16. As an agent owner, I want to be told when my instructions mention a tool the agent does not have, so that I catch a broken instruction.
17. As an agent owner, I want to be told when an attached tool is never mentioned in the instructions, so that I know the model has to guess when to use it.
18. As an agent owner, I want to be told when a `{{variable}}` in the instructions is not declared, or a declared one is not used, so that I catch rendering problems.
19. As an agent owner, I want each tool description checked against the 5 rules of the tool guide, so that I know which descriptions cause wrong tool choices.
20. As an agent owner, I want calls and errors per tool over the window, so that I can see which tools fail and which are never used. *Not deliverable on orq-hosted agents as of 2026-10-12: `tool_name` is not emitted on their traces. Reported as unobservable until `tool_name` groups return.*
21. As an agent owner, I want built-in tools reported with numbers only, so that I am not told to rewrite descriptions I cannot edit.
22. As an agent owner, I want every `{{skill.X}}` and `{{snippet.X}}` reference checked against the workspace's skills, so that I learn which ones render as empty text.
23. As an agent owner, I want to see skills in my project that my agent does not reference, labelled as candidates, so that I can decide whether to use them.
24. As an agent owner, I want the review not to confuse platform Skills with `AgentCard.skills`, so that it doesn't report skills that aren't there.
25. As an agent owner, I want my attached evaluators listed with sample rate, runs and pass rate, so that I know what is actually measured.
26. As an agent owner, I want pass rates marked unvalidated, so that I don't take an unchecked judge's score as the truth.
27. As an agent owner, I want to be told when no evaluators are attached, or one has sample rate 0 or no runs, so that I know about gaps in monitoring.
28. As an agent owner, I want evaluator gaps handed to orq-recommend-evaluators, so that the review doesn't redo that skill's work.
29. As an agent owner, I want my declared and fallback models compared with enabled models that have the same capabilities, so that I learn whether I overpay.
30. As an agent owner, I want "dominated" to mean the same as the platform's RES-1338 A1 check, so that the skill and the product never disagree about my agent.
31. As an agent owner with a tool-using agent, I want the model check repeated on the tau2 agentic benchmark, so that a general benchmark does not mislead me.
32. As an agent owner, I want models without benchmark or price data listed as unknown, so that missing data never reads as a pass.
33. As an agent owner, I want a model finding to point to orq-improve-agent, and to tell me to run orq-run-experiment first when my agent has a dataset and evaluators, so that I test a switch before making it.
34. As an agent owner with no traces yet, I want the review to still run on config alone, so that I can review an agent before launch.
35. As an agent owner, I want anything the review could not read listed as unobservable with a reason, so that absence never looks like health.
36. As an agent owner, I want a clear error when the key is wrong or belongs to another project, so that an empty result is not mistaken for a clean one.
37. As an agent owner, I want each review in its own directory named after the agent key and a timestamp, so that I can compare reviews over time and delete one in a single step.
38. As an agent owner who asked to "fix" my agent, I want orq-improve-agent to fire instead of the review, so that I get the skill I meant.
39. As an agent owner who asked "what's failing", I want orq-analyze-traces to fire instead, so that I get a failure analysis.
40. As an agent owner who asked which evals to add, I want orq-recommend-evaluators to fire instead, so that I get ranked evaluator suggestions.
41. As a skill maintainer, I want the tool guide, the 11 prompting guidelines and the Phase 0 list to live once in orq-shared, so that the review and the skills that fix things never drift apart.
42. As a skill maintainer, I want the review to state its model-comparison rule in one place, so that I can update it when RES-1338 changes the definition.
43. As a skill maintainer, I want the report template shipped inside the skill, so that it works in every install without other skills or local files.
44. As a skill maintainer, I want the skill's `allowed-tools` to pre-approve no write verb, so that a mistyped write still prompts. `Write` and `Task` stay unscoped by the harness.
45. As an agent owner, I want evaluators in my project that my agent does not use listed as candidates, so that I know what I could attach without building anything.
46. As an agent owner, I want a tool my instructions require but my agent lacks named as something to add, so that I know which tool to create or attach.

## Implementation Decisions

- **New skill `orq-review-agent`, orq agents only.** It takes an agent key or id. Deployments and local agents are out of v1.
- **Read-only.** `allowed-tools` pre-approves read verbs only: agent, tool, evaluator, skill and model reads, trace aggregate/search/span reads, reporting query. No `create`, `update`, `delete` or `invoke`. `Write` and `Task` are not scoped by the harness; the skill writes only inside its run directory (`agent-review-<key>-<ts>/`: the report and the JSON it read).
- **Suggestions, not changes.** A finding carries observation, best practice, evidence, a high-level suggestion and fixing skill. Candidates (models that beat the current one, unattached evaluators, unreferenced skills) are labelled as candidates. Rewritten text is never produced.
- **Refactor into orq-shared.** Three rubrics move there and become shared references:
  - the tool description guide, from orq-build-agent
  - the 11 prompting guidelines, from orq-improve-agent
  - the Phase 0 list of what to report, from orq-analyze-traces

  The skills they come from link to the shared copy instead of keeping their own. The system-instruction template stays in orq-build-agent, because it is a writing template, not a checklist. orq-shared's contents table lists the new files and the skills that use them.
- **How skills are linked to agents.** No agent has a list of attached skills. The link exists only as `{{skill.X}}` / `{{snippet.X}}` placeholders in `instructions`, matched on the skill's `display_name`, case-sensitive. The check parses the placeholders and resolves each against the workspace's skills. `AgentCard.skills` is ignored.
- **Model check.** Computed by the skill from `orq models list`; no endpoint exists (RES-1285's `pareto-pool` command and a REST `PreviewSmartRouterBands` never shipped). Inputs per model: `metadata.autorouter.v2.intelligence_index`, `price` (blended per 1M tokens), `aa_evaluations.tau2`, and the constraint fields `context_window`, `supports_tool_calling`, `supports_vision`, `supports_structured_outputs`, `enabled`.
  - The rule (RES-1338 A1 with the guardrails Bauke set) lives once, as `RULE` in `scripts/model_check.py`, and is printed in every result; the report quotes it from there.
  - Both models are scored at the agent's reasoning effort when the current model has an index for it, otherwise both at their default effort. Provider aliases of one model are listed once.
  - The same comparison is shown on `tau2` for agents with tools. Models without data are unknown.
  - Resolving the agent's model id follows `orq-shared/resources/model-slugs.md`. An unresolved id lists the closest catalogue names, for the report only.
- **Per-tool errors** are not observable on orq-hosted agents (probed 2026-10-12): `group_by: tool_name` returns only the null bucket, and `status` is trace-scoped.
- **Ranking for the top finding.**
  1. Broken: an unresolved skill reference, a tool required but not attached, sampled runs whose terminal reason is a cap.
  2. Backed by trace evidence, with the sample size.
  3. Mechanical config or catalogue findings. A config contradiction certain to break every run, with no traffic to confirm it, is labelled `predicted broken` and sorts first here.
  4. Best practice only.

  Ties go to whichever affects more traffic. While a broken or predicted-broken finding is open, a model finding's suggestion says to fix that first.
- **Report.** One self-contained HTML file, `report.html`, in the run directory, with one section per check plus an unobservable list. The template ships in the skill's resources; its placeholders use `<!--@NAME-->` so quoted `{{variables}}` cannot collide with them.
- **Hand-off by ownership.** After the report, one chat message: the top finding, the run directory, and a yes/no offer to run the fixing skill. Model findings go to `orq-improve-agent`, which owns `model.id`; `orq-run-experiment` refuses agents without a dataset and evaluators. Tool findings have no owning skill yet, so no skill is offered for them. Nothing runs without a yes.
- **Registration and release.** Registered on every surface validate-skills.mjs checks (skills directory, AGENTS.md, README table, the smoke-test doc, skills-lock.json). Ships a factual CSV and eval cases. MINOR version bump across the four plugin manifests, with a CHANGELOG entry.

## Testing Decisions

- A good test checks what the skill visibly does in a real session: which skill fires, which tools it calls, which tools it never calls. It never checks the wording of SKILL.md. All three test types below already exist in the repo; nothing new is added.
- **Evals in real sessions** (the `skill-tests` harness through `orq launch`). Prior art: orq-improve-agent's fires, near-miss and reads-config-first cases.
  - A fires case: a request to review an orq agent fires `orq-review-agent`.
  - Near-miss cases: "fix support-bot" → `orq-improve-agent`; "what's failing in support-bot" → `orq-analyze-traces`; "which evals should support-bot have" → `orq-recommend-evaluators`.
  - Fires cases on three phrasings: "review", "audit", "health check".
  - A behavioural case on the fixture agent `support-bot` in the skill-evals project that forbids `create_*`, `update_*`, `delete_*` and `invoke_*`. The runner sees MCP tool names only, so CLI write verbs are kept out by `allowed-tools`, not by this case.
  - A behavioural case on an agent in another project: the skill must ask for the right key or workspace.
  - Not covered: an unresolved `{{skill.X}}` reference, which needs a second fixture agent.
- **Factual drift checks.** A CSV for the skill checks that the CLI commands, flags and MCP tools it names still exist. The runner has no check for a JSON field, so the `orq models list` field paths are not covered; a change there shows up as `unknown` results.
- **`validate-skills.mjs`.** Checks registration on every surface, the frontmatter field set and shapes, and that links resolve after the rubrics move into orq-shared.
- **Not tested:** the content of the finished report. The harness stops after the first turn. Checking the report would need a longer session and a separate decision.

## Out of Scope

- Any write to the platform: attaching evaluators, editing instructions, tools or skills, switching models.
- Writing fix text: prompt rewrites, tool descriptions, new evaluators.
- Designing new tools. The review only names a tool the instructions require but the agent lacks; anything beyond that needs a full failure analysis (orq-analyze-traces).
- A floor for low absolute intelligence (decided 2026-10-12: left out). A cheap, weak model that nothing dominates passes the model check.
- Dollar savings estimates (RES-1338), scheduled reviews (RES-1340), platform UI (RES-1342).
- Deployments and local agents.
- Publishing findings as RES-1338 Recommendation objects.

## Further Notes

- Bauke asked on 2026-08-18 to park this for RES-1338. The ticket went back to Todo on 2026-09-21. The skill uses A1's definition of a dominated model so the two cannot disagree. The `tau2` comparison shipped without Arian's confirmation; it still needs it.
- Settled (Bauke, 2026-10-12): a finding must be actionable, "you can fix it like this or you should do this thing", high level, no diff. Each finding now carries a suggestion; rewritten text stays out.
- Settled (Bauke, 2026-10-12): the A1 guardrails are text and images only (rarer modalities only for agents built for them), tool calling the same as the current model, and context window at least min(current, 300k), the full window for coding agents.
- Open: no skill owns editing or adding an existing agent's tools. orq-improve-agent deliberately leaves tool definitions alone, and orq-build-agent is built for new agents. The review says so and offers no skill for tool findings.
- Open: the review's probe found agent terminal state on `span.agent` and `orq agents get-response` returning 404, contradicting `orq-shared/resources/trace-queries.md` §3.5, which `orq-improve-agent` relies on. Being re-probed separately.
- Settled (Bauke, 2026-10-12): ENG-2306 hill-climbs a metric; this skill is a general health check and does not.
