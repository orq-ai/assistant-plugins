// functional.mjs — deterministic functional track: exercise each skill's real
// MCP operations live, verify results, detect drift, then clean up.
//
// Mirrors the behavioural grader's verdict shape so reports stay diffable.
// Safety: only deletes resources this run created (tracked ids); never touches
// pre-existing workspace resources. See tests/setup.md.

import { matchValue } from "./match.mjs";
import { pathGet } from "./path-get.mjs";

const SEED = {
  echoAgentKey: "orq-skills-test-echo",
  datasetName: "orq-skills-test-dataset",
  lengthEvalKey: "orq-skills-test-eval-length",
};

// ── variable interpolation ───────────────────────────────────────────────
/** Replace {{var}} tokens in any string within a (possibly nested) value. */
function interpolate(value, vars) {
  if (typeof value === "string") {
    return value.replace(/\{\{\s*([\w.]+)\s*\}\}/g, (_, name) => {
      const v = vars[name];
      return v === undefined || v === null ? "" : String(v);
    });
  }
  if (Array.isArray(value)) return value.map((v) => interpolate(v, vars));
  if (value && typeof value === "object") {
    const out = {};
    for (const [k, v] of Object.entries(value)) out[k] = interpolate(v, vars);
    return out;
  }
  return value;
}

// ── preflight / seeding ────────────────────────────────────────────────────
/**
 * Discover an isolated test path and ensure the three shared seed resources
 * exist. Resources already present are reused (never deleted); only resources
 * created here are tracked for teardown.
 */
export async function preflight(client) {
  const ctx = { testPath: null, vars: {}, tracked: [], seedNotes: [] };

  // 1. discover a project → isolated path {project}/orq-skills-tests
  const proj = await client.callTool("search_entities", { type: "project", limit: 1 });
  const first = (proj.json?.data || proj.json || [])[0];
  const projName = first?.path || first?.display_name || first?.key || first?.name;
  if (!projName) throw new Error(`could not discover a project (search_entities type=project returned: ${proj.text?.slice(0, 200)})`);
  ctx.testPath = `${projName}/orq-skills-tests`;
  ctx.vars.testPath = ctx.testPath;

  // 2. seed echo agent
  const agent = await ensure(client, {
    label: "echo agent",
    get: () => client.callTool("get_agent", { key: SEED.echoAgentKey }),
    create: () =>
      client.callTool("create_agent", {
        key: SEED.echoAgentKey,
        description: "skill-tester seed: echoes the user's message",
        instructions: "Echo back the user's message verbatim.",
        path: ctx.testPath,
        model: "openai/gpt-4.1-mini",
      }),
    idPath: "id",
  });
  ctx.vars.echoAgentKey = SEED.echoAgentKey;
  ctx.vars.echoAgentId = agent.id;
  if (agent.createdThisRun) ctx.tracked.push({ kind: "agent", id: agent.id, key: SEED.echoAgentKey });
  ctx.seedNotes.push(`echo agent: ${agent.createdThisRun ? "created" : "reused"}`);

  // 3. seed dataset (+5 datapoints)
  const ds = await ensure(client, {
    label: "dataset",
    get: async () => {
      const r = await client.callTool("search_entities", { type: "dataset", query: SEED.datasetName });
      const hit = (r.json?.data || r.json || []).find((d) => (d.display_name || d.key) === SEED.datasetName);
      return { json: hit ? { id: hit._id || hit.id } : undefined, isError: !hit };
    },
    create: () => client.callTool("create_dataset", { display_name: SEED.datasetName, path: ctx.testPath }),
    idPath: "id",
  });
  ctx.vars.datasetId = ds.id;
  if (ds.createdThisRun) {
    ctx.tracked.push({ kind: "dataset", id: ds.id, key: SEED.datasetName });
    await client.callTool("create_datapoints", {
      dataset_id: ds.id,
      datapoints: Array.from({ length: 5 }, (_, i) => ({
        inputs: { message: `seed message ${i + 1}` },
        expected_output: `seed message ${i + 1}`,
      })),
    });
  }
  ctx.seedNotes.push(`dataset: ${ds.createdThisRun ? "created" : "reused"}`);

  // 4. seed length eval
  const evalRes = await ensure(client, {
    label: "length eval",
    get: () => client.callTool("get_python_eval", { key: SEED.lengthEvalKey }),
    create: () =>
      client.callTool("create_python_eval", {
        key: SEED.lengthEvalKey,
        path: ctx.testPath,
        output_type: "boolean",
        code: "def evaluate(log):\n    return len(log['output']) > 0",
      }),
    idPath: "id",
  });
  ctx.vars.lengthEvalId = evalRes.id;
  if (evalRes.createdThisRun) ctx.tracked.push({ kind: "evaluator", id: evalRes.id, key: SEED.lengthEvalKey });
  ctx.seedNotes.push(`length eval: ${evalRes.createdThisRun ? "created" : "reused"}`);

  return ctx;
}

