# orq-trace

Automatically trace Claude Code sessions to Orq. Captures sessions, turns, tool calls, LLM responses, and subagents as hierarchical OTLP spans.

## Installation

```bash
claude plugin marketplace add orq-ai/assistant-plugins
claude plugin install orq-trace@orq-claude-plugin
```

## Span Hierarchy

```
orq.claude_code.session              (root, carries the whole conversation)
├── chat claude-opus-4-6             (LLM response)
├── execute_tool Bash                (tool call)
├── chat claude-opus-4-6             (LLM response)
├── execute_tool Read                (tool call)
├── subagent.Explore                 (subagent)
│   ├── execute_tool Grep            (subagent tool)
│   └── chat claude-opus-4-6         (subagent LLM)
├── claude.context.compact           (compaction event)
└── claude_code.error.rate_limit     (API error event)
```

## Configuration

### Required

An API key must be resolvable for tracing to activate. The hook reads it from the first of these that is non-empty:

| Source | Description |
|---|---|
| Plugin option `ORQ_API_KEY` | Prompted for when you enable the plugin (stored as a sensitive value); change it with `/plugin configure`. Claude Code passes it to the hook as `CLAUDE_PLUGIN_OPTION_ORQ_API_KEY`. |
| `ORQ_API_KEY` env var | Used when the plugin option is empty. |

The hook does not read `~/.orq/config.json` or any orq CLI profile.

**Migrating from profile-based setup (0.5.x and earlier).** `ORQ_TRACE_PROFILE`, `ORQ_PROFILE` and the orq CLI's current profile are no longer consulted. If you relied on them, enter your key as the plugin option (`/plugin configure`) or export `ORQ_API_KEY` in your shell.

### Trace Destination

| Variable | Description | Default |
|---|---|---|
| `ORQ_BASE_URL` | Override API base URL | `https://my.orq.ai` |
| `ORQ_OTLP_ENDPOINT` | Explicit OTLP ingest URL, for hosts where the derived `api.` host does not serve OTLP (e.g. staging) | Derived from base URL |
| `OTEL_EXPORTER_OTLP_ENDPOINT` | Override the full OTLP endpoint URL (wins over `ORQ_OTLP_ENDPOINT`) | — |
| `OTEL_EXPORTER_OTLP_HEADERS` | Extra headers for OTLP endpoint (comma-separated `key=value`) | — |

### Privacy & Content Control

| Variable | Description | Default |
|---|---|---|
| `TRACE_ORQ_REDACT_CONTENT` | Strip ALL input/output content from traces (`true`/`1`) | `false` |
| `ORQ_TRACE_MAX_CONTENT_LEN` | Truncate content exceeding this character limit | No limit |

### Identity & Metadata

| Variable | Description | Default |
|---|---|---|
| `ORQ_TRACE_USER` | User identity attached to traces (filterable as `metadata.user`) | Falls back to `git config user.email` |

### Debugging

| Variable | Description | Default |
|---|---|---|
| `ORQ_DEBUG` | Enable debug logging to stderr and `/tmp/orq-trace-debug.log` (`true`/`1`) | `false` |
| `ORQ_CLAUDE_STATE_DIR` | Override session state directory | `~/.claude/state` |

### Resolution Priority

```
API Key:
  1. CLAUDE_PLUGIN_OPTION_ORQ_API_KEY       (plugin option)
  2. ORQ_API_KEY env var
  3. none: tracing stays off

Base URL:       ORQ_BASE_URL, else https://my.orq.ai
OTLP endpoint:  OTEL_EXPORTER_OTLP_ENDPOINT, else ORQ_OTLP_ENDPOINT,
                else derived from the base URL (my. -> api.)
```

Empty values are treated as unset.

## What Gets Captured

### Session Span (root)
- Session ID, working directory, permission mode
- Git branch, repo, commit hash
- User identity
- Model name
- Total turns, tool calls, failed tool calls
- End reason

### LLM Spans
- Model name (request and response)
- Conversation thread (accumulated input messages)
- Response with parts (text, reasoning, tool calls)
- Finish reason
- Token usage (input, output, total, cache creation, cache read)

### Tool Spans
- Tool name, arguments, result
- Duration
- Incomplete flag (for tools still running at session end)

### Subagent Spans
- Agent ID, type
- Child tool and LLM spans (reuses the same timeline/threading logic)

### Error Spans (StopFailure)
- Error type (rate_limit, authentication_failed, billing_error, server_error, etc.)
- Error message

### Event Spans
- Context compaction events with turn count

## Hooks Used

| Hook | Purpose |
|---|---|
| `SessionStart` | Initialize session state, capture git context and user |
| `UserPromptSubmit` | Record the prompt and advance the turn count (emits no span) |
| `Stop` | Process transcript, emit tool + LLM spans incrementally |
| `StopFailure` | Emit error span for API failures (rate limits, etc.) |
| `PostToolUse` | Record successful tool calls (name, input, response) in session state |
| `PostToolUseFailure` | Track failed tool calls |
| `PreCompact` | Emit context compaction event span |
| `SubagentStart` | Track subagent lifecycle |
| `SubagentStop` | Emit subagent + child spans |
| `SessionEnd` | Batch-send root + turn + transcript spans, cleanup |

## Redaction

Content is automatically scanned for sensitive data before sending:

- **Key-based**: Fields named `password`, `secret`, `token`, `api_key`, `authorization`, `private_key`, `access_key`
- **Value-based**: Patterns matching `sk-*`, `sk_live_*`, `ghp_*`, `ghu_*`, `AKIA*`, PEM private keys
- **Path-based**: `.env` file paths
- **Stringified JSON**: JSON strings in tool outputs are parsed and key-based redaction is applied to embedded objects

Set `TRACE_ORQ_REDACT_CONTENT=true` to strip all content entirely.

## Architecture

- **State**: File-based session state in `~/.claude/state/orq_sessions/` with atomic writes and file-based locking
- **Queue**: Failed sends are queued in `~/.claude/state/orq_queue/` for retry (100-file cap, 30s drain cooldown)
- **Cleanup**: Stale session files (>24h) and queue files (>1h) are pruned on session start
- **Batching**: SessionEnd sends all spans in a single HTTP request (parents first)
