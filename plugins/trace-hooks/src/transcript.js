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
      // Thinking is almost always withheld: the content is encrypted in
      // `signature` and `thinking` is left empty, in 4013 of 4037 blocks across
      // 153 local transcripts. That `signature` runs 300 to 75360 chars and
      // tracks thinking length at r=0.97, so the reasoning is there but
      // unreadable. Emitting the empty string drew a blank Reasoning block and
      // dropping the part would hide that the model reasoned at all, so mark it
      // the way the gateway does (libs/go/gateway/tracing.go), spelled out
      // rather than its bare "[encrypted]".
      if (block.type === "thinking") {
        const thinking = typeof block.thinking === "string" ? block.thinking : "";
        if (thinking) return { type: "reasoning", content: thinking };
        return block.signature ? { type: "reasoning", content: "[encrypted reasoning]" } : null;
      }
      // Safety-redacted thinking never carries plaintext at all.
      if (block.type === "redacted_thinking") {
        return { type: "reasoning", content: "[encrypted reasoning]" };
      }
      if (typeof block.text === "string") {
        return block.text ? { type: "text", content: block.text } : null;
      }
      if (block.type === "tool_use") {
        return { type: "tool_call", name: block.name, id: block.id, arguments: block.input };
      }
      return null;
    })
    .filter(Boolean);
}

// Counts the complete lines in a transcript: everything but the trailing
// element, which is either "" (every JSONL append ends with a newline) or a
// line still being written. Exported because the parse cursor has to be
// seeded by the same rule the parser consumes by; two copies of it drift.
export async function countCompleteLines(transcriptPath) {
  if (!transcriptPath) return 0;
  try {
    const content = await fs.readFile(transcriptPath, "utf8");
    return Math.max(0, content.split("\n").length - 1);
  } catch {
    return 0;
  }
}

