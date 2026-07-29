// OTel GenAI semconv messages: { role, parts: [...] }.
//
// The orq backend stores gen_ai.input.messages / gen_ai.output.messages
// verbatim and pairs tool calls with their results on the part `id`, so this
// shape is what makes tool output render in span detail and in the thread view.

import { ELISION, toStringValue, truncateBytes } from "./common.js";

export function textPartsMessage(role, content) {
  const text = toStringValue(content);
  if (!text) {
    return null;
  }
  return { role, parts: [{ type: "text", content: text }] };
}

// Tool payloads are replayed whole. Capping them at 2 KB was tried and dropped:
// on the 40 largest local sessions it changed nothing about how many turns
// survive the per-span budget, because that budget goes on prose, and it saved
// 6% of the wire. `shrinkPart` still shortens them once a conversation has
// actually blown its budget, which is when something has to give.
export function toolCallPartsMessage(toolCallParts) {
  return {
    role: "assistant",
    parts: toolCallParts.map((part) => ({
      type: "tool_call",
      id: part.id,
      name: part.name,
      arguments: part.arguments,
    })),
  };
}

export function toolResponsePartsMessage(toolCallId, response) {
  return {
    role: "tool",
    parts: [
      {
        type: "tool_call_response",
        id: toolCallId,
        response: toStringValue(response),
      },
    ],
  };
}

// Ceiling for the root span's conversation. A safety rail, not a routine
// budget: measured over 133 real sessions the conversation is 31 KB at p50 and
// 483 KB at p90, and resetting at each compaction boundary puts it further out
// of reach still.
export const CONVERSATION_MAX_BYTES = 8 * 1024 * 1024;

// Every chat span snapshots the conversation replayed so far, so the cost of a
// window is quadratic in its length: one 672-span window produced 184 MB of
// gen_ai.input.messages. A normal turn is far below this ceiling.
export const SPAN_CONVERSATION_MAX_BYTES = 256 * 1024;


// Largest a single part may be once a conversation has to shrink. Prose is the
// point of the history so it keeps the bigger allowance. Tool payloads get a
// small one because the execute_tool span that produced them still has them
// whole, and they are 81% of a replayed conversation.
const PART_MAX_BYTES = 32 * 1024;
const TOOL_PART_MAX_BYTES = 2 * 1024;

// Which field carries a part's payload. `arguments` is included because a
// message holding one big tool_call could otherwise never be brought under
// budget, and the fallback would then keep a lone tool_call_response with no
// call to pair against, the exact defect the parts shape exists to prevent.
function payloadFieldOf(part) {
  if (part.type === "tool_call_response") return "response";
  if (part.type === "tool_call") return "arguments";
  return "content";
}

function isToolPart(part) {
  return part.type === "tool_call" || part.type === "tool_call_response";
}

function shrinkPart(part) {
  const field = payloadFieldOf(part);
  const limit = isToolPart(part) ? TOOL_PART_MAX_BYTES : PART_MAX_BYTES;
  const raw = part[field];
  // tool_call arguments arrive as an object; measure and elide what would be
  // serialized, keeping the field a string only when it had to be cut.
  const value = typeof raw === "string" ? raw : raw === undefined ? "" : JSON.stringify(raw);
  if (Buffer.byteLength(value, "utf8") <= limit) {
    return part;
  }
  return { ...part, [field]: truncateBytes(value, limit) };
}

function shrinkMessage(message) {
  return { ...message, parts: (message.parts ?? []).map(shrinkPart) };
}

const sizeOf = (value) => Buffer.byteLength(JSON.stringify(value), "utf8");

// Brings a conversation under `maxBytes`, cheapest step first:
//
//   1. shrink oversized parts, which usually suffices and keeps every message
//      so nothing loses its pairing;
//   2. only if still over, keep the newest messages that fit, dragging along
//      the tool_call any kept response needs. A response with no call to pair
//      against is the bug the parts shape exists to fix.
//
// One pass, newest first, with sizes computed once. Re-serializing per dropped
// message is quadratic and this runs on every chat span.
//
// `truncated` is only set when something was actually lost.
export function fitConversation(conversation, maxBytes = CONVERSATION_MAX_BYTES) {
  if (sizeOf(conversation) <= maxBytes) {
    return { messages: conversation, truncated: false };
  }

  const messages = conversation.map(shrinkMessage);
  const shrank = messages.some((message, index) => sizeOf(message) !== sizeOf(conversation[index]));
  if (sizeOf(messages) <= maxBytes) {
    return { messages, truncated: shrank };
  }

  const sizes = messages.map(sizeOf);
  // Where each tool_call lives, so a kept response can pull its call along.
  const callIndexById = new Map();
  messages.forEach((message, index) => {
    for (const part of message.parts ?? []) {
      if (part.type === "tool_call" && part.id) callIndexById.set(part.id, index);
    }
  });

  const keep = new Set();
  let bytes = 2; // the enclosing brackets
  for (let index = messages.length - 1; index >= 0; index -= 1) {
    if (keep.has(index)) continue;
    const group = [index];
    for (const part of messages[index].parts ?? []) {
      if (part.type !== "tool_call_response" || !part.id) continue;
      const callIndex = callIndexById.get(part.id);
      if (callIndex !== undefined && callIndex !== index && !keep.has(callIndex)) {
        group.push(callIndex);
      }
    }
    const cost = group.reduce((total, at) => total + sizes[at] + 1, 0);
    if (bytes + cost > maxBytes) break;
    for (const at of group) keep.add(at);
    bytes += cost;
  }

  // Never return nothing: one message over budget on its own still beats an
  // empty conversation. Keep its tool_call too, or a lone response ships with
  // no call to pair against.
  if (keep.size === 0) {
    const last = messages.length - 1;
    keep.add(last);
    for (const part of messages[last]?.parts ?? []) {
      if (part.type !== "tool_call_response" || !part.id) continue;
      const callIndex = callIndexById.get(part.id);
      if (callIndex !== undefined) keep.add(callIndex);
    }
  }

  return { messages: messages.filter((_, index) => keep.has(index)), truncated: true };
}

// The closing assistant text of a conversation, used as the root span's output.
export function lastAssistantText(conversation) {
  for (let index = conversation.length - 1; index >= 0; index -= 1) {
    const message = conversation[index];
    if (message.role !== "assistant") continue;
    const text = (message.parts ?? [])
      .filter((part) => part.type === "text")
      .map((part) => part.content)
      .filter(Boolean)
      .join("\n");
    if (text) return text;
  }
  return "";
}

// Falls back to the last tool result for a session that ended mid-tool-call,
// where there is no closing assistant message to report.
export function lastToolResult(conversation) {
  for (let index = conversation.length - 1; index >= 0; index -= 1) {
    const message = conversation[index];
    if (message.role !== "tool") continue;
    const response = (message.parts ?? []).find((part) => part.type === "tool_call_response");
    if (response?.response) return response.response;
  }
  return "";
}
