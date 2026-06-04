// match.mjs — small deterministic matchers shared by the grader.
//
// A "matcher" is either a bare string (shorthand for { op: "contains", value })
// or an object { op, value }. Supported ops:
//   exact    — String(actual) === String(value)
//   contains — String(actual).includes(String(value))
//   regex    — new RegExp(value).test(String(actual))  (value may include (?i) inline flags)
//   exists   — actual is not null/undefined
//   oneOf    — value is an array; actual equals one of its entries (string compare)

export function normalizeMatcher(m) {
  if (m == null) return { op: "exists", value: null };
  if (typeof m === "string") return { op: "contains", value: m };
  if (typeof m === "object" && m.op) return m;
  // { value: "x" } with no op → contains
  if (typeof m === "object" && "value" in m) return { op: "contains", value: m.value };
  return { op: "contains", value: String(m) };
}

export function matchValue(matcher, actual) {
  const { op, value } = normalizeMatcher(matcher);
  const a = actual == null ? "" : typeof actual === "string" ? actual : JSON.stringify(actual);
  switch (op) {
    case "exact":
      return a === String(value);
    case "contains":
      return a.includes(String(value));
    case "regex":
      try {
        // JS RegExp has no inline (?i) flags — translate a leading (?flags) group
        // into real RegExp flags so YAML authors can write (?i)... naturally.
        let pattern = String(value);
        let flags = "";
        const lead = pattern.match(/^\(\?([a-z]+)\)/);
        if (lead) {
          flags = lead[1];
          pattern = pattern.slice(lead[0].length);
        }
        return new RegExp(pattern, flags).test(a);
      } catch {
        return false;
      }
    case "exists":
      return actual !== undefined && actual !== null && a !== "";
    case "oneOf":
      return (Array.isArray(value) ? value : [value]).some((v) => a === String(v));
    default:
      return false;
  }
}

/** Match a text matcher against the response text. */
export function matchText(matcher, text) {
  return matchValue(matcher, text ?? "");
}

/** Match an args spec { key: matcher, ... } against a captured tool input object. */
export function matchArgs(argsSpec, input) {
  if (!argsSpec) return { ok: true, fails: [] };
  const fails = [];
  for (const [key, matcher] of Object.entries(argsSpec)) {
    if (!matchValue(matcher, input?.[key])) fails.push(key);
  }
  return { ok: fails.length === 0, fails };
}
