#!/usr/bin/env node
// The replay queue is one directory shared by every traced session, so a batch
// queued while ingest was down outlives the session that made it. Draining it
// against whatever key the next session holds would post one workspace's
// session content to another workspace, which is the thing the per-session
// destination pin exists to prevent, reached the other way round.
//
// Each case runs in a child process, because the endpoint and the key are read
// from the environment at import time.
//
// Usage: node test-queue-destination.mjs

import assert from "node:assert";
import fs from "node:fs";
import http from "node:http";
import os from "node:os";
import path from "node:path";
import { spawn } from "node:child_process";
import { fileURLToPath } from "node:url";

const here = path.dirname(fileURLToPath(import.meta.url));
const otlpUrl = JSON.stringify(
  `file://${path.join(here, "..", "src", "otlp.js").replaceAll("\\", "/")}`,
);
const stateUrl = JSON.stringify(
  `file://${path.join(here, "..", "src", "state.js").replaceAll("\\", "/")}`,
);

let failed = 0;
async function test(name, fn) {
  try {
    await fn();
    console.log(`PASS ${name}`);
  } catch (err) {
    failed += 1;
    console.log(`FAIL ${name}: ${err?.message}`);
  }
}

function makeTempDir() {
  const dir = fs.mkdtempSync(path.join(os.tmpdir(), "orq-queue-dest-"));
  fs.mkdirSync(path.join(dir, "orq_queue"), { recursive: true });
  return dir;
}

function queuedFiles(dir) {
  return fs.readdirSync(path.join(dir, "orq_queue"));
}

// Answers 503 until `reachable` is set, and records what arrives with the key
// it arrived under, which is what tells the two workspaces apart.
function startEndpoint() {
  const delivered = [];
  const state = { reachable: false };
  const server = http.createServer((req, res) => {
    const chunks = [];
    req.on("data", (chunk) => chunks.push(chunk));
    req.on("end", () => {
      if (!state.reachable) {
        res.writeHead(503).end("down");
        return;
      }
      const body = JSON.parse(Buffer.concat(chunks).toString("utf8"));
      const names = (body.resourceSpans ?? []).flatMap((r) =>
        (r.scopeSpans ?? []).flatMap((s) => s.spans ?? []),
      );
      for (const span of names) {
        delivered.push({ name: span.name, auth: req.headers.authorization });
      }
      res.writeHead(200).end("{}");
    });
  });
  return new Promise((resolve) => {
    server.listen(0, "127.0.0.1", () =>
      resolve({ server, state, delivered, port: server.address().port }),
    );
  });
}

// Resolves with the exit code, and the cases assert it. Without that, a child
// that dies at import satisfies every "nothing was delivered and the file is
// still there" assertion in this file, so the suite would pass while the drain
// never ran at all.
function runInChild(source, env) {
  return new Promise((resolve) => {
    const child = spawn("node", ["--input-type=module", "-e", source], { env: { ...process.env, ...env } });
    let stderr = "";
    child.stderr.on("data", (chunk) => {
      stderr += chunk;
    });
    child.on("close", (code) => resolve({ code, stderr }));
  });
}

function ranCleanly(result, what) {
  assert.equal(result.code, 0, `${what} exited ${result.code}: ${result.stderr.trim()}`);
}

const span = (name) => ({
  traceId: "a".repeat(32),
  spanId: "b".repeat(16),
  name,
  kind: 1,
  startTimeUnixNano: "1700000000000000000",
  endTimeUnixNano: "1700000001000000000",
  attributes: [],
});

const queue = (name) => `
  const { sendSpans } = await import(${otlpUrl});
  await sendSpans([${JSON.stringify(span(name))}]);
`;
const drain = `
  const { drainQueue } = await import(${otlpUrl});
  await drainQueue();
`;
const pruneStale = `
  const { pruneStaleFiles } = await import(${stateUrl});
  await pruneStaleFiles();
`;

