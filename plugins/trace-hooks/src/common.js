import crypto from "node:crypto";

export function nowUnixNano() {
  return (BigInt(Date.now()) * 1000000n).toString();
}

export function isoToUnixNano(isoString) {
  const ms = new Date(isoString).getTime();
  if (Number.isNaN(ms)) {
    return nowUnixNano();
  }
  return (BigInt(ms) * 1000000n).toString();
}

export function randomHex(bytes) {
  return crypto.randomBytes(bytes).toString("hex");
}

// A span id derived from what the span describes rather than from chance, so
// the same transcript entry always maps to the same span and a replayed window
// cannot mint a second span for a message.
//
// This does not make a span updatable. Storage is append-only and no read path
// collapses duplicates; a repeat within 120 s is dropped by JetStream and a
// later one is stored twice, which draws the span and its subtree twice in the
// waterfall. A span goes out once.
//
// The key must be unique within a trace. A message id alone is not: 5 of 5880
// real assistant messages repeat one. Pairing it with the transcript line
// index fixes that, and the index is stable because the replay always starts
// at line 0.
export function stableSpanId(traceId, key) {
  return crypto.createHash("sha1").update(`${traceId}:${key}`).digest("hex").slice(0, 16);
}

// What a shortened value ends with, wherever it was shortened.
export const ELISION = "... [truncated]";

// Cuts text to fit `maxBytes` including the marker, so a cap means the size the
// caller asked for and not that plus the marker. Slicing bytes rather than code
// points can land mid-sequence and decode to U+FFFD, so a trailing one goes.
export function truncateBytes(text, maxBytes) {
  if (Buffer.byteLength(text, "utf8") <= maxBytes) {
    return text;
  }
  const keep = maxBytes - ELISION.length;
  if (keep <= 0) {
    return ELISION.slice(0, Math.max(0, maxBytes));
  }
  return (
    Buffer.from(text, "utf8").subarray(0, keep).toString("utf8").replace(/�$/, "") + ELISION
  );
}

export function toStringValue(value) {
  if (value === null || value === undefined) {
    return "";
  }
  if (typeof value === "string") {
    return value;
  }
  return JSON.stringify(value);
}

export async function readStdinJson() {
  const chunks = [];
  for await (const chunk of process.stdin) {
    chunks.push(chunk);
  }

  if (chunks.length === 0) {
    return {};
  }

  const raw = Buffer.concat(chunks).toString("utf8").trim();
  if (!raw) {
    return {};
  }

  try {
    return JSON.parse(raw);
  } catch (err) {
    process.stderr.write(`[orq-trace] WARN: stdin JSON parse failed: ${err?.message}; length=${raw.length}\n`);
    return {};
  }
}

export function attr(key, value) {
  if (value === undefined || value === null) {
    return null;
  }
  if (typeof value === "string") {
    return { key, value: { stringValue: value } };
  }
  if (typeof value === "boolean") {
    return { key, value: { boolValue: value } };
  }
  if (typeof value === "number") {
    if (Number.isInteger(value)) {
      return { key, value: { intValue: String(value) } };
    }
    return { key, value: { doubleValue: value } };
  }
  if (Array.isArray(value)) {
    return {
      key,
      value: {
        arrayValue: {
          values: value.map((item) => {
            if (typeof item === "string") return { stringValue: item };
            if (typeof item === "boolean") return { boolValue: item };
            if (typeof item === "number") {
              return Number.isInteger(item)
                ? { intValue: String(item) }
                : { doubleValue: item };
            }
            return { stringValue: JSON.stringify(item) };
          }),
        },
      },
    };
  }
  return { key, value: { stringValue: JSON.stringify(value) } };
}

export function compact(list) {
  return list.filter(Boolean);
}

export function boolEnv(name, defaultValue = false) {
  const value = process.env[name];
  if (!value) {
    return defaultValue;
  }
  return value === "1" || value.toLowerCase() === "true";
}

// Appends to a debug log when ORQ_DEBUG is set. Uses the platform temp
// directory rather than a hardcoded /tmp, which does not exist on Windows and
// is not where a macOS user would look either.
export async function debugLog(line) {
  if (!boolEnv("ORQ_DEBUG")) {
    return;
  }
  try {
    const [fs, os, path] = await Promise.all([
      import("node:fs/promises"),
      import("node:os"),
      import("node:path"),
    ]);
    await fs.appendFile(path.join(os.tmpdir(), "orq-trace-debug.log"), line, "utf8");
  } catch {
    // Debug logging must never break a hook.
  }
}

