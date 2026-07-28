import { execFileSync } from "node:child_process";

import { getApiKey } from "./config.js";
import {
  attr,
  boolEnv,
  compact,
  debugLog,
  nowUnixNano,
  randomHex,
  readStdinJson,
  stableSpanId,
  toStringValue,
  truncateBytes,
} from "./common.js";
import {
  CONVERSATION_MAX_BYTES,
  SPAN_CONVERSATION_MAX_BYTES,
  fitConversation,
  lastAssistantText,
  lastToolResult,
  textPartsMessage,
} from "./messages.js";
import { sendSpan, sendSpans, createSpan } from "./otlp.js";
import { sanitizeContent } from "./redact.js";
import { replay } from "./replay.js";
import {
  deleteSessionState,
  loadSessionState,
  pruneStaleFiles,
  saveSessionState,
  withSessionLock,
} from "./state.js";
import { countCompleteLines, parseTranscript } from "./transcript.js";

function getGitInfo(args, cwd) {
  try {
    return execFileSync("git", args, { cwd, encoding: "utf8", timeout: 5000 }).trim();
  } catch (err) {
    // Exit code 128 = not a git repo, ENOENT = git not installed. Both expected.
    if (err?.status === 128 || err?.code === "ENOENT") return null;
    process.stderr.write(`[orq-trace] WARN: git ${args[0]} failed (cwd=${cwd}): ${err?.message}\n`);
    return null;
  }
}

function getGitBranch(cwd) {
  return getGitInfo(["rev-parse", "--abbrev-ref", "HEAD"], cwd);
}

function getGitRepo(cwd) {
  return getGitInfo(["rev-parse", "--show-toplevel"], cwd);
}

function getGitCommit(cwd) {
  return getGitInfo(["rev-parse", "HEAD"], cwd);
}

function getUserIdentity(cwd) {
  if (process.env.ORQ_TRACE_USER) return process.env.ORQ_TRACE_USER;
  return getGitInfo(["config", "user.email"], cwd);
}

function getSessionId(payload) {
  return (
    payload.session_id ||
    payload.sessionId ||
    process.env.CLAUDE_SESSION_ID ||
    process.env.SESSION_ID ||
    null
  );
}

function enabledTracing() {
  return Boolean(getApiKey());
}

const DEFAULT_STATE_MAX_FIELD_CHARS = 10_240;

// Prepares a PostToolUse payload for the session state file. State files are
// rewritten on every hook fire, so a megabyte of file content there is real
// cost. Secrets go first, then the cap, and the original size is kept so the
// replay can tell this copy was cut and go to the transcript instead.
function compactForState(value) {
  if (value === null || value === undefined) {
    return { value: null, size_bytes: 0 };
  }
  const sanitized = sanitizeContent(value);
  if (sanitized === null || sanitized === undefined) {
    return { value: null, size_bytes: 0 };
  }
  let str;
  if (typeof sanitized === "string") {
    str = sanitized;
  } else {
    try {
      str = JSON.stringify(sanitized);
    } catch (e) {
      str = `[unserializable: ${e.message}]`;
    }
  }
  const sizeBytes = Buffer.byteLength(str, "utf8");
  const cap = parseInt(process.env.ORQ_TRACE_STATE_MAX_FIELD_CHARS, 10) || DEFAULT_STATE_MAX_FIELD_CHARS;
  if (sizeBytes <= cap) {
    return { value: sanitized, size_bytes: sizeBytes };
  }
  // A cut value is never read: the replay compares size_bytes against what was
  // kept and takes the transcript's whole copy instead. This only has to be
  // small and to record that it was cut.
  return { value: truncateBytes(str, cap), size_bytes: sizeBytes };
}

// A safety rail, not a budget. The tool span is where a payload is read in full,
// and the largest of 13,646 results across 280 local transcripts is 647 KB. This
// only exists so one runaway command cannot build a span too large to send.
const TOOL_PAYLOAD_MAX_BYTES = 1024 * 1024;