await test("a batch queued for one workspace is not drained by another", async () => {
  const dir = makeTempDir();
  const { server, state, delivered, port } = await startEndpoint();
  const endpoint = `http://127.0.0.1:${port}/v1/traces`;

  ranCleanly(
    await runInChild(queue("chat workspace A"), {
      ORQ_CLAUDE_STATE_DIR: dir,
      ORQ_API_KEY: "key-workspace-a",
      OTEL_EXPORTER_OTLP_ENDPOINT: endpoint,
    }),
    "the queueing child",
  );
  assert.equal(queuedFiles(dir).length, 1, "a failed send should be queued");

  // Same endpoint, different workspace key: the batch is not this session's to
  // deliver, so nothing may leave and the file must survive for its owner.
  state.reachable = true;
  const bDrain = await runInChild(drain, {
    ORQ_CLAUDE_STATE_DIR: dir,
    ORQ_API_KEY: "key-workspace-b",
    OTEL_EXPORTER_OTLP_ENDPOINT: endpoint,
  });
  ranCleanly(bDrain, "workspace B's drain");
  assert.deepEqual(delivered, [], "workspace B drained workspace A's session content");
  assert.equal(queuedFiles(dir).length, 1, "the batch was consumed by the wrong workspace");
  // The spans are held back, and the operator is told so on stderr: without
  // this the only trace of the skip is an ORQ_DEBUG log nobody has on.
  assert.match(
    bDrain.stderr,
    /1 queued batch\(es\) have no recorded destination or belong to a different endpoint or API key, so this session cannot deliver them; a later session start removes them once they are an hour old/,
    "workspace B's drain said nothing about the batch it could not deliver",
  );
  const bMarkers = fs.readdirSync(dir).filter((name) => name.startsWith("orq_undeliverable_warn_"));
  assert.equal(bMarkers.length, 1, "workspace B should create its own warning marker");
  const bMarker = bMarkers[0];

  // Said once an hour per destination, not once per hook. The rate limit lives
  // in the state directory because each hook is a new process, so a repeat
  // drain from the same workspace must stay quiet about the same files.
  const bAgain = await runInChild(drain, {
    ORQ_CLAUDE_STATE_DIR: dir,
    ORQ_API_KEY: "key-workspace-b",
    OTEL_EXPORTER_OTLP_ENDPOINT: endpoint,
  });
  ranCleanly(bAgain, "workspace B's second drain");
  assert.doesNotMatch(
    bAgain.stderr,
    /cannot deliver them/,
    "the warning repeated on the next hook in the same hour",
  );
  assert.equal(queuedFiles(dir).length, 1, "the second drain consumed the batch");

  // Another workspace needs its own warning even though B just saw this queue.
  const cDrain = await runInChild(drain, {
    ORQ_CLAUDE_STATE_DIR: dir,
    ORQ_API_KEY: "key-workspace-c",
    OTEL_EXPORTER_OTLP_ENDPOINT: endpoint,
  });
  ranCleanly(cDrain, "workspace C's drain");
  assert.match(cDrain.stderr, /cannot deliver them/, "workspace C's warning was suppressed by B's marker");

  // A different endpoint with the same API key is a distinct destination too.
  const otherEndpoint = await startEndpoint();
  const dDrain = await runInChild(drain, {
    ORQ_CLAUDE_STATE_DIR: dir,
    ORQ_API_KEY: "key-workspace-b",
    OTEL_EXPORTER_OTLP_ENDPOINT: `http://127.0.0.1:${otherEndpoint.port}/v1/traces`,
  });
  ranCleanly(dDrain, "workspace B's other-endpoint drain");
  assert.match(dDrain.stderr, /cannot deliver them/, "a fresh marker for the first endpoint suppressed this warning");

  // The warning resumes after the hour passes for B's destination alone.
  const markers = fs.readdirSync(dir).filter((name) => name.startsWith("orq_undeliverable_warn_"));
  assert.equal(markers.length, 3, "each endpoint and key pair should have its own warning marker");
  assert.ok(markers.includes(bMarker), "workspace B's marker was not found");
  const old = new Date(Date.now() - 61 * 60 * 1000);
  fs.utimesSync(path.join(dir, bMarker), old, old);
  const cAgain = await runInChild(drain, {
    ORQ_CLAUDE_STATE_DIR: dir,
    ORQ_API_KEY: "key-workspace-c",
    OTEL_EXPORTER_OTLP_ENDPOINT: endpoint,
  });
  ranCleanly(cAgain, "workspace C's second drain");
  assert.doesNotMatch(cAgain.stderr, /cannot deliver them/, "aging B's marker made C's fresh warning repeat");
  const bAfterHour = await runInChild(drain, {
    ORQ_CLAUDE_STATE_DIR: dir,
    ORQ_API_KEY: "key-workspace-b",
    OTEL_EXPORTER_OTLP_ENDPOINT: endpoint,
  });
  ranCleanly(bAfterHour, "workspace B's drain after an hour");
  assert.match(bAfterHour.stderr, /cannot deliver them/, "workspace B was not warned after an hour");
  assert.equal(queuedFiles(dir).length, 1, "the warning test consumed the batch");

  otherEndpoint.server.close();

  // Its own workspace comes back and collects it.
  ranCleanly(
    await runInChild(drain, {
      ORQ_CLAUDE_STATE_DIR: dir,
      ORQ_API_KEY: "key-workspace-a",
      OTEL_EXPORTER_OTLP_ENDPOINT: endpoint,
    }),
    "workspace A's drain",
  );
  assert.deepEqual(
    delivered.map((d) => d.name),
    ["chat workspace A"],
    "the owning workspace never got its queued span",
  );
  assert.equal(delivered[0].auth, "Bearer key-workspace-a", "delivered under the wrong key");
  assert.equal(queuedFiles(dir).length, 0, "a delivered file should be removed");

  server.close();
  fs.rmSync(dir, { recursive: true, force: true });
});

