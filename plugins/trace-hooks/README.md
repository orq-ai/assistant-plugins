# orq-trace

Automatically trace Claude Code sessions to Orq. Captures sessions, turns, tool calls, LLM responses, and subagents as hierarchical OTLP spans.

## Installation

```bash
claude plugin marketplace add orq-ai/claude-plugins
claude plugin install orq-trace@orq-claude-plugin
```

## Span Hierarchy

```
orq.claude_code.session              (root)
├── claude_code.turn.1               (agent turn)
│   ├── chat claude-opus-4-6         (LLM response)
│   ├── execute_tool Bash            (tool call)
│   ├── chat claude-opus-4-6         (LLM response)
│   ├── execute_tool Read            (tool call)
│   ├── chat claude-opus-4-6         (LLM response)
│   └── subagent.Explore             (subagent)
│       ├── execute_tool Grep        (subagent tool)
│       └── chat claude-opus-4-6     (subagent LLM)
├── claude_code.turn.2
│   └── ...
├── claude.context.compact           (compaction event)
└── claude_code.error.rate_limit     (API error event)
```

## Configuration

### Required

An API key must be resolvable for tracing to activate. Set one of the following:

| Variable | Description |
|---|---|
| `ORQ_TRACE_PROFILE` | Profile name in `~/.config/orq/config.json` — **highest priority** for trace destination |
| `ORQ_API_KEY` | API key (used if no trace profile is set) |

### Trace Destination

| Variable | Description | Default |
|---|---|---|
| `ORQ_TRACE_PROFILE` | Orqi profile for trace destination (decouples traces from CLI/MCP profile) | — |
| `ORQ_PROFILE` | General orqi profile (fallback if `ORQ_TRACE_PROFILE` not set) | — |
| `ORQ_BASE_URL` | Override API base URL | `https://my.orq.ai` |
| `OTEL_EXPORTER_OTLP_ENDPOINT` | Override the full OTLP endpoint URL | Derived from base URL |
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
  1. ORQ_TRACE_PROFILE profile's api_key    (trace-specific)
  2. ORQ_API_KEY env var                     (general key)
  3. ORQ_PROFILE profile's api_key
  4. ~/.config/orq/config.json current profile

Base URL:
  1. ORQ_TRACE_PROFILE profile's base_url   (trace-specific)
  2. ORQ_BASE_URL env var                    (general override)
  3. Profile's base_url
  4. Default: https://my.orq.ai
```

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
| `UserPromptSubmit` | Open new turn, close and send previous turn span |
| `Stop` | Process transcript, emit tool + LLM spans incrementally |
| `StopFailure` | Emit error span for API failures (rate limits, etc.) |
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