function capToolPayload(value) {
  return truncateBytes(toStringValue(value), TOOL_PAYLOAD_MAX_BYTES);
}

function usageAttrs(usage) {
  const prompt = usage.input_tokens ?? usage.prompt_tokens;
  const completion = usage.output_tokens ?? usage.completion_tokens;
  const total = usage.total_tokens ?? ((prompt || 0) + (completion || 0));
  const cacheCreation = usage.cache_creation_input_tokens ?? null;
  const cacheRead = usage.cache_read_input_tokens ?? null;

  return compact([
    attr("gen_ai.usage.input_tokens", prompt),
    attr("gen_ai.usage.output_tokens", completion),
    // Deprecated aliases kept for orq backend compat (aggregation uses these)
    attr("gen_ai.usage.prompt_tokens", prompt),
    attr("gen_ai.usage.completion_tokens", completion),
    attr("gen_ai.usage.total_tokens", total),
    // Anthropic prompt caching fields — only present when caching is active
    attr("gen_ai.usage.cache_creation_input_tokens", cacheCreation),
    attr("gen_ai.usage.cache_read_input_tokens", cacheRead),
  ]);
}

// What a span needs to know about the session it belongs to. Built from the
// session state at the call sites, so a replay can supply the same four fields
// without pretending to be a state object.
function spanContextOf(state) {
  return {
    traceId: state.trace_id,
    rootSpanId: state.root_span_id,
    sessionId: state.session_id,
    model: state.model,
  };
}

// Every span in a session shares a trace, a parent, and a thread id. Building
// them through one factory makes that structural instead of something the next
// caller has to remember: orq.thread_id is what the Threads tab groups on, and
// test-trace.sh asserts every span carries it.
function sessionSpan(context, {
  spanId = randomHex(8),
  // Pass null for the trace root. Not undefined: that would take the default
  // below and make the root its own parent, which the traces list reads as a
  // child and hides, so the whole session never appears.
  parentSpanId = context.rootSpanId,
  name,
  kind,
  startTimeUnixNano,
  endTimeUnixNano,
  attributes,
}) {
  return createSpan({
    traceId: context.traceId,
    spanId,
    parentSpanId: parentSpanId ?? undefined,
    name,
    kind,
    startTimeUnixNano,
    endTimeUnixNano,
    attributes: compact([...attributes, attr("orq.thread_id", context.sessionId)]),
  });
}

function toolSpan(context, step) {
  const { tool, recorded, input, output } = step;
  const args = capToolPayload(input);
  const result = capToolPayload(output);
  // Agent tool calls are emitted as tool spans here too. If SubagentStart/Stop
  // hooks fire they add richer sibling subagent.* spans; some overlap beats
  // losing Agent calls entirely when those hooks don't fire (e.g. -p mode).
  return sessionSpan(context, {
    spanId: stableSpanId(context.traceId, `tool:${tool.id}:${step.lineIndex}`),
    name: `execute_tool ${tool.name}`,
    kind: 1,
    startTimeUnixNano: step.startTimeUnixNano,
    endTimeUnixNano: step.endTimeUnixNano,
    attributes: [
      attr("orq.span.kind", "tool"),
      attr("gen_ai.tool.name", tool.name),
      attr("gen_ai.tool.call.arguments", args),
      attr("gen_ai.tool.call.result", result),
      // Set gen_ai.input/output so the backend uses these directly instead of
      // constructing a messages-wrapped version. Nothing else: orq.input.value
      // / orq.output.value duplicate the payload into attributes the masking
      // pass does not cover.
      attr("gen_ai.input", args),
      attr("gen_ai.output", result),
      tool.incomplete ? attr("claude_code.tool.incomplete", true) : null,
      attr("claude_code.tool.enriched", recorded ? "recorded" : tool.name === "Skill" ? "skipped_skill" : "transcript_only"),
      tool.name === "Skill" ? attr("claude_code.skill.name", tool.input?.skill ?? "unknown") : null,
      tool.name === "Skill" ? attr("claude_code.skill.args", tool.input?.args || "") : null,
    ],
  });
}