await test("stale warning markers, including the legacy marker, are pruned", async () => {
  const dir = makeTempDir();
  const old = new Date(Date.now() - 61 * 60 * 1000);
  const staleMarkers = ["orq_undeliverable_warn", "orq_undeliverable_warn_0123456789abcdef"];
  for (const name of staleMarkers) {
    const markerPath = path.join(dir, name);
    fs.writeFileSync(markerPath, "old\n");
    fs.utimesSync(markerPath, old, old);
  }
  const freshPath = path.join(dir, "orq_undeliverable_warn_fedcba9876543210");
  fs.writeFileSync(freshPath, "fresh\n");
  const unrelatedPath = path.join(dir, "orq_undeliverable_warn_notes");
  fs.writeFileSync(unrelatedPath, "keep\n");

  const result = await runInChild(pruneStale, { ORQ_CLAUDE_STATE_DIR: dir });
  ranCleanly(result, "stale-marker pruning");
  assert.deepEqual(
    fs.readdirSync(dir).filter((name) => name.startsWith("orq_undeliverable_warn")),
    ["orq_undeliverable_warn_fedcba9876543210", "orq_undeliverable_warn_notes"],
    "pruning should remove old markers while retaining fresh markers and unrelated prefix matches",
  );
  fs.rmSync(dir, { recursive: true, force: true });
});

