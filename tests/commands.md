# Command Tests

Tests the orq-skills slash commands. These verify our command `.md` files produce correct output using MCP tools. Requires `setup.md` to have run first.

**Prerequisites:** `orq-skills-test-echo` agent from setup, MCP tools working (verified by `mcp-tools.md`).

---

## `/orq:workspace`

- Run with no args → verify multi-section dashboard output (not raw JSON)
- Run with `agents` filter → verify only agents section shown

## `/orq:models`

- Run with no args → verify models grouped by provider
- Run with `embedding` filter → verify only embedding models shown

## `/orq:traces`

- Run with no args → verify trace listing
- Run with `--last 7d --limit 5` → verify filters applied

## `/orq:analytics`

- Run with no args → verify overview with requests, cost, tokens, error rate
- Run with `--group-by model` → verify drill-down by model
- Run with `--last 7d` → verify broader time range

## `/orq:quickstart`

- Verify it detects MCP is available and skips MCP setup step
- Verify it shows workspace snapshot on successful connection

---

## Critical Files

- `commands/workspace.md`
- `commands/models.md`
- `commands/traces.md`
- `commands/analytics.md`
- `commands/quickstart.md`
