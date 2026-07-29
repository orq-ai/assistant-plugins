// Replays a parsed transcript into ordered steps.
//
// This is the whole session, always, from the first line. Deciding which steps
// become spans belongs to the caller. Keeping that decision out of here is what
// makes "replay everything, emit some of it" visible in the code.
//
// Each step reports:
//   kind         "user" | "tool" | "llm" | "compact"
//   lineIndex    position in the transcript, the emit cursor's unit
//   emittable    whether this step can become a span at all
//   alwaysNew    emit regardless of the cursor (an unfinished tool call)
//   conversation for llm steps, the thread as it stood before the response

import { isoToUnixNano, toStringValue } from "./common.js";
import { sanitizeContent } from "./redact.js";
import {
  textPartsMessage,
  toolCallPartsMessage,
  toolResponsePartsMessage,
} from "./messages.js";

// Order everything by its position in the transcript. That single key gets the
// call/result order right for free: a message keeps the index of its first
// streamed chunk while a tool takes the index of its result line, so a call
// always precedes the result it produced. Sorting by timestamp inverted them,
// because merging a streamed message advances its timestamp to the final chunk
// while the tool's start timestamp came from the first.
function orderedTimeline(parsed) {
  return [
    ...parsed.userPrompts.map((data) => ({ kind: "user", data })),
    ...parsed.messages.map((data) => ({ kind: "llm", data })),
    ...parsed.toolCalls.map((data) => ({ kind: "tool", data })),
    ...(parsed.compactBoundaries ?? []).map((data) => ({ kind: "compact", data })),
  ].sort((a, b) => (a.data.lineIndex ?? 0) - (b.data.lineIndex ?? 0));
}

// PostToolUse records the payload into the session state, where it is capped.
// That copy has the better shape, since the hook hands over structured input and
// response where the transcript flattens a tool_result to text, so it stays the
// default while it is whole. Once the cap has cut it the transcript holds the
// only complete copy, and a state-file limit is no reason for a trace to show
// less than the tool produced. It cut 3.2% of results across 280 transcripts.
function pickWholest(recordedValue, recordedSizeBytes, fromTranscript) {
  if (recordedValue === undefined || recordedValue === null) return fromTranscript;
  const kept = Buffer.byteLength(toStringValue(recordedValue), "utf8");
  const wasCut = typeof recordedSizeBytes === "number" && recordedSizeBytes > kept;
  if (!wasCut) return recordedValue;
  return fromTranscript === undefined || fromTranscript === null ? recordedValue : fromTranscript;
}

function sanitizeParts(parts) {
  return (parts || []).map((part) =>
    part.type === "tool_call"
      ? {
          type: "tool_call",
          id: part.id,
          name: part.name,
          arguments: sanitizeContent(part.arguments),
        }
      : { ...part, content: sanitizeContent(part.content) },
  );
}

