/**
 * Credential resolution for the trace hook. Nothing here touches the disk.
 *
 *   API key:
 *     1. CLAUDE_PLUGIN_OPTION_ORQ_API_KEY  the plugin's own `ORQ_API_KEY`
 *                                          userConfig option (sensitive), which
 *                                          Claude Code exports to hook processes
 *     2. ORQ_API_KEY env var
 *     3. none: tracing stays off
 *
 *   Base URL:       ORQ_BASE_URL env var, default https://my.orq.ai
 *   OTLP endpoint:  ORQ_OTLP_ENDPOINT env var, else null (otlp.js derives it)
 *
 * An empty string counts as unset: Claude Code exports an option the user left
 * blank as "", and a shell may export `VAR=`.
 */

function fromEnv(name) {
  const value = process.env[name];
  return typeof value === "string" && value !== "" ? value : null;
}

export function getApiKey() {
  return fromEnv("CLAUDE_PLUGIN_OPTION_ORQ_API_KEY") || fromEnv("ORQ_API_KEY");
}

/**
 * Explicit OTLP ingest URL, for hosts where the my. -> api. subdomain swap in
 * otlp.js does not serve OTLP (my.staging.orq.ai swaps to a host that answers
 * with a Cloudflare 526). Production needs nothing here.
 * OTEL_EXPORTER_OTLP_ENDPOINT, handled in otlp.js, still wins over this.
 */
export function getOtlpEndpoint() {
  return fromEnv("ORQ_OTLP_ENDPOINT");
}

export function getBaseUrl() {
  return fromEnv("ORQ_BASE_URL") || "https://my.orq.ai";
}
