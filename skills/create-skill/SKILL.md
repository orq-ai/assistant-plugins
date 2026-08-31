---
name: create-skill
description: >
  Build or update an agent skill from an API, CLI, or MCP surface. Use when
  the user wants to document a new capability surface as a skill, or when an
  existing skill's contract is stale and needs re-verification. Do NOT use
  when editing an existing skill without re-probing the surface, or when
  documenting internal code conventions (that belongs in CLAUDE.md).
allowed-tools: Read, Write, Edit, Grep, Glob, Bash, AskUserQuestion, WebFetch, WebSearch, ToolSearch
---

# Create Skill

> `allowed-tools` grants bare `Bash` because this skill probes unknown surfaces: arbitrary CLIs via `--help`, `curl` against undocumented APIs, whatever the target requires. The surface is not known at skill-authoring time, so the grant cannot be enumerated. Every other tool is standard read/write. **The output skill this produces must NOT inherit the broad grant** -- its `allowed-tools` enumerates write operations individually (see [`resources/writing-guide.md`](resources/writing-guide.md)).

You are a **skill author**. You take a capability **surface** (an API, a CLI, or an MCP server), build a verified **inventory** of what it offers, and write it into a **contract**: a SKILL.md the agent can rely on because every claim in it was tested.

## Gotchas (read first)

1. **A written SKILL.md is not a registered skill.** In a repo with a contribution contract (this one has five surfaces, see Phase 6), a skill that exists only as `skills/<name>/SKILL.md` fails CI. Phase 6 is not optional there.
2. **The probe environment lies as readily as the docs.** A stray `ORQ_API_KEY` in the shell silently redirected every `orq models list` read to another workspace and returned `HTTP 401`, while `orq auth whoami` kept reporting the session's workspace. Before recording a failure as a surface gotcha, check whether your own environment caused it.
3. **`$?` after a pipeline is the last command's status.** `orq models list 2>&1 | head -5; echo $?` printed 0 for a command that exits 1. Measure exit codes with the command alone, redirecting to a file if you need the output — a piped measurement produced a wrong gotcha on the first run of this skill.
4. **An empty result is the most common disguised error.** `list_skills` with `starting_after: skill_doesnotexist` returns `{"data":[],"has_more":false}` — no error, no hint the cursor was garbage. Every list operation needs one deliberate bad-input call before you can claim it validates anything.
5. **The scratchpad holds the only evidence** until the gotchas land in the skill. Delete it in Phase 7, after the skill is written and registered — not before.

## When NOT to use

- Editing an existing skill when the surface hasn't changed (just edit the file directly)
- Documenting internal code or repo conventions (that belongs in CLAUDE.md or docs/)
- Building a skill from scratch without a live surface to probe (write it by hand with the template)

## Constraints

- Verify every endpoint, flag, schema, and behaviour against the live surface or its current docs. Training-data claims are hypotheses until confirmed.
- Date the contract in the CHANGELOG entry that ships the skill, so staleness is traceable to a commit.
- Search for an existing skill before creating a new one. Duplicates rot independently.
- Show the complete skill to the user and get approval before writing.

## Steps

### Phase 1: Gather requirements

Use `AskUserQuestion` in two rounds.

**Round 1 — Create or update?**

Ask a single question: does the user want to **create a new skill** or **update an existing one**?

- **Create new**: a brand-new skill for a surface that has no skill yet.
- **Update existing**: re-probe a surface whose skill is stale or incomplete.

If the user chose update, immediately search for the existing skill (Phase 3 logic) so you can show them what exists before asking for more detail.

**Round 2 — Surface and scope**

Ask two questions in one `AskUserQuestion` call:

1. **Describe what you want.** The surface to probe: a name, URL, CLI tool, MCP server, file path, or "I'll paste it". If updating, pre-fill what you found and ask the user to confirm or correct.

2. **Fast or thorough?**
   - **Fast**: read docs, extract the inventory, spot-check 2-3 operations. Minutes.
   - **Thorough**: test every operation with real calls, including error and edge cases, and record each gotcha from the actual response. Longer.

If the user already provided some of this in their initial message, skip the answered questions and ask only for what is missing.

**Done when:** you know create vs. update, have a surface to probe, and have a scope mode.

### Phase 2: Build the inventory

Read the source material and extract a structured inventory.

**For an API:** endpoints (method, path, required/optional params, response shape), authentication pattern, error shapes and status codes, rate limits, pagination, versioning.

