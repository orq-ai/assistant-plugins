import { attr, compact, debugLog, nowUnixNano } from "./common.js";
import { getApiKey, getBaseUrl, getOtlpEndpoint } from "./config.js";
import {
  deleteQueuedFile,
  enqueuePayload,
  listQueuedFiles,
  readQueuedPayload,
  writeQueuedPayload,
} from "./state.js";

const SCOPE_NAME = "orq-claude-code";
const SDK_VERSION = "0.1.0";

// Hard ceiling for a single request. orq ingest accepts spans well past this;
// the limit exists so a runaway payload is caught here rather than at the
// endpoint.
const MAX_PAYLOAD_BYTES = 12 * 1024 * 1024;

// Target size per request, well under the hard limit so a batch landing on the
// boundary still fits once wrapped in the resourceSpans envelope.
const BATCH_TARGET_BYTES = 4 * 1024 * 1024;

// Well inside the 30 s the hooks are given, with room for several batches and
// a queue drain in the same invocation.
const SEND_TIMEOUT_MS = 8000;

// A payload the endpoint will never accept, however often it is retried: a 4xx
// rejection, or a single span too large for any request. Kept distinct from
// transient faults so one bad payload cannot block the queue behind it.
class PermanentSendError extends Error {}

function spansOf(payload) {
  // A bare array is tolerated so files written by builds that queued spans
  // unwrapped still drain instead of being read as empty and deleted.
  if (Array.isArray(payload)) {
    return payload;
  }
  return (payload?.resourceSpans ?? []).flatMap((resourceSpan) =>
    (resourceSpan.scopeSpans ?? []).flatMap((scopeSpan) => scopeSpan.spans ?? []),
  );
}

// A configured endpoint may name only the host. Posting to a bare host 404s,
// and a 404 is permanent, so those spans would be dropped rather than retried.
function withTracesPath(url) {
  return url.endsWith("/v1/traces") ? url : `${url.replace(/\/$/, "")}/v1/traces`;
}

export function getEndpoint() {
  const explicit = process.env.OTEL_EXPORTER_OTLP_ENDPOINT;
  if (explicit) {
    return withTracesPath(explicit);
  }

  // A profile may name its ingest URL outright. Production does not: the
  // subdomain derivation below is right for my.orq.ai. Staging and other
  // non-production hosts set otlp_endpoint because that derivation produces a
  // host which does not serve OTLP.
  const fromProfile = getOtlpEndpoint();
  if (fromProfile) {
    return withTracesPath(fromProfile);
  }

  // Orq convention: OTLP ingest lives on api.orq.ai, derived from my.orq.ai
  // by swapping the subdomain. The /v2/otel/ prefix routes through the API gateway.
  const baseUrl = getBaseUrl();
  try {
    const url = new URL(baseUrl);
    const host = url.host.replace(/^my\./, "api.");
    return `${url.protocol}//${host}/v2/otel/v1/traces`;
  } catch (err) {
    process.stderr.write(
      `[orq-trace] WARN: invalid base URL "${baseUrl}", using default endpoint: ${err?.message}\n`,
    );
    return "https://api.orq.ai/v2/otel/v1/traces";
  }
}

function getHeaders() {
  const headers = {
    "Content-Type": "application/json",
  };

  const apiKey = getApiKey();
  if (apiKey) {
    headers.Authorization = `Bearer ${apiKey}`;
  }

  if (process.env.OTEL_EXPORTER_OTLP_HEADERS) {
    for (const item of process.env.OTEL_EXPORTER_OTLP_HEADERS.split(",")) {
      const [rawKey, ...valueParts] = item.split("=");
      const key = rawKey?.trim();
      const value = valueParts.join("=").trim();
      if (key && value) {
        headers[key] = value;
      }
    }
  }

  return headers;
}

function buildBatchPayload(spans) {
  return {
    resourceSpans: [
      {
        resource: {
          attributes: compact([
            attr("service.name", "claude-code"),
            attr("telemetry.sdk.name", SCOPE_NAME),
            attr("telemetry.sdk.version", SDK_VERSION),
            attr("telemetry.sdk.language", "nodejs"),
          ]),
        },
        scopeSpans: [
          {
            scope: {
              name: SCOPE_NAME,
              version: SDK_VERSION,
            },
            spans,
          },
        ],
      },
    ],
  };
}