// Always replays the whole transcript. Which of the parsed entries turn into
// spans is the caller's decision, made against its own cursor. This returns
// nothing that could move that cursor backwards.
export async function parseTranscript(transcriptPath, { emitPending = false } = {}) {
  const empty = {
    messages: [],
    toolCalls: [],
    userPrompts: [],
    compactBoundaries: [],
    nextLine: 0,
  };
  if (!transcriptPath) {
    return empty;
  }

  let content;
  try {
    content = await fs.readFile(transcriptPath, "utf8");
  } catch {
    return empty;
  }

  const lines = content.split("\n");
  // The last element is either "" (every JSONL append ends with a newline) or a
  // line still being written. Consuming it advances the cursor past an entry
  // that was never parsed, silently dropping it from the trace.
  let lineCount = Math.max(0, lines.length - 1);
  if (emitPending && lines.length > 0) {
    const tail = lines[lines.length - 1].trim();
    if (tail) {
      try {
        JSON.parse(tail);
        lineCount = lines.length;
      } catch {
        // Partial write, leave it for the next parse.
      }
    }
  }
  const messages = [];
  const pendingTools = new Map(); // tool_use_id -> { name, input, startTimestamp }
  const toolCalls = [];
  const userPrompts = [];
  // Where the model's context was thrown away. Everything before one of these
  // is no longer in context: the transcript keeps it, the model does not.
  const compactBoundaries = [];
  // Skill tool_result is just "Launching skill: X". The actual skill body is
  // injected on the next user turn as a plain text message. Capture it and
  // append to the Skill tool's output so the span carries the loaded content.
  let pendingSkillCall = null;

  for (let index = 0; index < lineCount; index += 1) {
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

    // Compaction drops most of the context and replaces it with a summary,
    // which the transcript then injects as an ordinary user message. Record
    // where that happened so the replay can reset the conversation there
    // instead of feeding spans messages the model no longer holds.
    if (parsed?.type === "system" && parsed?.subtype === "compact_boundary") {
      compactBoundaries.push({ lineIndex: index, timestamp: parsed.timestamp });
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
      // Same response: each content block (thinking/text/tool_use) is its own
      // entry sharing one message.id + usage; merge so usage counts once.
      const sameMessage = prev?.messageId && prev.messageId === message.id;
      if (prev && sameMessage) {
        const newParts = partsFromContent(messageContent);
        prev.parts.push(...newParts);
        const newText = newParts.filter((p) => p.type === "text").map((p) => p.content).join("\n");
        if (newText) prev.output = prev.output ? `${prev.output}\n${newText}` : newText;
        if ((usage.output_tokens || 0) >= (prev.usage.output_tokens || 0)) prev.usage = usage;
        // stop_reason/end-time live on the final block (earlier ones are mid-stream);
        // take the latest so finish_reason and span duration aren't stale.
        if (message.stop_reason) prev.stopReason = message.stop_reason;
        if (message.model) prev.model = message.model;
        prev.timestamp = parsed.timestamp;
      } else if (prev && prev.output === output && output && sameWindow) {
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
          messageId: message.id,
          model: message.model || "unknown",
          usage,
          output,
          parts: partsFromContent(messageContent),
          stopReason: message.stop_reason,
          timestamp: parsed.timestamp,
          // Transcript position of the FIRST chunk of this message. Merging a
          // streamed response advances its timestamp to the final chunk, so
          // only this index still orders the message before the tools it asked
          // for. Everything downstream sorts on it.
          lineIndex: index,
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
              messageId: message.id,
              startLineIndex: index,
            });
          }
        }
      }
    }

    // Match tool_result to its tool_use
    if (parsed?.type === "user" && parsed?.message) {
      const userContent = parsed.message.content;
      // A Skill's body lands in the user entry right after its tool result, and
      // always as content blocks. All 10 real Skill calls in local transcripts do
      // both. Claim that one entry, then disarm whether or not it turned up.
      // Left armed, or matched against string content, this swallows the next
      // typed prompt or a post-compaction summary onto the Skill span.
      const skillAwaitingBody =
        pendingSkillCall && pendingSkillCall.armedAtLine < index ? pendingSkillCall : null;
      if (skillAwaitingBody) pendingSkillCall = null;
      if (Array.isArray(userContent)) {
        let hadToolResult = false;
        for (const block of userContent) {
          if (block?.type === "tool_result" && block.tool_use_id) {
            hadToolResult = true;
            const pending = pendingTools.get(block.tool_use_id);
            if (pending) {
              const resultContent = Array.isArray(block.content)
                ? textFromContent(block.content)
                : block.content;
              const call = {
                id: block.tool_use_id,
                name: pending.name,
                input: pending.input,
                output: resultContent,
                startTimestamp: pending.startTimestamp,
                endTimestamp: parsed.timestamp,
                messageId: pending.messageId,
                // The result's own position. Always after the message that
                // requested it, which is what orders a call before its result.
                lineIndex: index,
              };
              toolCalls.push(call);
              pendingTools.delete(block.tool_use_id);
              if (pending.name === "Skill") {
                pendingSkillCall = { call, armedAtLine: index };
              }
            }
          }
        }
        // After a Skill tool_result, the next plain user text message holds the
        // loaded skill body. Capture it once and append to the Skill span output.
        if (!hadToolResult && skillAwaitingBody) {
          const text = userContent
            .map((b) => (b?.type === "text" && typeof b.text === "string" ? b.text : ""))
            .filter(Boolean)
            .join("\n");
          if (text) {
            const { call } = skillAwaitingBody;
            call.output = `${call.output ? `${call.output}\n\n` : ""}${text}`;
          }
        }
      } else if (typeof userContent === "string" && userContent.trim()) {
        // A typed prompt. Claude Code writes these as plain string content;
        // tool results are always arrays, and array-of-text entries are skill
        // bodies or system reminders handled above. Without these the replayed
        // conversation loses every user turn after the first.
        userPrompts.push({
          text: userContent,
          timestamp: parsed.timestamp,
          lineIndex: index,
        });
      }
    }
  }

  // Emit incomplete tool calls (no matching tool_result) so they don't vanish
  // from the trace. Only done at session end to avoid double-emission when
  // the tool_result arrives in a later parse window.
  if (emitPending) for (const [id, pending] of pendingTools.entries()) {
    toolCalls.push({
      id,
      name: pending.name,
      input: pending.input,
      output: "",
      startTimestamp: pending.startTimestamp,
      endTimestamp: null,
      messageId: pending.messageId,
      // No result line exists, so fall back to the call's own position.
      lineIndex: pending.startLineIndex,
      incomplete: true,
    });
  }

  return {
    messages,
    toolCalls,
    userPrompts,
    compactBoundaries,
    nextLine: lineCount,
  };
}