**For a CLI:** commands and subcommands (`<tool> --help`, `<tool> <cmd> --help`), flags (required, defaults, valid values), input/output formats, exit codes, environment variables.

**For MCP:** use `ToolSearch` (`select:<tool>,<tool>` for exact names) to pull tool schemas. `ToolSearch` returns the **input** schema only — descriptions are often one terse line and there is no declared return shape, so the return shape has to come from an actual call. For each tool: name, description, required/optional params, observed return shape; resource types if exposed; which tools are read-only vs. write/mutate.

**For all types:**
- Group related operations (CRUD sets, query variants, lifecycle commands)
- Mark which operations are safe (read) vs. dangerous (write/delete)
- Identify common workflows (sequences of calls that accomplish a task)

Write the inventory to the scratchpad: `.create-skill/<surface-name>.md`, relative to the repo root (or the working directory outside a repo). One directory, one run, deleted whole in Phase 7. Never write it inside `skills/` — a stray inventory there gets committed and hashed into `skills-lock.json`.

**Done when:** every operation the surface exposes is listed in the scratchpad with its params, response shape, and safe/dangerous classification.

### Phase 3: Check for existing skills

If the user chose **update** in Phase 1, you already searched and found the skill. Read it now and diff against the inventory from Phase 2. Scope Phase 4 testing to what is new or changed.

If the user chose **create**, search to make sure no skill already covers this surface:

```
Glob: skills/*/SKILL.md
Grep: <surface name or key terms> in those files
```

Plugin-scoped skills (an `orq:` or `superpowers:` prefix) are registered externally and never appear under `skills/`, so the Glob misses them. Read the available-skills list in your own context — the harness injects it at session start — and scan it for the surface name. Do this whether or not `skills/` exists; outside a plugin repo it is the only listing there is.

**Match found:** show the user and ask whether to update the existing skill instead of creating a duplicate.

**No match:** proceed to create.

**Done when:** you have the existing skill content (if updating) or confirmed no duplicate exists (if creating).

### Phase 4: Test and verify

The mode chosen in Phase 1 sets what the scratchpad must contain, and Phase 5 refuses to ship a claim whose evidence is not there. Fast and thorough differ in how many operations carry a recorded call, never in whether the recording is optional.

**Fast mode:**
- Confirm the surface exists (hit one endpoint, run one command, call one tool)
- Spot-check 2-3 operations likely to have gotchas (create, error case, pagination boundary)
- Record anything that behaved differently from the docs
- Every operation without a recorded call is `[unverified: fast mode, doc-only]`. That marking is the mode's cost and it belongs in the output skill.

**Thorough mode:**

Test every operation with real calls against the live surface. Do not trust docs alone.

**For each operation:**
1. **Run it** with a concrete example (real IDs, real data, not placeholders). For read operations, call them. For write operations, create a test entity, verify it exists, then clean it up.
2. **Record the exact call and the actual response** in the scratchpad. Copy the real output, not a paraphrase.
3. **Compare to docs**: note every difference between what the docs say and what actually happened (different field names, missing fields, extra fields, different status codes, different error shapes).
4. **Test the error path**: call with missing required params, invalid values, or a nonexistent ID. Record the actual error response shape — including the case where there isn't one. A list operation that answers a garbage cursor with an empty array is a silent failure, and it only shows up if you ask for something that cannot exist.

**For common workflows**, run the full sequence end-to-end: e.g. create -> list (verify it appears) -> get by ID -> update -> get (verify change) -> delete -> get (verify 404). This catches ordering dependencies, eventual consistency, and silent failures.

**For CLI surfaces**, run each command and subcommand. Capture `--help` output AND run a real invocation — help text is where CLIs lie most (duplicate rows, flags that no longer exist). Flags that accept values: test with a real value and confirm the output format. Check the exit code separately from the output; a CLI that prints help on an unknown subcommand may still exit 0, which makes the failure invisible to any script wrapping it.

**For MCP surfaces**, load the schemas in one batched `ToolSearch` call, then invoke each tool with real parameters and record the response — the return shape is not in the schema, so an uncalled tool has an unknown output. Batch the loads: one `ToolSearch` per tool wastes a round-trip each.

**Large surfaces (20+ operations):** confirm with the user before proceeding. Show the count and estimated time.

**When an operation cannot be tested** (auth unavailable, endpoint behind VPN, rate limit hit, destructive write on production data): mark it `[unverified: <reason>]` in the scratchpad. Report the count of unverified operations to the user before proceeding.

Record gotchas in the scratchpad with evidence:

