import { boolEnv } from "./common.js";

const SENSITIVE_KEY_PATTERN = /(secret|password|token|api[_-]?key|authorization|private[_-]?key|access[_-]?key)/i;
const SENSITIVE_VALUE_PATTERN = /(sk-[a-z0-9]{16,}|sk_live_[a-z0-9]+|sk_test_[a-z0-9]+|xox[baprs]-|ghp_[a-z0-9]{20,}|ghu_[a-z0-9]+|ghs_[a-z0-9]+|AKIA[A-Z0-9]{16}|-----BEGIN [A-Z ]*PRIVATE KEY-----)/i;
const SENSITIVE_PATH_PATTERN = /(^|\/|\\)\.env(\.|$)/i;

const MAX_JSON_REDACT_LEN = 10000;

function redactPrimitive(value) {
  if (typeof value === "string") {
    if (SENSITIVE_VALUE_PATTERN.test(value) || SENSITIVE_PATH_PATTERN.test(value)) {
      return "[REDACTED]";
    }
    // Try to redact secrets embedded in stringified JSON (e.g. tool outputs
    // like '{"password":"hunter2"}'). Key-based redaction only fires on
    // parsed objects, so we need to parse, redact, and re-stringify.
    if (value.length > 2 && value.length < MAX_JSON_REDACT_LEN &&
        (value[0] === "{" || value[0] === "[")) {
      try {
        const parsed = JSON.parse(value);
        try {
          const redacted = deepRedact(parsed);
          const redactedStr = JSON.stringify(redacted);
          // Only return re-stringified form when something was actually redacted.
          // Re-stringifying unchanged data silently normalizes whitespace and
          // number formatting, making payloads differ from the original.
          return redactedStr !== JSON.stringify(parsed) ? redactedStr : value;
        } catch (redactErr) {
          process.stderr.write(`[orq-trace] WARN: redact failed on JSON string, returning [REDACTED]: ${redactErr?.message}\n`);
          return "[REDACTED]";
        }
      } catch {
        // SyntaxError from JSON.parse — not valid JSON, return as-is
      }
    }
  }
  return value;
}

export function deepRedact(value) {
  if (value === null || value === undefined) {
    return value;
  }

  if (Array.isArray(value)) {
    return value.map((item) => deepRedact(item));
  }

  if (typeof value === "object") {
    const output = {};
    for (const [key, current] of Object.entries(value)) {
      if (SENSITIVE_KEY_PATTERN.test(key)) {
        output[key] = "[REDACTED]";
      } else {
        output[key] = deepRedact(current);
      }
    }
    return output;
  }

  return redactPrimitive(value);
}

export function sanitizeContent(value) {
  const stripBodies = boolEnv("TRACE_ORQ_REDACT_CONTENT", false);
  if (stripBodies) {
    return "[REDACTED]";
  }

  const redacted = deepRedact(value);

  const maxLen = parseInt(process.env.ORQ_TRACE_MAX_CONTENT_LEN, 10);
  if (!maxLen || maxLen <= 0) {
    return redacted;
  }

  const str = typeof redacted === "string" ? redacted : JSON.stringify(redacted);
  if (str && str.length > maxLen) {
    return str.slice(0, maxLen) + ` ... [truncated, original_length=${str.length}]`;
  }

  return redacted;
}
