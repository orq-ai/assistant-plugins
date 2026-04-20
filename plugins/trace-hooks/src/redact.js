import { boolEnv } from "./common.js";

const SENSITIVE_KEY_PATTERN = /(secret|password|token|api[_-]?key|authorization|private[_-]?key|access[_-]?key)/i;
const SENSITIVE_VALUE_PATTERN = /(sk-[a-z0-9]{16,}|sk_live_[a-z0-9]+|sk_test_[a-z0-9]+|xox[baprs]-|ghp_[a-z0-9]{20,}|ghu_[a-z0-9]+|ghs_[a-z0-9]+|AKIA[A-Z0-9]{16}|-----BEGIN [A-Z ]*PRIVATE KEY-----)/i;
const SENSITIVE_PATH_PATTERN = /(^|\/|\\)\.env(\.|$)/i;

function redactPrimitive(value) {
  if (typeof value === "string") {
    if (SENSITIVE_VALUE_PATTERN.test(value) || SENSITIVE_PATH_PATTERN.test(value)) {
      return "[REDACTED]";
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
  return deepRedact(value);
}