```
Gotcha 1: <command> with <flag> silently returns empty instead of erroring
  Tested: <exact call>
  Expected: 404 or error message
  Actual: 200 with empty array
```

**Done when:** every operation has either (a) a recorded real call with its actual response, or (b) an `[unverified: reason]` mark. Every gotcha has the exact call that produced it.

### Phase 5: Write the contract

Read [`resources/writing-guide.md`](resources/writing-guide.md) first. Then structure the output following [`resources/template.md`](resources/template.md). Fill the skeleton with the verified inventory and gotchas from the scratchpad.

**Naming**: use kebab-case. Prefix with the platform domain when the skill wraps a specific product (e.g. `orq-analyze-trace-failures`, `orq-compare-agents`). Cross-cutting skills use a descriptive name (e.g. `create-skill`).

**Grouping**: organize capability sections by user workflow (what the user is trying to do), not by HTTP method or CLI subcommand hierarchy. Each group should correspond to a task the agent would perform.

**Companion skills**: search `skills/*/SKILL.md` for related skills and populate the Companion skills section with verified names and one-line boundaries. Do not guess skill names.

**Review the draft adversarially before anyone sees it.** For each claim, ask:

1. **Did I actually test this, or am I restating the docs?** Check each claim against the scratchpad: thorough mode means a recorded call per operation, fast mode means a recorded call for the spot-checks and `[unverified: fast mode, doc-only]` on the rest. A claim with no entry behind it either gets tested now or ships marked — the mode does not change that, only how much of the surface it covers.
2. **Would an agent following this skill hit a wall?** Walk each workflow as if executing it for the first time. Missing setup steps, unstated prerequisites, parameter values that only make sense if you already know the surface.
3. **Are the gotchas complete?** Re-read the test results for any surprise that did not make it into the skill. Silent failures, unexpected defaults, and undocumented required fields are the most common omissions.
4. **Are the examples real?** Every command, call, or snippet must be one you ran (thorough) or took from current docs (fast). No invented examples.

Fix what the review catches, running additional tests if the fix needs them, then run the failure-mode checklist from the writing guide against the corrected draft.

Output the complete skill as text so the user can read it, then ask for approval via `AskUserQuestion` (approve / request changes). Then write to `skills/<name>/SKILL.md`.

**If updating:** use `Edit` on the existing file rather than rewriting, unless the changes are so extensive that a rewrite is cleaner. Show the diff first.

**Done when:** the user approved the content, the file is written, the writing-guide checklist passes, no untested doc claim is left unmarked, and every claim traces to a test result (thorough) or doc reference (fast) in the scratchpad.

### Phase 6: Register the skill

A SKILL.md alone is not a registered skill. Check whether the repo has a contribution contract: a `CLAUDE.md` or `AGENTS.md` describing where skills must be listed, or a validator under `tests/`.

**No contract found:** skip to Phase 7.

**Contract found:** follow it, then run its validator. In this repo the contract is five surfaces plus a lock file:

- `skills/<name>/SKILL.md` (written in Phase 5)
- `agents/AGENTS.md` — the path list and the `<available_skills>` description block
- `README.md` — a row in the skills table, label and link target agreeing
- `tests/skills.md` — a `## \`<name>\`` smoke-test section, plus the Critical Files list (free prose, nothing validates it)
- `skills-lock.json` — regenerate with `git add skills/ && node tests/scripts/validate-skills.mjs --fix` (it hashes **staged** content)
- Version bump across all four `plugin.json` manifests plus a `CHANGELOG.md` entry — a new skill is a MINOR bump

**Done when:** the repo's validator passes, or no contract exists.

### Phase 7: Clean up

Delete the `.create-skill/` scratchpad directory. The evidence now lives in the skill itself: gotchas carry the exact calls that produced them.

**Done when:** `.create-skill/` no longer exists.

## Done When

All of these are true:

- The SKILL.md is written (or updated) and the user approved it
- The adversarial review and the writing-guide failure-mode checklist both pass, with no untested claims remaining
- Every claim traces to a test result (thorough) or doc reference (fast)
- Unverified operations are marked `[unverified: reason]` in the output
- Companion skills section references verified skill names
- The skill is registered on every surface the repo's contract requires, and its validator passes
- The scratchpad is deleted

## Companion skills

- **orq-manage-skills** — orq.ai Skills, the *platform* entity injected into prompts via `{{skill.<display_name>}}`. Different thing entirely; this skill writes SKILL.md files for coding agents.
- **orq-cli** — reference for the `orq` CLI itself. Use it when probing orq as a surface; this skill only tells you how to write up what you find.
