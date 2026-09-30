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

function runInChild(source, env) {
  return new Promise((resolve) => {
    const child = spawn("node", ["--input-type=module", "-e", source], { env: { ...process.env, ...env } });
    child.stderr.on("data", () => {});
    child.on("close", resolve);
  });
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

await test("a batch queued for one workspace is not drained by another", async () => {
  const dir = makeTempDir();
  const { server, state, delivered, port } = await startEndpoint();
  const endpoint = `http://127.0.0.1:${port}/v1/traces`;

  await runInChild(queue("chat workspace A"), {
    ORQ_CLAUDE_STATE_DIR: dir,
    ORQ_API_KEY: "key-workspace-a",
    OTEL_EXPORTER_OTLP_ENDPOINT: endpoint,
  });
  assert.equal(queuedFiles(dir).length, 1, "a failed send should be queued");

  // Same endpoint, different workspace key: the batch is not this session's to
  // deliver, so nothing may leave and the file must survive for its owner.
  state.reachable = true;
  await runInChild(drain, {
    ORQ_CLAUDE_STATE_DIR: dir,
    ORQ_API_KEY: "key-workspace-b",
    OTEL_EXPORTER_OTLP_ENDPOINT: endpoint,
  });
  assert.deepEqual(delivered, [], "workspace B drained workspace A's session content");
  assert.equal(queuedFiles(dir).length, 1, "the batch was consumed by the wrong workspace");

  // Its own workspace comes back and collects it.
  await runInChild(drain, {
    ORQ_CLAUDE_STATE_DIR: dir,
    ORQ_API_KEY: "key-workspace-a",
    OTEL_EXPORTER_OTLP_ENDPOINT: endpoint,
  });
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

await test("a batch queued for one endpoint is not drained against another", async () => {
  const dir = makeTempDir();
  const a = await startEndpoint();
  const b = await startEndpoint();
  const key = "same-key-two-environments";

  await runInChild(queue("chat staging"), {
    ORQ_CLAUDE_STATE_DIR: dir,
    ORQ_API_KEY: key,
    OTEL_EXPORTER_OTLP_ENDPOINT: `http://127.0.0.1:${a.port}/v1/traces`,
  });
  assert.equal(queuedFiles(dir).length, 1, "a failed send should be queued");

  b.state.reachable = true;
  await runInChild(drain, {
    ORQ_CLAUDE_STATE_DIR: dir,
    ORQ_API_KEY: key,
    OTEL_EXPORTER_OTLP_ENDPOINT: `http://127.0.0.1:${b.port}/v1/traces`,
  });
  assert.deepEqual(b.delivered, [], "a staging batch was posted to the other environment");
  assert.equal(queuedFiles(dir).length, 1, "the batch was consumed by the wrong endpoint");

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

  await runInChild(drain, {
    ORQ_CLAUDE_STATE_DIR: dir,
    ORQ_API_KEY: "any-key",
    OTEL_EXPORTER_OTLP_ENDPOINT: `http://127.0.0.1:${port}/v1/traces`,
  });
  assert.deepEqual(delivered, [], "a batch of unknown provenance was posted anyway");
  assert.equal(queuedFiles(dir).length, 1, "the file should be left for the queue cap to evict");

  server.close();
  fs.rmSync(dir, { recursive: true, force: true });
});

const total = 3;
console.log("");
console.log(failed === 0 ? `ALL PASS (${total} cases)` : `${failed} FAILED (${total} cases)`);
process.exit(failed === 0 ? 0 : 1);
