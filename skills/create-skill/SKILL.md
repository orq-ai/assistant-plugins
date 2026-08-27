---
name: create-skill
description: >
  Build or update an agent skill from an API, CLI, or MCP surface. Use when
  the user wants to document a new capability surface as a skill, or when an
  existing skill's contract is stale and needs re-verification. Do NOT use
  when editing an existing skill without re-probing the surface, or when
  documenting internal code conventions (that belongs in CLAUDE.md).
allowed-tools: Read, Write, Edit, Grep, Glob, Bash, AskUserQuestion, WebFetch, WebSearch, ToolSearch
metadata:
  verified: "2026-08-27"
  surface: meta
  source: "skills/create-skill/resources/"
---

# Create Skill

> `allowed-tools` grants bare `Bash` because this skill probes unknown surfaces: arbitrary CLIs via `--help`, `curl` against undocumented APIs, whatever the target requires. The surface is not known at skill-authoring time, so the grant cannot be enumerated. Every other tool is standard read/write. **The output skill this produces must NOT inherit the broad grant** -- its `allowed-tools` enumerates write operations individually (see [`resources/writing-guide.md`](resources/writing-guide.md)).

You are a **skill author**. You take a capability **surface** (an API, a CLI, or an MCP server), build a verified **inventory** of what it offers, and write it into a **contract**: a SKILL.md the agent can rely on because every claim in it was tested.

## When NOT to use

- Editing an existing skill when the surface hasn't changed (just edit the file directly)
- Documenting internal code or repo conventions (that belongs in CLAUDE.md or docs/)
- Building a skill from scratch without a live surface to probe (write it by hand with the template)

## Constraints

- Verify every endpoint, flag, schema, and behaviour against the live surface or its current docs. Training-data claims are hypotheses until confirmed.
- Date the contract. The frontmatter carries `verified: YYYY-MM-DD` in metadata so staleness is visible.
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

**For MCP:** use `ToolSearch` to pull tool schemas; for each tool: name, description, required/optional params, return shape; resource types if exposed; which tools are read-only vs. write/mutate.

**For all types:**
- Group related operations (CRUD sets, query variants, lifecycle commands)
- Mark which operations are safe (read) vs. dangerous (write/delete)
- Identify common workflows (sequences of calls that accomplish a task)

Write the inventory to a scratchpad file.

**Done when:** every operation the surface exposes is listed in the scratchpad with its params, response shape, and safe/dangerous classification.

### Phase 3: Check for existing skills

If the user chose **update** in Phase 1, you already searched and found the skill. Read it now and diff against the inventory from Phase 2. Scope Phase 4 testing to what is new or changed.

If the user chose **create**, search to make sure no skill already covers this surface:

```
Glob: skills/*/SKILL.md
Grep: <surface name or key terms> in those files
```

If no `skills/` directory exists (running outside a plugin repo), check the runtime skill listing instead. Plugin-scoped skills (e.g. `orq:` prefix) are registered externally and never appear on disk, so always check the listing for platform capabilities regardless of context.

**Match found:** show the user and ask whether to update the existing skill instead of creating a duplicate.

**No match:** proceed to create.

**Done when:** you have the existing skill content (if updating) or confirmed no duplicate exists (if creating).

### Phase 4: Test and verify

**Fast mode:**
- Confirm the surface exists (hit one endpoint, run one command, call one tool)
- Spot-check 2-3 operations likely to have gotchas (create, error case, pagination boundary)
- Record anything that behaved differently from the docs

**Thorough mode:**

Test every operation with real calls against the live surface. Do not trust docs alone.

**For each operation:**
1. **Run it** with a concrete example (real IDs, real data, not placeholders). For read operations, call them. For write operations, create a test entity, verify it exists, then clean it up.
2. **Record the exact call and the actual response** in the scratchpad. Copy the real output, not a paraphrase.
3. **Compare to docs**: note every difference between what the docs say and what actually happened (different field names, missing fields, extra fields, different status codes, different error shapes).
4. **Test the error path**: call with missing required params, invalid values, or a nonexistent ID. Record the actual error response shape.

**For common workflows**, run the full sequence end-to-end: e.g. create -> list (verify it appears) -> get by ID -> update -> get (verify change) -> delete -> get (verify 404). This catches ordering dependencies, eventual consistency, and silent failures.

**For CLI surfaces**, run each command and subcommand. Capture `--help` output AND run a real invocation. Flags that accept values: test with a real value and confirm the output format.

**For MCP surfaces**, call each tool via the MCP client with real parameters. Verify the return shape matches the schema from `ToolSearch`.

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

**Naming**: use kebab-case. Prefix with the platform domain when the skill wraps a specific product (e.g. `orq-analyze-agent`, `orq-improve-agent`). Cross-cutting skills use a descriptive name (e.g. `wiki-vault`, `create-skill`).

**Grouping**: organize capability sections by user workflow (what the user is trying to do), not by HTTP method or CLI subcommand hierarchy. Each group should correspond to a task the agent would perform.

**Companion skills**: search `skills/*/SKILL.md` for related skills and populate the Companion skills section with verified names and one-line boundaries. Do not guess skill names.

Before presenting: run the failure-mode checklist from the writing guide against the draft. Fix what it catches. If any operations were `[unverified]`, carry the marking into the output skill so the `verified` date does not overstate what was tested.

Output the complete skill as text so the user can read it, then ask for approval via `AskUserQuestion` (approve / request changes). Then write to `skills/<name>/SKILL.md`.

**If updating:** use `Edit` on the existing file rather than rewriting, unless the changes are so extensive that a rewrite is cleaner. Show the diff first.

**Done when:** the user approved the content, the file is written, the writing-guide checklist passes, and every claim traces to a test result (thorough) or doc reference (fast) in the scratchpad.

### Phase 6: Critical review

Before presenting the skill to the user, review the draft adversarially. For each claim in the skill, ask:

1. **Did I actually test this, or am I restating the docs?** If a section says "returns X" but you never saw X in a real response, either test it now or mark it `[unverified]`.
2. **Would an agent following this skill hit a wall?** Walk through each workflow section as if you were the agent executing it for the first time. Are there missing setup steps, unstated prerequisites, or parameter values that only make sense if you already know the surface?
3. **Are the gotchas complete?** Review your test results for any surprise that did not make it into the skill. Silent failures, unexpected defaults, and undocumented required fields are the most common omissions.
4. **Are the examples real?** Every code example, command snippet, or API call in the skill must be something you actually ran (thorough) or extracted from current docs (fast). No invented examples.

Fix what the review catches. If fixing requires additional tests, run them now.

**Done when:** every claim traces to evidence, no untested doc claims remain unmarked, and the workflows are executable by a cold-start agent.

### Phase 7: Clean up

Delete the scratchpad inventory file. The evidence now lives in the skill itself (gotchas with tested calls) and in the `verified` date.

**Done when:** no intermediate files from this run remain in the scratchpad directory.

## Done When

All of these are true:

- The SKILL.md is written (or updated) and the user approved it
- The writing-guide failure-mode checklist passes
- The critical review (Phase 6) passed with no untested claims remaining
- Every claim traces to a test result (thorough) or doc reference (fast)
- Unverified operations are marked `[unverified: reason]` in the output
- Companion skills section references verified skill names
- The scratchpad is clean