await test("a batch queued for one endpoint is not drained against another", async () => {
  const dir = makeTempDir();
  const a = await startEndpoint();
  const b = await startEndpoint();
  const key = "same-key-two-environments";

  ranCleanly(
    await runInChild(queue("chat staging"), {
      ORQ_CLAUDE_STATE_DIR: dir,
      ORQ_API_KEY: key,
      OTEL_EXPORTER_OTLP_ENDPOINT: `http://127.0.0.1:${a.port}/v1/traces`,
    }),
    "the queueing child",
  );
  assert.equal(queuedFiles(dir).length, 1, "a failed send should be queued");

  b.state.reachable = true;
  ranCleanly(
    await runInChild(drain, {
      ORQ_CLAUDE_STATE_DIR: dir,
      ORQ_API_KEY: key,
      OTEL_EXPORTER_OTLP_ENDPOINT: `http://127.0.0.1:${b.port}/v1/traces`,
    }),
    "the other environment's drain",
  );
  assert.deepEqual(b.delivered, [], "a staging batch was posted to the other environment");
  assert.equal(queuedFiles(dir).length, 1, "the batch was consumed by the wrong endpoint");

  // Positive control: the same file, same key, against the endpoint it was
  // queued for. Without this the case would also pass if the drain never
  // delivered anything to anyone.
  a.state.reachable = true;
  ranCleanly(
    await runInChild(drain, {
      ORQ_CLAUDE_STATE_DIR: dir,
      ORQ_API_KEY: key,
      OTEL_EXPORTER_OTLP_ENDPOINT: `http://127.0.0.1:${a.port}/v1/traces`,
    }),
    "the owning environment's drain",
  );
  assert.deepEqual(
    a.delivered.map((d) => d.name),
    ["chat staging"],
    "the owning endpoint never got its queued span",
  );
  assert.equal(queuedFiles(dir).length, 0, "a delivered file should be removed");

  a.server.close();
  b.server.close();
  fs.rmSync(dir, { recursive: true, force: true });
});

await test("a file written before destinations were recorded is left alone", async () => {
  const dir = makeTempDir();
  const { server, state, delivered, port } = await startEndpoint();
  state.reachable = true;

  // The shape an older build wrote: the bare OTLP envelope, no destination.
  fs.writeFileSync(
    path.join(dir, "orq_queue", "1700000000000-legacy.json"),
    `${JSON.stringify({
      resourceSpans: [{ resource: { attributes: [] }, scopeSpans: [{ scope: {}, spans: [span("chat legacy")] }] }],
    })}\n`,
  );

  // Positive control in the same drain: a bound file for this very destination.
  // It must be delivered while the legacy one is left, so the case cannot pass
  // by the drain doing nothing at all.
  const endpoint = `http://127.0.0.1:${port}/v1/traces`;
  ranCleanly(
    await runInChild(
      `
  const { writeQueuedPayload } = await import(${stateUrl});
  const { currentDestination } = await import(${otlpUrl});
  const path = await import("node:path");
  await writeQueuedPayload(
    path.join(process.env.ORQ_CLAUDE_STATE_DIR, "orq_queue", "1700000001000-bound.json"),
    { orqDestination: currentDestination(), payload: { resourceSpans: [{ resource: { attributes: [] }, scopeSpans: [{ scope: {}, spans: [${JSON.stringify(span("chat bound"))}] }] }] } },
  );
`,
      { ORQ_CLAUDE_STATE_DIR: dir, ORQ_API_KEY: "any-key", OTEL_EXPORTER_OTLP_ENDPOINT: endpoint },
    ),
    "the child that queues a bound file",
  );
  assert.equal(queuedFiles(dir).length, 2, "the bound control file was not written");

  const legacyDrain = await runInChild(drain, {
    ORQ_CLAUDE_STATE_DIR: dir,
    ORQ_API_KEY: "any-key",
    OTEL_EXPORTER_OTLP_ENDPOINT: endpoint,
  });
  ranCleanly(legacyDrain, "the drain");
  assert.match(legacyDrain.stderr, /have no recorded destination/, "the legacy warning described the wrong reason");
  assert.deepEqual(
    delivered.map((d) => d.name),
    ["chat bound"],
    "the bound file was not delivered, so this case proves nothing about the legacy one",
  );
  assert.deepEqual(queuedFiles(dir), ["1700000000000-legacy.json"], "the legacy file should be the one left behind");

  server.close();
  fs.rmSync(dir, { recursive: true, force: true });
});

const total = 4;
console.log("");
console.log(failed === 0 ? `ALL PASS (${total} cases)` : `${failed} FAILED (${total} cases)`);
process.exit(failed === 0 ? 0 : 1);
