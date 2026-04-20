import { attr, compact, nowUnixNano } from "./common.js";
import { getApiKey, getBaseUrl } from "./config.js";
import {
  deleteQueuedFile,
  enqueuePayload,
  listQueuedFiles,
  readQueuedPayload,
} from "./state.js";

const SCOPE_NAME = "orq-claude-code";
const SDK_VERSION = "0.1.0";

function getEndpoint() {
  const explicit = process.env.OTEL_EXPORTER_OTLP_ENDPOINT;
  if (explicit) {
    return explicit.endsWith("/v1/traces")
      ? explicit
      : `${explicit.replace(/\/$/, "")}/v1/traces`;
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
  if (process.env.ORQ_DEBUG === "1" || process.env.ORQ_DEBUG === "true") {
    const spanCount = payload?.resourceSpans?.[0]?.scopeSpans?.[0]?.spans?.length || 0;
    const names = (payload?.resourceSpans?.[0]?.scopeSpans?.[0]?.spans || []).map(s => s.name).join(", ");
    try { const fs = await import("node:fs"); fs.default.appendFileSync("/tmp/orq-trace-debug.log", `[otlp] PRE-POST ${endpoint} spans=${spanCount} [${names}]\n`); } catch {}
  }
  const response = await fetch(endpoint, {
    method: "POST",
    headers: getHeaders(),
    body: JSON.stringify(payload),
  });

  const body = await response.text().catch(() => "");
  if (!response.ok) {
    throw new Error(`OTLP send failed (${response.status}): ${body}`);
  }

  if (process.env.ORQ_DEBUG === "1" || process.env.ORQ_DEBUG === "true") {
    const fs = await import("node:fs");
    fs.default.appendFileSync("/tmp/orq-trace-debug.log", `[otlp] POST ${endpoint} ${response.status}: ${body}\n`);
  }
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
    try {
      await postPayload(payload);
      await deleteQueuedFile(filePath);
    } catch {
      // Network/endpoint failure — stop draining and retry next invocation.
      break;
    }
  }
}

export async function sendSpan(span) {
  return sendSpans([span]);
}

export async function sendSpans(spans) {
  if (spans.length === 0) {
    return;
  }

  const payload = buildBatchPayload(spans);

  try {
    await drainQueue();
    await postPayload(payload);
  } catch (sendErr) {
    process.stderr.write(`[orq-trace] WARN: span send failed (queued for retry): ${sendErr?.message}\n`);
    try {
      await enqueuePayload(payload);
    } catch (enqueueErr) {
      process.stderr.write(
        `[orq-trace] WARN: span data lost — send failed and enqueue failed: ${enqueueErr?.message || enqueueErr}\n`,
      );
    }
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
