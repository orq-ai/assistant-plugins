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

Ask the user with `AskUserQuestion`. Three things:

1. **What surface?** A name or description. If the user already said what it is, confirm.

2. **Where are the docs?** Accept any of:
   - A URL (API docs, OpenAPI spec, reference pages)
   - A file path (local spec, README, config)
   - A CLI command name (you run `--help` yourself)
   - An MCP server name (you use `ToolSearch` to pull schemas)
   - "I'll paste it"

3. **Fast or thorough?**
   - **Fast**: read docs, extract the inventory, spot-check 2-3 operations. Minutes.
   - **Thorough**: test every operation with real calls, including error and edge cases, and record each gotcha from the actual response. Longer.

If the user already provided some of this, ask only for what is missing.

**Done when:** you have a surface name, a source to read, and a scope mode.

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

```
Glob: skills/*/SKILL.md
Grep: <surface name or key terms> in those files
```

If no `skills/` directory exists (running outside a plugin repo), check the runtime skill listing instead. Plugin-scoped skills (e.g. `orq:` prefix) are registered externally and never appear on disk, so always check the listing for platform capabilities regardless of context.

**Match found:** read it, diff the verified inventory against what it documents, propose an update. Show the diff to the user. Scope Phase 4 testing to what is new or changed.

**No match:** proceed to create.

**Done when:** you know whether you are creating or updating, and the user agrees.

### Phase 4: Test and verify

**Fast mode:**
- Confirm the surface exists (hit one endpoint, run one command, call one tool)
- Spot-check 2-3 operations likely to have gotchas (create, error case, pagination boundary)
- Record anything that behaved differently from the docs

**Thorough mode:**
- Test every operation in the inventory (or only the new/changed operations if updating)
- For each, record: the exact call, the actual response shape, and any surprise
- Test error cases: missing required params, bad auth, invalid values
- Test edge cases: empty results, large payloads, special characters
- Test common workflows end-to-end

**Large surfaces (20+ operations):** confirm with the user before proceeding in thorough mode. Show the count and estimated scope.

**When an operation cannot be tested** (auth unavailable, endpoint behind VPN, rate limit hit, destructive write): mark it `[unverified: <reason>]` in the scratchpad. Report the count of unverified operations to the user before proceeding.

Record gotchas in the scratchpad with evidence:

```
Gotcha 1: <command> with <flag> silently returns empty instead of erroring
  Tested: <exact call>
  Expected: 404 or error message
  Actual: 200 with empty array
```

**Done when:** every operation is either tested with a recorded result or marked `[unverified: reason]`, and every gotcha has a recorded test that produced it.

### Phase 5: Write the contract

Read [`resources/writing-guide.md`](resources/writing-guide.md) first. Then structure the output following [`resources/template.md`](resources/template.md). Fill the skeleton with the verified inventory and gotchas from the scratchpad.

**Naming**: use kebab-case. Prefix with the platform domain when the skill wraps a specific product (e.g. `orq-analyze-agent`, `orq-improve-agent`). Cross-cutting skills use a descriptive name (e.g. `wiki-vault`, `create-skill`).

**Grouping**: organize capability sections by user workflow (what the user is trying to do), not by HTTP method or CLI subcommand hierarchy. Each group should correspond to a task the agent would perform.

**Companion skills**: search `skills/*/SKILL.md` for related skills and populate the Companion skills section with verified names and one-line boundaries. Do not guess skill names.

Before presenting: run the failure-mode checklist from the writing guide against the draft. Fix what it catches. If any operations were `[unverified]`, carry the marking into the output skill so the `verified` date does not overstate what was tested.

Output the complete skill as text so the user can read it, then ask for approval via `AskUserQuestion` (approve / request changes). Then write to `skills/<name>/SKILL.md`.

**If updating:** use `Edit` on the existing file rather than rewriting, unless the changes are so extensive that a rewrite is cleaner. Show the diff first.

**Done when:** the user approved the content, the file is written, the writing-guide checklist passes, and every claim traces to a test result (thorough) or doc reference (fast) in the scratchpad.

### Phase 6: Clean up

Delete the scratchpad inventory file. The evidence now lives in the skill itself (gotchas with tested calls) and in the `verified` date.

**Done when:** no intermediate files from this run remain in the scratchpad directory.

## Done When

All of these are true:

- The SKILL.md is written (or updated) and the user approved it
- The writing-guide failure-mode checklist passes
- Every claim traces to a test result (thorough) or doc reference (fast)
- Unverified operations are marked `[unverified: reason]` in the output
- Companion skills section references verified skill names
- The scratchpad is clean
