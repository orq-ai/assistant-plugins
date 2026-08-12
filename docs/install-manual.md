# Manual clone — Claude Code

Use when you want to run the plugin from a local checkout (e.g. for development).

```bash
git clone https://github.com/orq-ai/assistant-plugins.git
cd assistant-plugins
claude --plugin-dir .
```

> **Note:** Commands (`/orq:quickstart`, `/orq:workspace`, etc.) and agents are only available when installed as a Claude Code plugin.

> **Agent Plugins 1.0.0:** the same checkout is a portable [Agent Plugins](https://agent-plugins.org) plugin — root `plugin.json` plus `skills/`. Point any spec-conformant client at it instead of `--plugin-dir`. See [Agent Plugins 1.0.0 clients](../README.md#agent-plugins-100-clients).