function chatSpan(context, step, fallbackStopReason) {
  const { message } = step;
  const modelName = message.model || context.model || "claude";
  // Bounded here rather than in the replay: serializing the conversation once
  // per historical message would be quadratic per hook and cubic per session,
  // and only emitted steps need the snapshot at all.
  const { messages: inputMessages, truncated } = fitConversation(
    step.conversation,
    SPAN_CONVERSATION_MAX_BYTES,
  );

  return sessionSpan(context, {
    spanId: stableSpanId(context.traceId, `msg:${message.messageId}:${step.lineIndex}`),
    name: `chat ${modelName}`,
    kind: 3,
    startTimeUnixNano: step.startTimeUnixNano,
    endTimeUnixNano: step.endTimeUnixNano,
    attributes: [
      attr("orq.span.kind", "llm"),
      attr("gen_ai.operation.name", `chat ${modelName}`),
      attr("gen_ai.system", "anthropic"),
      attr("gen_ai.provider.name", "anthropic"),
      attr("gen_ai.request.model", modelName),
      attr("gen_ai.response.model", modelName),
      attr("gen_ai.response.finish_reasons", [message.stopReason || fallbackStopReason || "stop"]),
      // One attribute per direction, semconv shape. The backend stores these
      // verbatim and derives every other projection (span.input, span.output,
      // the thread view) from them; the old gen_ai.input / orq.*.value / input
      // / output copies flattened the parts away, so tool results arrived with
      // no id to pair them to their call.
      attr("gen_ai.input.messages", JSON.stringify(inputMessages)),
      attr("gen_ai.output.messages", JSON.stringify(step.outputMessages)),
      truncated ? attr("claude_code.conversation_truncated", true) : null,
      ...usageAttrs(message.usage || {}),
    ],
  });
}

function transcriptPathOf(payload) {
  return payload.transcript_path || payload.transcriptPath;
}

export async function handleSessionStart() {
  const payload = await readStdinJson();
  const sessionId = getSessionId(payload);
  if (!sessionId) {
    return;
  }

  // Prune stale session/queue files on startup (best-effort, non-blocking)
  pruneStaleFiles().catch(() => {});

  await withSessionLock(sessionId, async () => {
    const existing = await loadSessionState(sessionId);
    if (existing) return;

    const cwd = payload.cwd || process.cwd();
    const state = {
      session_id: sessionId,
      trace_id: randomHex(16),
      root_span_id: randomHex(8),
      session_started_at_ns: nowUnixNano(),
      turn_count: 0,
      total_tool_calls: 0,
      model: payload.model || payload.model_name || null,
      source: payload.source || null,
      cwd,
      git_branch: getGitBranch(cwd),
      git_repo: getGitRepo(cwd),
      git_commit: getGitCommit(cwd),
      user: getUserIdentity(cwd),
      // Start after whatever the transcript already holds. A resumed session
      // (or one whose state was deleted by SessionEnd and recreated here, which
      // is what /model and /clear do) keeps its old transcript, so starting at
      // 0 would re-emit every past entry as one enormous window on the next
      // Stop hook, duplicating spans that were already sent.
      last_processed_line: await countCompleteLines(transcriptPathOf(payload)),
      subagents: {},
    };

    await saveSessionState(sessionId, state);
  });
}

export async function handleUserPromptSubmit() {
  const payload = await readStdinJson();
  const sessionId = getSessionId(payload);
  if (!sessionId) {
    return;
  }

  // No span is emitted per turn. orq's own agent runtime has no per-turn span
  // (a multi-step agent run nests under a single agent.response), and the turn
  // span was also colliding with the root span's content attributes at
  // SessionEnd, dropping itself and orphaning its children. Turn boundaries
  // survive as turn_count on the root and as orq.thread_id grouping.
  await withSessionLock(sessionId, async () => {
    const state = await loadSessionState(sessionId);
    if (!state) return;

    state.turn_count += 1;

    await saveSessionState(sessionId, state);
  });
}

