// mcp-client.mjs — thin wrapper over the official MCP SDK for the functional track.
//
// Connects to the orq workspace MCP server defined in .mcp.json (streamable HTTP,
// Bearer auth), and exposes just what the functional runner needs: list tool
// names (for drift detection) and call a tool by name, returning its parsed JSON.

import { readFileSync } from "node:fs";
import { Client } from "@modelcontextprotocol/sdk/client/index.js";
import { StreamableHTTPClientTransport } from "@modelcontextprotocol/sdk/client/streamableHttp.js";

const SERVER = "orq-workspace";

/** Expand ${VAR} references in a string against process.env. */
function expandEnv(s) {
  return String(s).replace(/\$\{([A-Z0-9_]+)\}/gi, (_, name) => process.env[name] ?? "");
}

/** Read the orq-workspace server config (url + headers) from .mcp.json. */
export function readServerConfig(mcpConfigPath) {
  const cfg = JSON.parse(readFileSync(mcpConfigPath, "utf8"));
  const srv = cfg.mcpServers?.[SERVER];
  if (!srv) throw new Error(`${mcpConfigPath}: missing mcpServers.${SERVER}`);
  const headers = {};
  for (const [k, v] of Object.entries(srv.headers || {})) headers[k] = expandEnv(v);
  return { url: srv.url, headers };
}

export class McpClient {
  constructor(mcpConfigPath) {
    this.config = readServerConfig(mcpConfigPath);
    this.client = null;
    this._toolNames = null;
  }

  async connect() {
    const transport = new StreamableHTTPClientTransport(new URL(this.config.url), {
      requestInit: { headers: this.config.headers },
    });
    this.client = new Client({ name: "skill-tester-functional", version: "0.1.0" }, { capabilities: {} });
    await this.client.connect(transport);
  }

  /** All tool names the server exposes (cached). Used for drift detection. */
  async listToolNames() {
    if (this._toolNames) return this._toolNames;
    const { tools } = await this.client.listTools();
    this._toolNames = tools.map((t) => t.name);
    return this._toolNames;
  }

  /**
   * Call a tool by short name (orq MCP tools are unprefixed) and return:
   *   { json, text, isError } — json is the first text block parsed as JSON
   *   (or undefined if it isn't JSON); text is the raw concatenated text.
   */
  async callTool(name, args) {
    const res = await this.client.callTool({ name, arguments: args || {} });
    const text = (res.content || [])
      .filter((c) => c.type === "text")
      .map((c) => c.text)
      .join("\n");
    let json;
    try {
      json = JSON.parse(text);
    } catch {
      json = undefined;
    }
    return { json, text, isError: !!res.isError };
  }

  async close() {
    try {
      await this.client?.close();
    } catch {
      /* ignore */
    }
  }
}
