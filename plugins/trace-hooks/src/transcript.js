import fs from "node:fs/promises";

function textFromContent(content) {
  if (!Array.isArray(content)) {
    return "";
  }

  return content
    .map((block) => {
      if (!block || typeof block !== "object") {
        return "";
      }
      if (typeof block.text === "string") {
        return block.text;
      }
      if (block.type === "tool_use") {
        return JSON.stringify({ type: "tool_use", name: block.name, input: block.input });
      }
      return "";
    })
    .filter(Boolean)
    .join("\n");
}

function partsFromContent(content) {
  if (!Array.isArray(content)) {
    return [];
  }

  return content
    .map((block) => {
      if (!block || typeof block !== "object") {
        return null;
      }
      if (block.type === "thinking" && typeof block.thinking === "string") {
        return { type: "reasoning", content: block.thinking };
      }
      if (typeof block.text === "string") {
        return { type: "text", content: block.text };
      }
      if (block.type === "tool_use") {
        return { type: "tool_call", name: block.name, id: block.id, arguments: block.input };
      }
      return null;
    })
    .filter(Boolean);
}

export async function parseTranscript(transcriptPath, lastProcessedLine = 0, { emitPending = false } = {}) {
  if (!transcriptPath) {
    return {
      messages: [],
      toolCalls: [],
      nextLine: lastProcessedLine,
    };
  }

  let content;
  try {
    content = await fs.readFile(transcriptPath, "utf8");
  } catch {
    return {
      messages: [],
      toolCalls: [],
      nextLine: lastProcessedLine,
    };
  }

  const lines = content.split("\n");
  const messages = [];
  const pendingTools = new Map(); // tool_use_id -> { name, input, startTimestamp }
  const toolCalls = [];

  for (let index = lastProcessedLine; index < lines.length; index += 1) {
    const line = lines[index].trim();
    if (!line) {
      continue;
    }

    let parsed;
    try {
      parsed = JSON.parse(line);
    } catch {
      continue;
    }

    if (parsed?.type === "assistant" && parsed?.message) {
      const message = parsed.message;
      const messageContent = message.content;

      const output = textFromContent(messageContent);
      const usage = message.usage || {};

      // Deduplicate: if the previous message has the same output AND is from
      // the same time window, merge by keeping the one with more tokens (the
      // final version). The timestamp check prevents merging genuinely distinct
      // messages that happen to have identical text (e.g. "Done.").
      const prev = messages.length > 0 ? messages[messages.length - 1] : null;
      const sameWindow = prev?.timestamp && parsed.timestamp &&
        Math.abs(new Date(parsed.timestamp) - new Date(prev.timestamp)) < 2000;
      if (prev && prev.output === output && output && sameWindow) {
        const prevTokens = prev.usage.output_tokens || 0;
        const curTokens = usage.output_tokens || 0;
        if (curTokens >= prevTokens) {
          prev.usage = usage;
          prev.stopReason = message.stop_reason;
          prev.model = message.model || prev.model;
          prev.timestamp = parsed.timestamp;
          // Merge parts: keep tool_call parts from the previous version if the
          // new version lost them (Claude sometimes re-emits the message with
          // higher tokens but without the tool_use content blocks).
          const newParts = partsFromContent(messageContent);
          const hasToolCall = (ps) => ps.some(p => p.type === "tool_call");
          if (hasToolCall(prev.parts) && !hasToolCall(newParts)) {
            // Preserve existing tool_call parts, update the rest
            const prevToolParts = prev.parts.filter(p => p.type === "tool_call");
            const newNonToolParts = newParts.filter(p => p.type !== "tool_call");
            prev.parts = [...newNonToolParts, ...prevToolParts];
          } else {
            prev.parts = newParts;
          }
        }
      } else {
        messages.push({
          model: message.model || "unknown",
          usage,
          output,
          parts: partsFromContent(messageContent),
          stopReason: message.stop_reason,
          timestamp: parsed.timestamp,
        });
      }

      // Extract tool_use blocks with their start timestamps
      if (Array.isArray(messageContent)) {
        for (const block of messageContent) {
          if (block?.type === "tool_use" && block.id) {
            pendingTools.set(block.id, {
              name: block.name || "tool",
              input: block.input,
              startTimestamp: parsed.timestamp,
            });
          }
        }
      }
    }

    // Match tool_result to its tool_use
    if (parsed?.type === "user" && parsed?.message) {
      const userContent = parsed.message.content;
      if (Array.isArray(userContent)) {
        for (const block of userContent) {
          if (block?.type === "tool_result" && block.tool_use_id) {
            const pending = pendingTools.get(block.tool_use_id);
            if (pending) {
              const resultContent = Array.isArray(block.content)
                ? textFromContent(block.content)
                : block.content;
              toolCalls.push({
                name: pending.name,
                input: pending.input,
                output: resultContent,
                startTimestamp: pending.startTimestamp,
                endTimestamp: parsed.timestamp,
              });
              pendingTools.delete(block.tool_use_id);
            }
          }
        }
      }
    }
  }

  // Emit incomplete tool calls (no matching tool_result) so they don't vanish
  // from the trace. Only done at session end to avoid double-emission when
  // the tool_result arrives in a later parse window.
  if (emitPending) for (const pending of pendingTools.values()) {
    toolCalls.push({
      name: pending.name,
      input: pending.input,
      output: "",
      startTimestamp: pending.startTimestamp,
      endTimestamp: null,
      incomplete: true,
    });
  }

  return {
    messages,
    toolCalls,
    nextLine: lines.length,
  };
}