export async function handlePostToolUseFailure() {
  const payload = await readStdinJson();
  const sessionId = getSessionId(payload);
  if (!sessionId) {
    return;
  }

  await withSessionLock(sessionId, async () => {
    const state = await loadSessionState(sessionId);
    if (!state || !state.root_span_id) return;

    state.failed_tool_calls ||= [];
    state.failed_tool_calls.push({
      tool_name: payload.tool_name || payload.toolName || "unknown",
      error: payload.error || payload.stderr || "",
      timestamp: nowUnixNano(),
    });

    await saveSessionState(sessionId, state);
  });
}

export async function handlePostToolUse() {
  const payload = await readStdinJson();
  const sessionId = getSessionId(payload);
  if (!sessionId) {
    return;
  }

  await withSessionLock(sessionId, async () => {
    const state = await loadSessionState(sessionId);
    if (!state || !state.root_span_id) return;

    const inputRec = compactForState(payload.tool_input ?? payload.toolInput);
    const responseRec = compactForState(payload.tool_response ?? payload.toolResponse);

    state.successful_tool_calls ||= [];
    state.successful_tool_calls.push({
      tool_use_id: payload.tool_use_id || payload.toolUseId || null,
      tool_name: payload.tool_name || payload.toolName || "unknown",
      tool_input: inputRec.value,
      tool_input_size_bytes: inputRec.size_bytes,
      tool_response: responseRec.value,
      tool_response_size_bytes: responseRec.size_bytes,
      timestamp: nowUnixNano(),
    });

    await saveSessionState(sessionId, state);
  });
}

export async function handlePreCompact() {
  const payload = await readStdinJson();
  const sessionId = getSessionId(payload);
  if (!sessionId) {
    return;
  }

  let span;
  await withSessionLock(sessionId, async () => {
    const state = await loadSessionState(sessionId);
    if (!state) return;

    span = sessionSpan(spanContextOf(state), {
      name: "claude.context.compact",
      kind: 1,
      attributes: [
        attr("orq.span.kind", "event"),
        attr("claude_code.event", "context_compaction"),
        attr("claude_code.turn_count_at_compaction", state.turn_count),
      ],
    });
  });

  if (span) {
    await sendSpan(span);
  }
}

// Replays the whole transcript into the conversation the model saw, emitting
// spans only for the entries past the cursor.
//
// Always replaying from line 0 costs one extra parse per hook (118 ms on the
// largest transcript measured here, against a 30 s hook timeout) and buys two
// things a windowed replay cannot:
//
//   - correct span timing. Walking the earlier entries carries previousEndNs
//     through their real timestamps, so the first new span starts where the
//     last one actually ended instead of at session start.
//   - a complete conversation on every chat span, which is what the model
//     genuinely saw, and what lets the thread view (which renders exactly one
//     span, the last) show the whole session.
//
// The conversation is rebuilt rather than carried in the session state file,
// because that file is rewritten on every hook fire, once per tool call
// included, so persisting it would mean gigabytes of lock-held writes.
// Pure: it reads the transcript and returns what it found. Callers own their
// own state, which is what lets a subagent reuse it by passing four values
// instead of fabricating a whole session-state object to be read back out.
async function buildTranscriptSpans({
  traceId,
  rootSpanId,
  sessionId,
  startNs,
  model,
  fromLine = 0,
  recorded = [],
  transcriptPath,
  stopReason,
  final = false,
}) {
  const parsed = await parseTranscript(transcriptPath, { emitPending: final });

  // Prefer the PostToolUse record when a transcript tool call matches by id:
  // it is the exact tool_response captured at execution time, not the parsed
  // approximation. Skill is excluded because its transcript output gets the
  // loaded skill body appended after the result, which PostToolUse fires too
  // early to see.
  const recordedByToolUseId = new Map();
  for (const rec of recorded) {
    if (rec?.tool_use_id) recordedByToolUseId.set(rec.tool_use_id, rec);
  }

  const { steps, conversation } = replay(parsed, { startNs, recordedByToolUseId });

  const context = { traceId, rootSpanId, sessionId, model };

  // The whole session is replayed for context; only what lies past the cursor
  // has not been sent yet.
  const spans = [];
  let toolCallCount = 0;
  for (const step of steps) {
    if (!step.emittable) continue;
    if (!step.alwaysNew && step.lineIndex < fromLine) continue;
    if (step.kind === "tool") {
      toolCallCount += 1;
      spans.push(toolSpan(context, step));
    } else {
      spans.push(chatSpan(context, step, stopReason));
    }
  }

  // Nothing re-sends a span to improve it, because that does not work. Storage
  // is append-only and no read path collapses duplicates. JetStream drops a
  // repeat of the same trace and span id within 120 s, so a re-send is either
  // thrown away or stored twice, and a second row draws the span and its whole
  // subtree twice in the waterfall.

  // Monotonic: a transcript that could not be read reports 0 entries, and
  // accepting that would rewind the cursor and re-emit the whole session.
  return { spans, conversation, nextLine: Math.max(fromLine, parsed.nextLine), toolCallCount };
}