/** Reuse a resource if a read resolves it, else create it and flag createdThisRun. */
async function ensure(client, { get, create, idPath }) {
  let existing;
  try {
    existing = await get();
  } catch {
    existing = { isError: true };
  }
  if (existing && !existing.isError && existing.json) {
    return { id: pathGet(existing.json, idPath) ?? existing.json.id, createdThisRun: false };
  }
  const created = await create();
  if (created.isError) throw new Error(`seed failed: ${created.text?.slice(0, 200)}`);
  return { id: pathGet(created.json, idPath) ?? created.json?.id, createdThisRun: true };
}

// ── case execution ──────────────────────────────────────────────────────────
/**
 * Run one functional case. Returns a verdict:
 *   { scenario_id, skill, status: PASS|FAIL|DRIFT|SKIP, checks[], steps[], evidence }
 */
export async function runFunctionalCase(spec, skill, ctx, client, { onDebug } = {}) {
  const scenario_id = `${skill}/${spec.id}`;
  if (spec.skip) {
    return { scenario_id, skill, title: spec.title || "", status: "SKIP", checks: [], steps: [], evidence: spec.skip };
  }

  const toolNames = await client.listToolNames();
  const vars = { ...ctx.vars };
  const checks = [];
  const steps = [];
  let hardFail = false;
  let driftFail = false;
  const add = (id, pass, notes) => checks.push({ id, pass, notes: notes ?? "" });

  for (const [si, step] of (spec.steps || []).entries()) {
    const args = interpolate(step.args || {}, vars);

    // drift: a documented operation the server no longer exposes
    if (!toolNames.includes(step.call)) {
      add(`tool:${step.call}`, false, "not exposed by server (drift)");
      driftFail = true;
      steps.push({ call: step.call, args, missing: true });
      if (onDebug) onDebug({ scenario_id, call: step.call, args, response: "(tool not exposed)" });
      continue;
    }

    let res;
    try {
      res = await client.callTool(step.call, args);
    } catch (e) {
      res = { isError: true, text: String(e?.message || e) };
    }
    const responseStr = res.json !== undefined ? JSON.stringify(res.json) : res.text || "";
    steps.push({ call: step.call, args, isError: res.isError, response: responseStr });
    if (onDebug) onDebug({ scenario_id, call: step.call, args, response: responseStr });

    if (res.isError) {
      add(`call:${step.call}#${si}`, false, `error: ${(res.text || "").slice(0, 160)}`);
      hardFail = true;
      continue;
    }

    // capture saves
    for (const [name, p] of Object.entries(step.save || {})) {
      vars[name] = pathGet(res.json, p);
    }

    // verify matchers
    for (const [vi, v] of (step.verify || []).entries()) {
      const actual = pathGet(res.json !== undefined ? res.json : res.text, v.path);
      const ok = matchValue({ op: v.op, value: v.value }, actual);
      add(`verify:${step.call}#${si}.${vi}`, ok, ok ? "matched" : `path=${v.path} actual=${JSON.stringify(actual)?.slice(0, 80)}`);
      if (!ok) hardFail = true;
    }
  }

  // explicit drift expectations against the live tool list
  for (const d of spec.drift || []) {
    const present = toolNames.includes(d.tool);
    const want = (d.expect || "present") === "present";
    const ok = present === want;
    add(`drift:${d.tool}=${d.expect || "present"}`, ok, present ? "present" : "absent");
    if (!ok) driftFail = true;
  }

  // per-case cleanup (best-effort, runs regardless of pass/fail)
  for (const c of spec.cleanup || []) {
    try {
      await client.callTool(c.call, interpolate(c.args || {}, vars));
    } catch {
      /* teardown is best-effort; report-level teardown also sweeps tracked ids */
    }
  }

  const status = hardFail ? "FAIL" : driftFail ? "DRIFT" : "PASS";
  return { scenario_id, skill, title: spec.title || "", status, checks, steps, evidence: buildEvidence(checks, status) };
}

function buildEvidence(checks, status) {
  if (status === "PASS") return `${checks.length} checks passed`;
  const f = checks.find((c) => !c.pass);
  return f ? `${f.id}: ${f.notes}` : status;
}

// ── teardown ──────────────────────────────────────────────────────────────
/** Delete only the seed resources this run created. Returns cleanup records. */
export async function teardown(ctx, client) {
  const results = [];
  // reverse order: evaluator, dataset, agent
  for (const r of [...ctx.tracked].reverse()) {
    try {
      if (r.kind === "dataset") {
        await client.callTool("delete_dataset", { dataset_id: r.id });
      } else {
        await client.callTool("delete_entity", { type: r.kind, id: r.id });
      }
      results.push({ ...r, deleted: true });
    } catch (e) {
      results.push({ ...r, deleted: false, error: String(e?.message || e) });
    }
  }
  return results;
}
