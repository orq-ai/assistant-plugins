// grade.mjs — turn one captured trace into a deterministic verdict.
//
// Verdict is a superset of resources/verdict.schema.json with the same field
// names so reports stay diffable and CI-gradeable. Gating = routing ∧ tool_calls
// ∧ text ∧ no-forbidden. Soft prose assertions live in `notes` and are NOT gated.

import { shortName } from "./capture.mjs";
import { matchArgs, matchText } from "./match.mjs";

/**
 * @param {object} spec  one case from a *.cases.yaml (with .id, .type, .expect, .notes)
 * @param {string} skill the skill the case file belongs to
 * @param {object} captured  output of capture.runCase()
 */
export function gradeCase(spec, skill, captured) {
  const expect = spec.expect || {};
  const checks = [];
  const add = (id, pass, notes) => checks.push({ id, pass, notes: notes ?? "" });

  const selected = captured.selectedSkills || [];
  const toolCalls = captured.toolCalls || [];
  const text = captured.text || "";

  // --- harness sanity: did the run even complete? ---
  const ran = captured.exitCode === 0 && !captured.timedOut;
  if (!ran) {
    add("run-completed", false, captured.timedOut ? "timed out" : `exit=${captured.exitCode} ${captured.stderr || ""}`.trim());
  }

  // --- routing ---
  const isNegative = spec.type === "negative" || expect.not_routing != null;
  let routingPass = true;
  if (isNegative) {
    const forbidden = expect.not_routing || skill;
    routingPass = !selected.includes(forbidden);
    add(`routing:not-${forbidden}`, routingPass, `selected=[${selected.join(",")}]`);
  } else if (expect.routing) {
    routingPass = selected.includes(expect.routing);
    add(`routing:${expect.routing}`, routingPass, `selected=[${selected.join(",")}]`);
  }

  // --- tool calls (ordering: any | subsequence | strict) ---
  const order = expect.order || "any";
  const expectedCalls = expect.tool_calls || [];
  let toolPass = true;
  let cursor = 0; // for ordered matching
  for (const ec of expectedCalls) {
    let foundIdx = -1;
    const start = order === "any" ? 0 : cursor;
    for (let i = start; i < toolCalls.length; i++) {
      if (shortName(ec.tool) !== shortName(toolCalls[i].name)) continue;
      if (!matchArgs(ec.args, toolCalls[i].input).ok) continue;
      if (order === "strict" && i !== cursor) break; // must be the very next call
      foundIdx = i;
      break;
    }
    const ok = foundIdx !== -1;
    add(`tool:${shortName(ec.tool)}`, ok, ok ? `@${foundIdx}` : "missing");
    if (!ok) toolPass = false;
    else if (order !== "any") cursor = foundIdx + 1;
  }

  // --- required text patterns ---
  let textPass = true;
  for (const [i, pat] of (expect.text || []).entries()) {
    const ok = matchText(pat, text);
    add(`text:${i}`, ok, ok ? "matched" : `no match for ${JSON.stringify(pat)}`);
    if (!ok) textPass = false;
  }

  // --- forbidden tools / text (anti-patterns) ---
  let forbiddenPass = true;
  for (const ft of expect.forbidden_tools || []) {
    const present = toolCalls.some((tc) => shortName(tc.name) === shortName(ft));
    add(`forbidden-tool:${shortName(ft)}`, !present, present ? "PRESENT" : "absent");
    if (present) forbiddenPass = false;
  }
  for (const [i, fp] of (expect.forbidden_text || []).entries()) {
    const present = matchText(fp, text);
    add(`forbidden-text:${i}`, !present, present ? `matched ${JSON.stringify(fp)}` : "absent");
    if (present) forbiddenPass = false;
  }

  const usagePass = toolPass && textPass && forbiddenPass;
  const overall = ran && routingPass && usagePass;
  const passed = checks.filter((c) => c.pass).length;
  const score = checks.length ? Math.round((100 * passed) / checks.length) : 0;

  return {
    scenario_id: `${skill}/${spec.id}`,
    skill,
    invocation_type: spec.type,
    expected_skill: isNegative ? null : expect.routing ?? null,
    selected_skill: selected.length ? selected.join("+") : null,
    routing: routingPass ? "pass" : "fail",
    usage: isNegative ? "n/a" : usagePass ? "pass" : "fail",
    overall_pass: overall,
    score,
    checks,
    tool_calls: toolCalls.map((t) => shortName(t.name)),
    notes: spec.notes || [], // non-gating, informational
    flaky: false,
    evidence: buildEvidence(captured, checks),
    response_text: text, // the model's final response, for the report + --debug
  };
}

function buildEvidence(captured, checks) {
  const failed = checks.find((c) => !c.pass);
  if (failed) return `FAIL ${failed.id}: ${failed.notes}`;
  const tools = (captured.toolCalls || []).map((t) => t.name).join(", ");
  return `tools: ${tools || "(none)"}`;
}
