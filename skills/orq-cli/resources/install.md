# Installing the orq CLI

Companion to [`../SKILL.md`](../SKILL.md) Phase 1. Verified against orq CLI
5.1.0 and the live `install.sh` on 2026-08-31; flags confirmed by running the
installer's own `--help`.

Check before installing — it is usually already present:

```sh
orq version --json     # {"api_version":"4.14.3","cli":"5.1.0","install_method":"npm"}
```

## Installing

**npm is the recommended route, and the only one for Windows.** It needs
Node.js ≥ 14 and fetches the matching native binary for the platform:

```sh
npm install -g @orq-ai/cli
```

The `curl | sh` installer drops a raw binary at `~/.orq/bin/orq`, verifying it
against the release's published `.sha256`:

```sh
curl -fsSL https://cli.orq.ai/install.sh | sh
```

`https://cli.orq.ai/install.sh` is the canonical URL — verified, it redirects to
`raw.githubusercontent.com/orq-ai/orq-cli/main/install.sh` and is byte-identical
(same SHA-256). Prefer the short one; it is what upstream documents.

**Two defaults matter if you are not a human at a terminal.** Left alone, the
installer *asks to edit your shell profile* and then *runs `orq setup`*, which
starts an interactive OAuth login and offers to rewire the coding agents on the
machine. For an unattended install, decline both:

```sh
curl -fsSL https://cli.orq.ai/install.sh | sh -s -- --no-modify-path --no-setup
```

Flags go after `-s --`. Verified against the live installer's `--help`:

| Flag | Effect |
|---|---|
| `--version <v>` | Pin a release (`v5.1.3`). Default: latest |
| `--channel <c>` | `stable` (default) or `rc`, the pre-release line |
| `--install-dir <dir>` | Default `$HOME/.orq/bin`; must be writable by the current user |
| `--no-modify-path` | Do not touch the shell profile |
| `--no-setup` | Do not run `orq setup` afterwards |
| `--help` | Print this and exit 0 |

`ORQ_CLI_VERSION`, `ORQ_CLI_CHANNEL` and `ORQ_CLI_INSTALL_DIR` are the env-var
equivalents; a flag wins when both are given. `--channel` together with
`--version` is an error, but an *exported* `ORQ_CLI_CHANNEL` is treated as
ambient config and ignored rather than rejected when `--version` pins a release
— that is the combination `orq update` itself passes.

A checksum **mismatch** aborts the install, as does any failure to fetch the
checksum other than a 404. Releases published before the checksum assets existed
skip verification with a notice — so "no verification" is visible, not silent.

Two other routes, when neither of the above fits:

- **Pre-built binary** from the [Releases page](https://github.com/orq-ai/orq-cli/releases),
  named `orq-<os>-<arch>[.exe]`. Published targets are `darwin-arm64`,
  `darwin-x64`, `linux-arm64`, `linux-x64`, and `win32-x64.exe`, each beside its
  own `.sha256`. Man pages ship as `orq-man-pages.tar.gz`.
- **From source**, needing Go ≥ 1.23:
  `git clone https://github.com/orq-ai/orq-cli.git && cd orq-cli && make build`
  → `./bin/orq`. `orq update` refuses to replace a dev build and says to rebuild.

## After installing: `orq` not found, or the wrong `orq`

The `install.sh` route drops the binary in `~/.orq/bin`, which is often not on
`PATH` — guaranteed not to be if you passed `--no-modify-path`, and only there
after a new shell if you accepted the profile edit. **So a "failed" install is
usually a `PATH` problem.** Check the path before concluding anything:

```sh
~/.orq/bin/orq version --json          # works? then it installed fine
export PATH="$HOME/.orq/bin:$PATH"     # for this shell
```

The installer brackets its profile edit with `# >>> orq cli >>>` /
`# <<< orq cli <<<` markers, so you can find or remove it by hand.

If `orq` resolves to something that prints Node or oclif stack traces, `which orq`
is pointing at a different tool with the same name. Use the real binary's full
path rather than fighting `PATH`.

Confirm any install with `orq version --json` and read `install_method` — it
reports `npm`, `installer`, or `unknown`, which tells you which upgrade path
applies and whether `orq update` can act on this binary at all.

Upgrading is in [`../SKILL.md`](../SKILL.md) under Phase 1 — `orq update`, and
the npm 4.x → 5.x hop that `npm update -g` will not make.