export function replay(parsed, { startNs = null, recordedByToolUseId = new Map() } = {}) {
  // Carries the clock across entries so an llm step, which has only one
  // timestamp, gets a start time instead of a zero duration. Seeded from the
  // session start for the very first entry only.
  let previousEndNs = startNs;
  const conversation = [];
  const steps = [];

  for (const entry of orderedTimeline(parsed)) {
    const lineIndex = entry.data.lineIndex ?? 0;

    if (entry.kind === "compact") {
      // Everything before this point left the model's context. The summary
      // that replaced it is injected right after as a user message, so the
      // replay picks the conversation back up from there on its own.
      conversation.length = 0;
      steps.push({ kind: "compact", lineIndex, emittable: false });
      continue;
    }

    if (entry.kind === "user") {
      const message = textPartsMessage("user", sanitizeContent(entry.data.text));
      if (message) conversation.push(message);
      // Advance the clock to the prompt, so the response step measures the
      // model's latency rather than however long the user spent typing.
      if (entry.data.timestamp) previousEndNs = isoToUnixNano(entry.data.timestamp);
      steps.push({ kind: "user", lineIndex, emittable: false });
      continue;
    }

    if (entry.kind === "tool") {
      const tool = entry.data;
      const recorded = tool.id && tool.name !== "Skill" ? recordedByToolUseId.get(tool.id) : null;
      const input = sanitizeContent(pickWholest(recorded?.tool_input, recorded?.tool_input_size_bytes, tool.input));
      const output = sanitizeContent(
        pickWholest(recorded?.tool_response, recorded?.tool_response_size_bytes, tool.output),
      );
      const startTimeUnixNano = tool.startTimestamp ? isoToUnixNano(tool.startTimestamp) : undefined;
      const endTimeUnixNano = tool.endTimestamp ? isoToUnixNano(tool.endTimestamp) : undefined;

      steps.push({
        kind: "tool",
        lineIndex,
        emittable: true,
        // An incomplete call has no result line, so its lineIndex is the call's
        // own position, which usually predates the cursor. It is only parsed at
        // all on the final read, and has never been sent, so the cursor must
        // not exclude it.
        alwaysNew: Boolean(tool.incomplete),
        tool,
        recorded,
        input,
        output,
        startTimeUnixNano,
        endTimeUnixNano,
      });

      previousEndNs = endTimeUnixNano || startTimeUnixNano || previousEndNs;
      // Append the result as a tool_call_response part carrying the tool_use_id
      // so the UI can pair it with the tool_call part that requested it.
      conversation.push(toolResponsePartsMessage(tool.id, output));
      continue;
    }

    const message = entry.data;
    const rawOutput = sanitizeContent(message.output || "");
    const parts = sanitizeParts(message.parts);

    // A tool-only turn produces no text, but its tool_call parts are what the
    // UI renders, so an empty parts array is only backfilled from the raw
    // output text when the transcript gave us nothing structured.
    const fallbackText = toStringValue(rawOutput);
    const outputParts = parts.length > 0
      ? parts
      : fallbackText
        ? [{ type: "text", content: fallbackText }]
        : [];

    const endTimeUnixNano = message.timestamp ? isoToUnixNano(message.timestamp) : undefined;
    // Never let start exceed end; that would produce a negative duration.
    let startTimeUnixNano = previousEndNs || endTimeUnixNano;
    if (startTimeUnixNano && endTimeUnixNano && BigInt(startTimeUnixNano) > BigInt(endTimeUnixNano)) {
      startTimeUnixNano = endTimeUnixNano;
    }

    steps.push({
      kind: "llm",
      lineIndex,
      emittable: true,
      message,
      outputMessages: [{
        role: "assistant",
        parts: outputParts,
        finish_reason: message.stopReason || "stop",
      }],
      // The thread that led to this response: everything received so far,
      // before this assistant message. Copied so a caller holding onto it does
      // not see the rest of the replay mutate it.
      conversation: [...conversation],
      startTimeUnixNano,
      endTimeUnixNano,
    });

    // Push the response into the history as the model would have seen it:
    // reasoning and prose in one assistant message, then the tool calls as a
    // second one whose tool_call parts carry the ids the results pair back to.
    // Reasoning is the first thing to drop if payloads get tight: the thinking
    // is often longer than the answer, and it is replayed into every later step.
    const contentParts = parts.filter((part) => part.type === "reasoning" || part.type === "text");
    const toolCallParts = parts.filter((part) => part.type === "tool_call");
    if (contentParts.length > 0) {
      conversation.push({ role: "assistant", parts: contentParts });
    }
    if (toolCallParts.length > 0) {
      conversation.push(toolCallPartsMessage(toolCallParts));
    }
    if (contentParts.length === 0 && toolCallParts.length === 0) {
      const fallback = textPartsMessage("assistant", rawOutput);
      if (fallback) conversation.push(fallback);
    }
    previousEndNs = endTimeUnixNano || previousEndNs;
  }

  return { steps, conversation };
}