async function postPayload(payload) {
  const endpoint = getEndpoint();
  const spans = spansOf(payload);
  await debugLog(`[otlp] PRE-POST ${endpoint} spans=${spans.length} [${spans.map((s) => s.name).join(", ")}]\n`);

  const body = JSON.stringify(payload);
  const size = Buffer.byteLength(body, "utf8");
  if (size > MAX_PAYLOAD_BYTES) {
    throw new PermanentSendError(
      `payload is ${(size / 1024 / 1024).toFixed(1)} MB, over the ${MAX_PAYLOAD_BYTES / 1024 / 1024} MB limit`,
    );
  }

  const response = await fetch(endpoint, {
    method: "POST",
    headers: getHeaders(),
    body,
    // Without this, an endpoint that accepts the connection and never answers
    // holds the request for undici's 300 s default while Claude Code kills the
    // hook at 30 s, taking the spans with it, since nothing gets queued.
    signal: AbortSignal.timeout(SEND_TIMEOUT_MS),
  });

  const responseBody = await response.text().catch(() => "");
  if (!response.ok) {
    // 4xx will never be accepted however often it is retried. Only 5xx, 429 and
    // network faults are worth keeping in the queue.
    const Err = response.status >= 400 && response.status < 500 && response.status !== 429
      ? PermanentSendError
      : Error;
    throw new Err(`OTLP send failed (${response.status}): ${responseBody}`);
  }

  await debugLog(`[otlp] POST ${endpoint} ${response.status}: ${responseBody}\n`);
}

// Split a span list into batches that each fit inside one request. This is the
// only bound on outgoing size: per-span caps do not help when a long turn
// produces hundreds of individually small spans.
//
// A span larger than a whole request ends up alone in its batch, which is the
// one case that cannot be made to fit.
function chunkSpans(spans) {
  const batches = [];
  let current = [];
  let currentBytes = 0;

  for (const span of spans) {
    const size = Buffer.byteLength(JSON.stringify(span), "utf8");
    if (current.length > 0 && currentBytes + size > BATCH_TARGET_BYTES) {
      batches.push(current);
      current = [];
      currentBytes = 0;
    }
    current.push(span);
    currentBytes += size;
  }
  if (current.length > 0) batches.push(current);
  return batches;
}

// Everything outgoing goes through here, so every request is bounded and a
// session too large for one request simply arrives across several: spans join
// on trace_id at ingest, so it still renders as exactly one trace.
//
// Returns the spans it could not deliver. An empty list means success; anything
// else is the caller's to queue or keep, along with the error that stopped it.
// Returning rather than calling back keeps the units unambiguous: these are
// spans, and it is the caller that decides what envelope they go into.
async function postSpans(spans) {
  const batches = chunkSpans(spans);
  for (let index = 0; index < batches.length; index += 1) {
    const batch = batches[index];
    try {
      await postPayload(buildBatchPayload(batch));
    } catch (err) {
      if (err instanceof PermanentSendError) {
        // Retrying cannot help, and chunkSpans has already bounded the batch,
        // so this is either a rejection or one span too large to ever send.
        const names = batch.map((span) => span.name).join(", ");
        process.stderr.write(
          `[orq-trace] WARN: dropping ${batch.length} span(s) [${names}]: ${err.message}\n`,
        );
        continue;
      }
      // Everything from the failing batch onward is still owed.
      return { undelivered: batches.slice(index).flat(), error: err };
    }
  }
  return { undelivered: [], error: null };
}

export async function drainQueue() {
  const queueFiles = await listQueuedFiles();
  for (const filePath of queueFiles) {
    let payload;
    try {
      payload = await readQueuedPayload(filePath);
    } catch (err) {
      process.stderr.write(`[orq-trace] WARN: dropping corrupt queue file: ${err?.message}\n`);
      await deleteQueuedFile(filePath);
      continue;
    }
    // Re-chunked rather than posted as-is, so a file queued before batching
    // existed still gets through instead of wedging the queue forever.
    const queued = spansOf(payload);
    const { undelivered, error } = await postSpans(queued);

    if (undelivered.length === 0) {
      await deleteQueuedFile(filePath);
      continue;
    }

    process.stderr.write(`[orq-trace] WARN: drain retry failed: ${error?.message}\n`);
    // Shrink the file to what still needs sending, so the next drain does not
    // re-post the batches that already landed.
    if (undelivered.length !== queued.length) {
      await writeQueuedPayload(filePath, buildBatchPayload(undelivered)).catch(() => {});
    }
    // Still unreachable. Keep the file; the next hook tries again.
    break;
  }
}

export async function sendSpan(span) {
  return sendSpans([span]);
}

export async function sendSpans(spans) {
  if (spans.length === 0) {
    return;
  }

  await drainQueue().catch(() => {});

  const { undelivered, error } = await postSpans(spans);
  if (undelivered.length === 0) {
    return;
  }

  process.stderr.write(`[orq-trace] WARN: span send failed (queued for retry): ${error?.message}\n`);
  try {
    // Queued in the same envelope the endpoint takes, so a later drain reads
    // back exactly what was written.
    await enqueuePayload(buildBatchPayload(undelivered));
  } catch (enqueueErr) {
    process.stderr.write(
      `[orq-trace] WARN: span data lost, send failed and enqueue failed: ${enqueueErr?.message || enqueueErr}\n`,
    );
  }
}

export function createSpan({
  traceId,
  spanId,
  parentSpanId,
  name,
  kind = 1,
  startTimeUnixNano,
  endTimeUnixNano,
  attributes = [],
}) {
  return {
    traceId,
    spanId,
    parentSpanId,
    name,
    kind,
    startTimeUnixNano: startTimeUnixNano || nowUnixNano(),
    endTimeUnixNano: endTimeUnixNano || nowUnixNano(),
    attributes,
  };
}