// Runs the replay for a live session and folds the result back into its state.
async function emitTranscriptSpans(state, payload, { final = false } = {}) {
  const result = await buildTranscriptSpans({
    traceId: state.trace_id,
    rootSpanId: state.root_span_id,
    sessionId: state.session_id,
    startNs: state.session_started_at_ns || null,
    model: state.model,
    fromLine: state.last_processed_line || 0,
    recorded: state.successful_tool_calls || [],
    transcriptPath: transcriptPathOf(payload),
    stopReason: payload.stop_reason,
    final,
  });

  state.total_tool_calls = (state.total_tool_calls || 0) + result.toolCallCount;
  state.last_processed_line = result.nextLine;

  return { spans: result.spans, conversation: result.conversation };
}

export async function handleStop() {
  const payload = await readStdinJson();
  const sessionId = getSessionId(payload);
  if (!sessionId) {
    return;
  }

  // Build under the lock, send outside it, and only then mark the window
  // consumed. Advancing the cursor first meant a hook killed mid-send (Claude
  // Code allows 30 s) left the window recorded as delivered while nothing had
  // been sent or queued. Re-emitting the window instead is the lesser evil: the
  // spans carry the same ids, and the repeat lands inside JetStream's 120 s
  // window, which drops it. A lost window cannot be recovered at all.
  let result = null;
  await withSessionLock(sessionId, async () => {
    const state = await loadSessionState(sessionId);
    if (!state || !state.root_span_id) return;

    result = await buildTranscriptSpans({
      traceId: state.trace_id,
      rootSpanId: state.root_span_id,
      sessionId: state.session_id,
      startNs: state.session_started_at_ns || null,
      model: state.model,
      fromLine: state.last_processed_line || 0,
      recorded: state.successful_tool_calls || [],
      transcriptPath: transcriptPathOf(payload),
      stopReason: payload.stop_reason,
    });
  });

  if (!result) return;

  if (result.spans.length > 0) {
    await sendSpans(result.spans);
  }

  await withSessionLock(sessionId, async () => {
    const state = await loadSessionState(sessionId);
    if (!state) return;

    state.total_tool_calls = (state.total_tool_calls || 0) + result.toolCallCount;
    state.last_processed_line = Math.max(state.last_processed_line || 0, result.nextLine);
    await saveSessionState(sessionId, state);
  });
}

export async function handleStopFailure() {
  const payload = await readStdinJson();
  const sessionId = getSessionId(payload);
  if (!sessionId) {
    return;
  }

  const reason = payload.reason || payload.stop_reason || "unknown";

  let span;
  await withSessionLock(sessionId, async () => {
    const state = await loadSessionState(sessionId);
    if (!state) return;

    span = sessionSpan(spanContextOf(state), {
      name: `claude_code.error.${reason}`,
      kind: 1,
      attributes: [
        attr("orq.span.kind", "event"),
        attr("error.type", reason),
        attr("otel.status_code", "ERROR"),
        attr("claude_code.event", "stop_failure"),
        attr("claude_code.error.message", payload.error || payload.message || ""),
      ],
    });
  });

  if (span) {
    await sendSpan(span);
  }
}

