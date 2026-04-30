import fs from "node:fs";
import path from "node:path";

const ORQ_CONFIG_PATH = path.join(
  process.env.HOME || process.env.USERPROFILE || "",
  ".config",
  "orq",
  "config.json",
);

let _cached = null;

function loadOrqConfig() {
  if (_cached !== null) return _cached;
  try {
    const raw = fs.readFileSync(ORQ_CONFIG_PATH, "utf8");
    _cached = JSON.parse(raw);
  } catch (err) {
    if (err?.code !== "ENOENT") {
      process.stderr.write(
        `[orq-trace] WARN: failed to load orq config (${ORQ_CONFIG_PATH}): ${err?.message}\n`,
      );
    }
    _cached = {};
  }
  return _cached;
}

/**
 * ┌─────────────────────────────────────────────────────────────────┐
 * │           Credential Resolution Hierarchy (trace hook)         │
 * ├─────────────────────────────────────────────────────────────────┤
 * │                                                                │
 * │  API Key:                                                      │
 * │    1. ORQ_TRACE_PROFILE profile        (trace-specific)         │
 * │    2. ORQ_API_KEY env var              (general key)             │
 * │    3. Profile resolved below                                   │
 * │                                                                │
 * │  Profile (determines api_key + base_url):                      │
 * │    1. ORQ_TRACE_PROFILE env var        (trace-specific)         │
 * │    2. ORQ_PROFILE env var              (general orq profile)    │
 * │    3. "current" in ~/.config/orq/config.json  (CLI default)     │
 * │                                                                │
 * │  Base URL:                                                     │
 * │    1. ORQ_BASE_URL env var             (explicit override)      │
 * │    2. Profile resolved above                                   │
 * │    3. Default: https://my.orq.ai                               │
 * │                                                                │
 * │  Set ORQ_TRACE_PROFILE to decouple trace destination from the  │
 * │  profile used for CLI/MCP operations.                          │
 * └─────────────────────────────────────────────────────────────────┘
 */
function resolveProfile() {
  const config = loadOrqConfig();
  const profiles = config.profiles || {};

  const resolved = resolveProfileName();
  if (resolved.name && profiles[resolved.name]) {
    return profiles[resolved.name];
  }

  return null;
}

/**
 * Returns the resolved profile name and which source it came from.
 */
function resolveProfileName() {
  const config = loadOrqConfig();

  if (process.env.ORQ_TRACE_PROFILE) {
    return { name: process.env.ORQ_TRACE_PROFILE, source: "ORQ_TRACE_PROFILE env var" };
  }
  if (process.env.ORQ_PROFILE) {
    return { name: process.env.ORQ_PROFILE, source: "ORQ_PROFILE env var" };
  }
  if (config.current) {
    return { name: config.current, source: `~/.config/orq/config.json (current)` };
  }
  return { name: null, source: "none — no profile found" };
}

/**
 * Resolve the API key with the following priority:
 * 1. ORQ_TRACE_PROFILE profile's api_key (trace-specific override)
 * 2. ORQ_API_KEY env var (general key)
 * 3. ORQ_PROFILE profile's api_key
 * 4. Current profile in ~/.config/orq/config.json
 *
 * ORQ_TRACE_PROFILE wins over ORQ_API_KEY so users can decouple
 * where traces go from the key used for CLI/MCP operations.
 */
export function getApiKey() {
  // If a trace-specific profile is set, its key takes priority over everything
  if (process.env.ORQ_TRACE_PROFILE) {
    const config = loadOrqConfig();
    const profile = config.profiles?.[process.env.ORQ_TRACE_PROFILE];
    if (profile?.api_key) {
      return profile.api_key;
    }
  }
  if (process.env.ORQ_API_KEY) {
    return process.env.ORQ_API_KEY;
  }
  return resolveProfile()?.api_key || null;
}

/**
 * Resolve the base URL with the following priority:
 * 1. ORQ_TRACE_PROFILE profile's base_url (trace-specific override)
 * 2. ORQ_BASE_URL env var (general override)
 * 3. ORQ_PROFILE profile's base_url
 * 4. Current profile in ~/.config/orq/config.json
 * 5. Default: https://my.orq.ai
 */
export function getBaseUrl() {
  if (process.env.ORQ_TRACE_PROFILE) {
    const config = loadOrqConfig();
    const profile = config.profiles?.[process.env.ORQ_TRACE_PROFILE];
    if (profile?.base_url) {
      return profile.base_url;
    }
  }
  if (process.env.ORQ_BASE_URL) {
    return process.env.ORQ_BASE_URL;
  }
  return resolveProfile()?.base_url || "https://my.orq.ai";
}

