// functional-report.mjs — aggregate functional verdicts into a markdown report
// + console summary. Mirrors report.mjs (behavioural) so the two read alike.

export function summarizeFunctional(verdicts) {
  const count = (s) => verdicts.filter((v) => v.status === s).length;
  const bySkill = new Map();
  for (const v of verdicts) {
    if (!bySkill.has(v.skill)) bySkill.set(v.skill, []);
    bySkill.get(v.skill).push(v);
  }
  return {
    total: verdicts.length,
    pass: count("PASS"),
    fail: count("FAIL"),
    drift: count("DRIFT"),
    skip: count("SKIP"),
    bySkill,
  };
}

export function consoleSummaryFunctional(verdicts) {
  const { total, pass, fail, drift, skip, bySkill } = summarizeFunctional(verdicts);
  const mark = { PASS: "✓", FAIL: "✗", DRIFT: "≠", SKIP: "–" };
  const lines = [];
  for (const [skill, vs] of bySkill) {
    const p = vs.filter((v) => v.status === "PASS").length;
    lines.push(`  ${skill}: ${p}/${vs.length} pass`);
    for (const v of vs.filter((v) => v.status !== "PASS")) {
      lines.push(`      ${mark[v.status]} ${v.scenario_id} [${v.status}] — ${v.evidence}`);
    }
  }
  lines.push("");
  lines.push(`  TOTAL: ${pass} pass, ${fail} fail, ${drift} drift, ${skip} skip (of ${total})`);
  return lines.join("\n");
}

export function markdownReportFunctional(verdicts, cleanup = [], meta = {}) {
  const { total, pass, fail, drift, skip, bySkill } = summarizeFunctional(verdicts);
  const out = [];
  out.push(`# Skill-Tester Functional Report`);
  out.push("");
  out.push(`- Date: ${new Date().toISOString()}`);
  out.push(`- Skills: ${[...bySkill.keys()].join(", ") || "(none)"}`);
  if (meta.testPath) out.push(`- Test path: \`${meta.testPath}\``);
  if (meta.seedNotes?.length) out.push(`- Seed: ${meta.seedNotes.join("; ")}`);
  out.push("");
  out.push(`## Summary`);
  out.push("");
  out.push(`- Total cases: ${total}`);
  out.push(`- Passed: ${pass} | Failed: ${fail} | Drift: ${drift} | Skipped: ${skip}`);
  out.push("");

  out.push(`## Functional`);
  out.push("");
  out.push(`| Scenario | Status | Checks | Evidence |`);
  out.push(`|----------|--------|--------|----------|`);
  for (const [, vs] of bySkill) {
    for (const v of vs) {
      const passed = v.checks.filter((c) => c.pass).length;
      const ev = (v.evidence || "").replace(/\|/g, "\\|").slice(0, 90);
      out.push(`| ${v.scenario_id} | ${v.status} | ${passed}/${v.checks.length} | ${ev} |`);
    }
  }
  out.push("");

  // Failure / drift detail
  const problems = verdicts.filter((v) => v.status === "FAIL" || v.status === "DRIFT");
  if (problems.length) {
    out.push(`## Failure / drift detail`);
    out.push("");
    for (const v of problems) {
      out.push(`### ${v.scenario_id} — ${v.status}`);
      for (const c of v.checks.filter((c) => !c.pass)) out.push(`- ✗ \`${c.id}\` — ${c.notes}`);
      out.push("");
    }
  }

  // Tool calls & responses — the functional analogue of "Model responses".
  const withSteps = verdicts.filter((v) => v.steps && v.steps.length);
  if (withSteps.length) {
    out.push(`## Tool calls & responses`);
    out.push("");
    for (const [, vs] of bySkill) {
      for (const v of vs) {
        if (!v.steps?.length) continue;
        out.push(`<details><summary>${v.scenario_id} — ${v.status}</summary>`);
        out.push("");
        for (const s of v.steps) {
          out.push(`**${s.call}**${s.missing ? " — _not exposed (drift)_" : ""}`);
          out.push("");
          out.push("```json");
          out.push(`// request\n${JSON.stringify(s.args, null, 2)}`);
          if (!s.missing) out.push(`// response\n${truncate(s.response, 2000).replace(/```/g, "ʼʼʼ")}`);
          out.push("```");
          out.push("");
        }
        out.push(`</details>`);
        out.push("");
      }
    }
  }

  // Cleanup status
  out.push(`## Cleanup`);
  out.push("");
  if (!cleanup.length) {
    out.push(`- No resources were created this run (all seed resources reused).`);
  } else {
    for (const r of cleanup) {
      out.push(`- ${r.deleted ? "✓ deleted" : "⚠ MANUAL CLEANUP"} ${r.kind} \`${r.key}\` (${r.id})${r.error ? ` — ${r.error}` : ""}`);
    }
  }
  out.push("");

  return out.join("\n");
}

function truncate(s, n) {
  s = String(s ?? "");
  return s.length > n ? s.slice(0, n) + `… (${s.length} chars)` : s;
}
