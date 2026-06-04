// report.mjs — aggregate verdicts into a markdown report + console summary.

export function summarize(verdicts) {
  const total = verdicts.length;
  const passed = verdicts.filter((v) => v.overall_pass).length;
  const bySkill = new Map();
  for (const v of verdicts) {
    if (!bySkill.has(v.skill)) bySkill.set(v.skill, []);
    bySkill.get(v.skill).push(v);
  }
  return { total, passed, failed: total - passed, bySkill };
}

export function consoleSummary(verdicts) {
  const { total, passed, failed, bySkill } = summarize(verdicts);
  const lines = [];
  for (const [skill, vs] of bySkill) {
    const p = vs.filter((v) => v.overall_pass).length;
    lines.push(`  ${p === vs.length ? "✓" : "✗"} ${skill}: ${p}/${vs.length}`);
    for (const v of vs.filter((v) => !v.overall_pass)) {
      lines.push(`      ✗ ${v.scenario_id} [${v.invocation_type}] routing=${v.routing} usage=${v.usage} — ${v.evidence}${v.flaky ? " (flaky)" : ""}`);
    }
  }
  lines.push("");
  lines.push(`  TOTAL: ${passed}/${total} passed, ${failed} failed`);
  return lines.join("\n");
}

export function markdownReport(verdicts, meta = {}) {
  const { total, passed, failed, bySkill } = summarize(verdicts);
  const out = [];
  out.push(`# Skill-Tester Behavioural Report`);
  out.push("");
  out.push(`- Date: ${new Date().toISOString()}`);
  out.push(`- Skills: ${[...bySkill.keys()].join(", ") || "(none)"}`);
  if (meta.repeat) out.push(`- Repeat: ${meta.repeat}× per case`);
  if (meta.note) out.push(`- Note: ${meta.note}`);
  out.push("");
  out.push(`## Summary`);
  out.push("");
  out.push(`- Total cases: ${total}`);
  out.push(`- Passed: ${passed}`);
  out.push(`- Failed: ${failed}`);
  out.push("");

  out.push(`## Behavioural`);
  out.push("");
  out.push(`| Scenario | Type | Routing | Usage | Overall | Score | Evidence |`);
  out.push(`|----------|------|---------|-------|---------|-------|----------|`);
  for (const [, vs] of bySkill) {
    for (const v of vs) {
      const ev = (v.evidence || "").replace(/\|/g, "\\|").slice(0, 90);
      out.push(
        `| ${v.scenario_id} | ${v.invocation_type} | ${v.routing} | ${v.usage} | ${v.overall_pass ? "PASS" : "FAIL"}${v.flaky ? " (flaky)" : ""} | ${v.score} | ${ev} |`
      );
    }
  }
  out.push("");

  // Per-failure check detail
  const failures = verdicts.filter((v) => !v.overall_pass);
  if (failures.length) {
    out.push(`## Failure detail`);
    out.push("");
    for (const v of failures) {
      out.push(`### ${v.scenario_id} — ${v.invocation_type}`);
      out.push(`- selected_skill: \`${v.selected_skill}\` | expected: \`${v.expected_skill}\``);
      out.push(`- tool_calls: ${v.tool_calls.join(", ") || "(none)"}`);
      for (const c of v.checks.filter((c) => !c.pass)) {
        out.push(`- ✗ \`${c.id}\` — ${c.notes}`);
      }
      out.push("");
    }
  }

  // Model responses — full final answer per case, collapsed to keep the report scannable.
  const withResponses = verdicts.filter((v) => v.response_text);
  if (withResponses.length) {
    out.push(`## Model responses`);
    out.push("");
    for (const [, vs] of bySkill) {
      for (const v of vs) {
        if (!v.response_text) continue;
        out.push(`<details><summary>${v.scenario_id} [${v.invocation_type}] — ${v.overall_pass ? "PASS" : "FAIL"}</summary>`);
        out.push("");
        out.push("```");
        out.push(v.response_text.replace(/```/g, "ʼʼʼ"));
        out.push("```");
        out.push("");
        out.push(`</details>`);
        out.push("");
      }
    }
  }

  // Non-gating prose notes (informational only)
  const withNotes = verdicts.filter((v) => v.notes && v.notes.length);
  if (withNotes.length) {
    out.push(`## Non-gating prose notes (not scored)`);
    out.push("");
    out.push(`These soft/methodology assertions are recorded from the catalog but do not affect pass/fail.`);
    out.push("");
    for (const v of withNotes) {
      out.push(`- **${v.scenario_id}**`);
      for (const n of v.notes) out.push(`  - ${n}`);
    }
    out.push("");
  }

  return out.join("\n");
}