export async function handleSessionEnd() {
  const payload = await readStdinJson();
  const sessionId = getSessionId(payload);
  if (!sessionId) {
    return;
  }

  let rootSpan = null;
  let transcriptSpans = [];

  await withSessionLock(sessionId, async () => {
    const state = await loadSessionState(sessionId);
    if (!state) return;

    // In -p / non-interactive mode UserPromptSubmit may never fire, so the
    // turn count comes from here instead.
    if (state.turn_count === 0) state.turn_count = 1;

    // One pass does both jobs: it emits the remaining spans and returns the
    // whole session's conversation, since the replay always starts at line 0.
    const { spans, conversation } = await emitTranscriptSpans(state, payload, { final: true });
    transcriptSpans = spans;

    const { messages: rootInputMessages, truncated } = fitConversation(conversation, CONVERSATION_MAX_BYTES);
    // Read off the conversation rather than tracked in state: the replay covers
    // the whole session, so it already holds the closing message. The tool
    // result covers a session that ended mid-tool-call.
    const rootOutputText = lastAssistantText(conversation) || lastToolResult(conversation);
    const rootOutputMessage = textPartsMessage("assistant", rootOutputText);
    const rootOutputMessages = rootOutputMessage
      ? [{ ...rootOutputMessage, finish_reason: payload.reason || "stop" }]
      : [];

    rootSpan = sessionSpan(spanContextOf(state), {
      spanId: state.root_span_id,
      // The root of the trace: no parent, rather than parenting to itself.
      parentSpanId: null,
      name: "orq.claude_code.session",
      kind: 1,
      startTimeUnixNano: state.session_started_at_ns,
      endTimeUnixNano: nowUnixNano(),
      attributes: [
        attr("orq.span.kind", "workflow"),
        attr("gen_ai.operation.name", "orq.claude_code.session"),
        attr("gen_ai.system", "anthropic"),
        attr("gen_ai.provider.name", "anthropic"),
        attr("orq.trace.framework.name", "claude-code"),
        // Everything the session reports about itself goes under metadata.*,
        // because ingest drops the claude_code.* namespace on a trace row.
        // metadata.user and metadata.git.* survived only because they were
        // already keyed this way; the counters were not, so they landed
        // nowhere and the turn count became unreadable when the per-turn
        // spans were removed. Child spans keep claude_code.* fine.
        attr("metadata.session_id", state.session_id),
        attr("metadata.permission_mode", payload.permission_mode || process.env.CLAUDE_PERMISSION_MODE || ""),
        attr("metadata.cwd", state.cwd || payload.cwd || ""),
        attr("metadata.model", state.model || payload.model || ""),
        attr("metadata.git.commit", state.git_commit || null),
        attr("metadata.git.branch", state.git_branch || null),
        attr("metadata.git.repo", state.git_repo || null),
        attr("metadata.user", state.user || null),
        attr("metadata.total_turns", state.turn_count || 0),
        attr("metadata.total_tool_calls", state.total_tool_calls || 0),
        attr("metadata.successful_tool_calls", (state.successful_tool_calls || []).length),
        attr("metadata.failed_tool_calls", (state.failed_tool_calls || []).length),
        attr("metadata.end_reason", payload.reason || ""),
        truncated ? attr("metadata.conversation_truncated", true) : null,
        rootInputMessages.length ? attr("gen_ai.input.messages", JSON.stringify(rootInputMessages)) : null,
        rootOutputMessages.length ? attr("gen_ai.output.messages", JSON.stringify(rootOutputMessages)) : null,
      ],
    });

    if (boolEnv("ORQ_DEBUG")) {
      const msg = `[orq-trace] SessionEnd: ${1 + transcriptSpans.length} spans (1 root + ${transcriptSpans.length} transcript), conversation=${rootInputMessages.length} msgs${truncated ? " (truncated)" : ""}\n`;
      process.stderr.write(msg);
      await debugLog(msg);
    }

    // Delete state inside the lock so a racing hook can't write new state
    // between lock release and deletion.
    await deleteSessionState(sessionId);
  });

  if (!rootSpan) return;

  // Batch all spans in one HTTP request, root first so the backend processes
  // it before child $inc upserts arrive. A single request also avoids repeated
  // drainQueue overhead and the queue-eviction problem where the root span
  // (sent first) would be the oldest queued entry.
  await sendSpans([rootSpan, ...transcriptSpans].filter(Boolean));
}

