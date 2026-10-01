# Changelog

All notable changes to the `orq-trace` plugin are documented here. Follows [Semantic Versioning](https://semver.org/).

## [0.5.0] - 2026-09-30

### Added
- `ORQ_TRACE_DISABLED` switches tracing off for one session, on `1`, `true`, `yes` or `on`, matched without regard to case. Once the plugin is installed in a user's own config, a launcher has no way to decline a session, so `orq launch claude --no-otel` could promise something it could not deliver. The gate sits in `runSafely`, so every hook honours it.

### Fixed
- The `sk-` branch covers key bodies that hold `_`, and its lookahead is bounded. Provider key bodies are base64url, which includes `_`, and both character classes allowed only `[a-z0-9-]`, so an underscore early in the body ended the run before the 16-character minimum and the key reached the span in plaintext: measured over 20000 generated keys of each shape, 8.8% of `sk-ant-api03-`, 16.9% of `sk-proj-` and 17.9% of `sk-orq-` keys. Bounding the lookahead to 64 characters fixes a second problem in the same expression: unbounded, it rescanned and backtracked over the run at every `sk-` start, which took 16.8s on 240k characters of `sk-` against a 30s hook timeout, and nothing shortens a string before the pattern sees it. It now takes 0.02s. `tests/test-redact.mjs` pins both, including the time bound.
- A queued batch is only delivered to the workspace and endpoint it was meant for. The replay queue is one directory shared by every traced session, and a drain used to post whatever it found under the current session's key, so a batch queued while ingest was down could carry one workspace's session content into another, or a staging batch into production. Each file now travels with its destination (the endpoint, plus a fingerprint of the key rather than the key itself), and a drain skips what is not its own. Files written by earlier versions carry no destination and are left where they are: in practice the hourly prune on the next session start removes them first, and the 100-file cap is the backstop. A file this session cannot deliver is reported on stderr, at most once an hour per destination across hooks, because a rotated API key changes the fingerprint and would otherwise lose that session's own queued spans without a word. The warning also identifies legacy files with no recorded destination.
- Hook commands in the manifest quote `${CLAUDE_PLUGIN_ROOT}`. On Windows, a plugin root holding a space (a username with one, or `%TEMP%` under such a user) made `node` receive a truncated path and every hook failed, which showed up only as a session with metrics and no trace.

## [0.4.1] - 2026-09-30

### Fixed
- Free-text provider keys reach the trace redacted. Tool input and output arrive as plain strings, where only the value pattern runs, and it required an unbroken alphanumeric run after `sk-`, so every segmented key passed through: `sk-ant-api03-...`, `sk-proj-...` and orq's own `sk-orq-...`. A key issued from the orq dashboard is a bare JWT and matched nothing at all, and neither did `Authorization: Bearer <token>` in a shell command. The pattern now covers dashed `sk-` keys, JWTs, bearer tokens that carry a digit, fine-grained GitHub PATs, Google `AIza` keys and HuggingFace `hf_` tokens. `tests/test-redact.mjs` pins all of it, including the prose it must leave alone.
- The `sk-` branch is anchored on a word boundary and requires a digit in the run, so it no longer matches the `sk-` inside ordinary kebab-case text such as `task-runner-configuration`, `risk-assessment-framework` or `disk-usage-monitoring-service`. A match costs the whole string rather than the matched span, so one of those would have thrown away an entire tool output.

### Known limitations
- A hit still replaces the whole string, so a single key in a long build log redacts the log. Shapes without a distinctive prefix are still missed, and a bearer token made only of letters is not matched. A key whose first 64 body characters hold no digit at all is also kept, which the digit guard is the price of: without it, every long kebab-case identifier would redact its whole tool output.

## [0.4.0] - 2026-07-28

### Removed
Anything querying these by name needs updating.

- `claude_code.turn.N` spans. Chat, tool, subagent, compaction and error spans parent directly to the root session span.
- `gen_ai.input`, `gen_ai.output`, `orq.input.value`, `orq.output.value`, `input`, `output` on chat, root and subagent spans, replaced by `gen_ai.input.messages` / `gen_ai.output.messages`. `execute_tool` spans keep `gen_ai.input` / `gen_ai.output`.

### Fixed
- Tool results render in orq (BOPS-1008). LLM spans emit semconv messages, with calls as `tool_call` parts and results as `tool_call_response` parts sharing a `tool_use_id`. The old flat messages had no id to pair on, so every tool call showed empty output.
- A tool span shows what the tool produced. The replay preferred the session state copy, capped at 10 KB, over the transcript's whole one, cutting 3.2% of all tool results across 280 local transcripts. Because the cap sliced the serialized payload, and the span detail panel parses that attribute to render it, anything over the cap also arrived as unterminated JSON and displayed as escaped text instead of an object. The replay now reads the transcript whenever the state copy was cut, so the span carries the whole payload and parses. A 1 MB ceiling remains as a safety rail against the 647 KB largest on record.
- Session counters land where ingest keeps them. The `trace` row drops the whole `claude_code.*` namespace, so `total_turns`, `total_tool_calls`, `end_reason`, `cwd`, `model` and the git fields were written and discarded. They are keyed under `metadata.*` now.
- Sessions appear in the Threads tab. Every span carries `orq.thread_id`, the attribute ingest copies into the column thread grouping keys on. Nothing set it before.
- A tool call is replayed before its result. Ordering by transcript position gets this right; ordering by timestamp inverted them, because merging a streamed message moves its timestamp to the final chunk while the tool's came from the first.
- No transcript entry is skipped between windows. The cursor was set one line past the last real entry, so the first entry of every window was consumed unparsed. When that was the closing assistant message, the response went missing.
- A resumed session does not re-emit its transcript. `/model` and `/clear` delete the state and fire SessionStart again, which reset the cursor to 0 and replayed everything as one window. It now seeds past what the transcript already holds.
- An unfinished tool call is emitted at session end. Its call line belongs to an earlier window, so it was never parsed and never sent.
- The replayed conversation keeps every user turn. It was seeded from one field of the session state, so only the current prompt appeared and every earlier one was missing.
- The Thread tab shows the whole session. It renders one span, the last chat completion, which used to carry only its own parse window.
- A compacted session does not replay context the model threw away. One observed boundary dropped 988,570 tokens. The conversation resets at each `compact_boundary`, which was not handled at all before.
- Reasoning is replayed into later turns instead of staying on the span that produced it, and withheld reasoning is marked `[encrypted reasoning]` rather than rendering blank. Claude Code leaves `thinking` empty in 4013 of 4037 blocks, and the empty string was being emitted as-is.
- Span timing reflects real latency. Each window restarted its clock at the turn start, so every window's first span claimed to begin there and durations stretched to fill the gap.
- A send that never gets an answer no longer costs a turn. `fetch` had no signal, so undici waited 300 s while the hook was killed at 30 s, with the cursor already advanced inside the lock the spans were built in. It gives up after 8 s, and the cursor moves only once delivery is accounted for.
- A payload too large for one request no longer wedges the queue forever. It is split across requests and sent, since spans join on `trace_id` and still render as one trace. A 4xx is discarded rather than retried for good, named in the log.
- A profile can name its OTLP endpoint with `otlp_endpoint`. Off production the `my.` to `api.` derivation lands on a host answering Cloudflare 526, and every span queued forever.
- A Skill call claims only the entry right after its tool result. The pending flag stayed armed indefinitely and matched any later string user entry, so unrelated text could be glued onto the Skill span.

### Changed
- The root span carries the conversation and the closing reply. It carried no content at all before, only session metadata. It is rebuilt from the transcript at SessionEnd rather than accumulated in the state file, which is rewritten on every hook fire. Over 133 real sessions it is 483 KB at p90, so the 8 MB ceiling stays out of reach.
- Chat spans carry the conversation up to that point, capped per span, with oversized parts shortened before whole messages are dropped and a call and its response always kept together.
- One content attribute per direction, matching the Go agent runtime. Every other projection is derived at read time, and the dropped copies sat outside the backend masking pass.
- Span ids are derived from the transcript entry, `sha1(trace_id + message or tool id + line index)`, collision-free across 11,657 spans from 160 sessions, rather than drawn at random. A replayed window can no longer mint a second span for the same entry. It does not make a span updatable: storage is append-only and a re-send is either dropped by JetStream or drawn twice in the waterfall. Nothing re-sends.
- Replay is separated from emission: `src/replay.js` orders the steps, `src/messages.js` builds the messages, `handlers.js` keeps the hooks and one visible emit decision instead of a 217-line walk. Every span goes through one factory.
- Every send is chunked under the request limit, in one place, and a queued file is re-chunked through the same path.
- Debug logging writes to the platform temp directory instead of a hardcoded `/tmp`, which does not exist on Windows.

### Known limitations
- `gen_ai.system_instructions` is never emitted: across 158 local transcripts every message role is `user` or `assistant`.
- Reasoning is usually unreadable, since the transcript holds it encrypted. Token counts are unaffected.

## [0.3.2] - 2026-06-22

### Fixed
- Merge assistant content blocks sharing a `message.id` so token usage is counted once instead of per block (was inflating usage).
- Drop the `last_assistant_message` fallback that stamped the turn's final text onto tool/reasoning spans; tool spans now show their actual `tool_use` call.

### Changed
- Split assistant history into separate prose and per-tool-call messages instead of one mixed text+JSON string.
- `Stop`/`SessionEnd` hooks are synchronous (`async: false`) so final spans flush before process exit (async hooks were killed mid-send).

## [0.3.1] - 2026-06-17

### Fixed
- Config path: the hook read `~/.config/orq/config.json`, which does not exist. The orqi CLI stores profiles at `~/.orq/config.json`. The wrong path made `loadOrqConfig()` hit ENOENT and return `{}`, silently disabling the profile-resolution fallback chain (`ORQ_TRACE_PROFILE` / `ORQ_PROFILE` / CLI current profile). Only a raw `ORQ_API_KEY` env var worked. Repointed to `~/.orq/config.json`.

  Note: profile resolution now works where it never did before, so after upgrading, traces may start flowing to the workspace named by your active profile / `ORQ_TRACE_PROFILE`. Verify the destination is intended.

### Changed
- `ORQ_CONFIG_PATH` is now exported from `src/config.js` and overridable via the `ORQ_CONFIG_PATH` env var. Tests import the constant instead of re-hardcoding the path (single source of truth).
- Test profiles default to the CLI current profile + any other profile, so the suite runs against a real config without manual overrides.
