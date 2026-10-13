import { boolEnv } from "./common.js";

const SENSITIVE_KEY_PATTERN = /(secret|password|token|api[_-]?key|authorization|private[_-]?key|access[_-]?key)/i;
// Free-text secrets only reach this pattern, never SENSITIVE_KEY_PATTERN, so
// the shapes a coding session actually pastes have to be here. Three of them
// used to slip through: the `sk-` run allows dashes, because every current
// provider key is segmented (`sk-ant-api03-...`, `sk-proj-...`, `sk-orq-...`);
// a JWT stands alone, which is how an orq key issued from the dashboard looks
// and what an `Authorization` header carries; and a bearer token is redacted
// only when it reads like a token, so "Bearer authentication" stays legible.
//
// The `sk-` branch needs both guards it carries. Without `\b` it matches the
// `sk-` inside `task-`, `risk-` and `disk-`, and without a digit somewhere in
// the run it matches any long kebab-case identifier. A hit costs the whole
// string, not the match, so a false positive throws away an entire tool
// output.
//
// Both character classes carry `_` because provider key bodies are base64url,
// which includes it. Leaving it out ended the run at the first underscore, so
// a measured 8.8% of `sk-ant-api03-` and 16.9% of `sk-proj-` keys reached the
// span in plaintext. The lookahead is bounded for the same reason it exists:
// unbounded, it rescans and backtracks over the whole run at every `sk-`
// start, which took 16.8s on 240k characters of `sk-` and would outlive the
// 30s hook timeout on a longer one. Bounded at 64 it takes 0.02s. A key whose
// first 64 body characters hold no digit at all is still kept. Over 200k
// random base64url bodies per length that is 1.7% at 24 characters and 0.03%
// at 48, and it is the price of not redacting every long kebab-case
// identifier.
const SENSITIVE_VALUE_PATTERN = /(\bsk-(?=[a-z0-9_-]{0,64}[0-9])[a-z0-9][a-z0-9_-]{15,}|sk_live_[a-z0-9]+|sk_test_[a-z0-9]+|xox[baprs]-|ghp_[a-z0-9]{20,}|ghu_[a-z0-9]+|ghs_[a-z0-9]+|github_pat_[a-z0-9_]{22,}|AIza[a-z0-9_-]{35}|hf_[a-z0-9]{30,}|AKIA[A-Z0-9]{16}|eyJ[a-z0-9_=-]{8,}\.[a-z0-9_=-]{8,}|bearer\s+(?=[a-z0-9._~+\/=-]{16,})[a-z0-9._~+\/=-]*[0-9]|-----BEGIN [A-Z ]*PRIVATE KEY-----)/i;
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