export async function handleSubagentStart() {
  const payload = await readStdinJson();
  const sessionId = getSessionId(payload);
  if (!sessionId) {
    return;
  }

  await withSessionLock(sessionId, async () => {
    const state = await loadSessionState(sessionId);
    if (!state) return;

    const agentId = payload.agent_id || payload.agentId;
    if (!agentId) return;

    state.subagents ||= {};
    state.subagents[agentId] = {
      span_id: randomHex(8),
      started_at_ns: nowUnixNano(),
      type: payload.agent_type || payload.agentType || "subagent",
    };

    await saveSessionState(sessionId, state);
  });
}

export async function handleSubagentStop() {
  const payload = await readStdinJson();
  const sessionId = getSessionId(payload);
  if (!sessionId) {
    return;
  }

  let spans = [];
  await withSessionLock(sessionId, async () => {
    const state = await loadSessionState(sessionId);
    if (!state) return;

    const agentId = payload.agent_id || payload.agentId;
    if (!agentId || !state.subagents?.[agentId]) return;

    const subagent = state.subagents[agentId];
    const outputValue = sanitizeContent(payload.last_assistant_message || "");
    const subagentSpanId = subagent.span_id;
    const subagentOutputMessages = compact([textPartsMessage("assistant", outputValue)]);

    // Emit the subagent wrapper span
    spans.push(sessionSpan(spanContextOf(state), {
      spanId: subagentSpanId,
      name: `subagent.${subagent.type}`,
      kind: 1,
      startTimeUnixNano: subagent.started_at_ns,
      endTimeUnixNano: nowUnixNano(),
      attributes: [
        attr("orq.span.kind", "agent"),
        attr("claude_code.subagent.id", agentId),
        attr("claude_code.subagent.type", subagent.type),
        subagentOutputMessages.length
          ? attr("gen_ai.output.messages", JSON.stringify(subagentOutputMessages))
          : null,
      ],
    }));

    // The subagent's transcript goes through the same replay, so it gets the
    // same ordering, conversation threading and timing as the main session.
    const agentTranscriptPath = payload.agent_transcript_path || payload.agentTranscriptPath;
    if (agentTranscriptPath) {
      const child = await buildTranscriptSpans({
        traceId: state.trace_id,
        // Its spans hang under the subagent wrapper, and carry the parent
        // session's thread id so the whole session stays one thread.
        rootSpanId: subagentSpanId,
        sessionId: state.session_id,
        startNs: subagent.started_at_ns,
        model: state.model,
        transcriptPath: agentTranscriptPath,
        final: true,
      });
      spans.push(...child.spans);
      state.total_tool_calls = (state.total_tool_calls || 0) + child.toolCallCount;
    }

    delete state.subagents[agentId];
    await saveSessionState(sessionId, state);
  });

  // Send spans outside the lock (network I/O)
  if (spans.length > 0) {
    await sendSpans(spans);
  }
}

export async function runSafely(handler) {
  try {
    if (!enabledTracing()) {
      return;
    }

    await handler();
  } catch (err) {
    // Hooks must not block Claude Code flows, but always surface errors
    // so users know tracing is broken. Full stack only in debug mode.
    process.stderr.write(`[orq-trace] hook error: ${err?.message || err}\n`);
    if (boolEnv("ORQ_DEBUG")) {
      process.stderr.write(`[orq-trace] ${err?.stack}\n`);
    }
  }
}
