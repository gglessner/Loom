# Loom

[![version](https://img.shields.io/badge/version-1.3.3-D77757)](#changelog) [![python](https://img.shields.io/badge/python-3.10%2B-blue)](#install) [![license](https://img.shields.io/badge/license-GPL--3.0--or--later-blue)](LICENSE) [![tests](https://img.shields.io/badge/tests-passing-4EBA65)](#tests)

Copyright (C) 2026 Garland Glessner &lt;gglessner@gmail.com&gt;
Licensed under the GNU General Public License v3.0 or later. See [LICENSE](LICENSE).

A minimal AI coder for the terminal. Multi-turn agentic loop, real MCP support,
skill files, two provider backends:

- **Vertex AI** (Claude on Vertex), with credentials sourced from HashiCorp
  Vault via AppRole.
- **OpenRouter** for any model OpenRouter.ai serves.

A single Python process with a small dependency surface, useful when heavier
IDE integrations won't cooperate with your environment.

## Quickstart

```bash
# 1. install (Python 3.10+)
cd Loom
pip install -e .

# 2. one-time user-global config
python -m loom init                # creates ~/.loom/loom.toml + ~/.loom/.env
# edit ~/.loom/.env and put in OPENROUTER_API_KEY (or VAULT_* + VERTEX_*)

# 3. cd to any codebase and go
cd path/to/some/project
python -m loom
```

> **`loom` vs. `python -m loom`** - both invoke the exact same code. `python -m loom`
> always works as long as `python` is on your PATH. The shorter `loom` command
> is also installed by `pip`, but only works if its Scripts directory is on
> PATH (see [Putting `loom` on your PATH](#putting-loom-on-your-path)). The rest of
> this README uses `loom` for brevity; if it's not found, just prepend `python -m`.

## Features

- Streaming responses with **Ctrl+C interrupt** - abort a runaway generation
  without losing the partial response or your shell.
- Provider-agnostic tool calling. Builtin tools cover `read_file`,
  `write_file`, `edit_file`, `list_dir`, `tree`, `grep`, `find_files`,
  `run_shell`, `run_python`, `platform_info`, plus `excel_*` helpers.
- Real **MCP** clients (stdio transport). Connect to any MCP server and its
  tools become available to the model under namespaced names like
  `mcp__filesystem__read_file`.
- Skill files (`*.md` in `skills/`) are appended to the system prompt.
- Cross-platform: Windows + macOS (and Linux).

## Install

```bash
pip install -e .
```

Python 3.10+. This installs the `loom` package; you can invoke it two ways:

```bash
python -m loom            # always works as long as python is on PATH
loom                      # shorter, but requires the Scripts dir to be on PATH
```

If you only ever use `python -m loom`, you can skip the next section.

### Putting `loom` on your PATH (optional)

`pip install` may print a warning that the Scripts directory isn't on your
PATH. The exact path depends on your platform; pip will print it. Common
locations:

- **Windows**: `%APPDATA%\Python\Python3xx\Scripts`,
  e.g. `C:\Users\<you>\AppData\Roaming\Python\Python310\Scripts`
- **macOS**: `~/Library/Python/3.xx/bin`
- **Linux**: `~/.local/bin`

To add it permanently:

```powershell
# Windows PowerShell - add to user PATH (no admin needed). Open a NEW terminal afterwards.
$dir = "$env:APPDATA\Python\Python310\Scripts"
$cur = [Environment]::GetEnvironmentVariable("Path","User")
if (-not (($cur -split ';') -contains $dir)) {
  [Environment]::SetEnvironmentVariable("Path", "$cur;$dir", "User")
}
```

```bash
# macOS / Linux - put in your shell rc (~/.zshrc, ~/.bashrc, etc.)
export PATH="$HOME/Library/Python/3.11/bin:$PATH"   # macOS, adjust 3.11
export PATH="$HOME/.local/bin:$PATH"                # Linux
```

## Configure once

```bash
python -m loom init        # or just `loom init` if PATH is set up
```

This creates `~/.loom/loom.toml` and `~/.loom/.env`. Open them and put your
provider details in - this is your user-global default that Loom will use
from any directory. Re-running `loom init` is safe; it won't overwrite
existing files. Use `--force` if you really want to start over.

```bash
python -m loom where       # show which config files Loom would load right now
```

### Config layering

Loom merges config from these locations, with later layers overriding earlier:

| Priority | Location                  | Purpose                                      |
| -------- | ------------------------- | -------------------------------------------- |
| 1 (low)  | `~/.loom/loom.toml`       | Your defaults (provider, model, tokens, ...) |
| 2        | `<cwd>/.loom/loom.toml`   | Project-local overrides (gitignore-friendly) |
| 3        | `<cwd>/loom.toml`         | Project-local, visible                       |
| 4 (high) | `--config <path>`         | Explicit override for one session            |

`.env` files layer the same way, except OS environment variables always win
over any `.env` file (so `OPENROUTER_API_KEY=... python -m loom` works as
you'd expect).

### Vertex

You'll need:

- `vertex.project_id`, `vertex.region`, `vertex.model` (e.g. `claude-opus-4-6`)
- A Vault AppRole that can read a secret containing a Google OAuth token. The
  Vault GCP secrets engine produces these at paths like
  `gcp/token/<roleset>`; Loom reads `data.token` (or `data.access_token`)
  out of the response automatically.
- `VAULT_URL`, `VAULT_NAMESPACE`, `VAULT_ROLE_ID`, `VAULT_SECRET_ID`,
  `VAULT_TOKEN_PATH` - usually via `.env`.

Loom caches both the Vault session token and the GCP access token, refreshing
each just before expiry.

### OpenRouter

Just set `OPENROUTER_API_KEY` and pick a model (`OPENROUTER_MODEL`,
default `anthropic/claude-opus-4.7`).

### TLS

Loom does TLS verification by default and gives you two levels of override
for when something on your network can't be verified out of the box.

#### Internal Vault with an internal CA (most common case)

Your Vault server uses an internal CA that your fresh Mac/Windows install
doesn't trust, but Vertex/OpenRouter (on public CAs) verify fine. Use the
Vault-only knob so external traffic stays strictly verified:

```toml
# ~/.loom/loom.toml - point Vault at your internal CA bundle
[vault]
tls_ca_bundle = "/path/to/internal-vault-ca.pem"
```

Or via env: `VAULT_TLS_CA_BUNDLE=/path/to/internal-vault-ca.pem`.

Don't have the CA file handy? Last resort - disable verification just for
Vault:

```toml
[vault]
tls_verify = false
```

Or `VAULT_TLS_VERIFY=false`. Loom prints a warning at startup so you don't
forget. Vertex/OpenRouter still verify normally.

#### Corporate TLS-intercepting proxy (everything fails verification)

If your network does TLS interception (corporate Wi-Fi/VPN) and **all**
HTTPS calls fail, set the global knob instead - it applies to Vault,
Vertex, and OpenRouter:

```toml
[loom]
tls_ca_bundle = "/path/to/corporate-proxy-ca.pem"     # preferred
# tls_verify = false                                  # last resort
```

Or `LOOM_TLS_CA_BUNDLE` / `LOOM_TLS_VERIFY` env vars.

These do *not* affect MCP servers, which manage their own network stacks.

## Run

```bash
cd path/to/some/codebase
python -m loom             # or just `loom` if PATH is set up
```

Loom opens with that directory as its working directory; every tool call
(`read_file`, `run_shell`, `grep`, ...) operates relative to it. While the
model is generating, Ctrl+C interrupts cleanly without losing the partial
response. At the prompt, Ctrl+C exits.

### Inside the REPL

| Command       | What it does                                                |
| ------------- | ----------------------------------------------------------- |
| `/help`       | List slash commands.                                        |
| `/tools`      | List every tool (builtin + MCP) the model can call.         |
| `/skills`     | List loaded skill files (with which directory they came from). |
| `/system`     | Print the current system prompt.                            |
| `/history`    | Print the conversation so far.                              |
| `/clear`      | Clear conversation history (keeps system prompt).           |
| `/config`     | Show active config + provider.                              |
| `/quit`       | Exit Loom (or just press Ctrl+C at the prompt).             |

### Outside the REPL

(Anywhere it says `loom` you can substitute `python -m loom`.)

| Command                          | What it does                                  |
| -------------------------------- | --------------------------------------------- |
| `loom`                           | Start a session in the current directory.     |
| `loom init`                      | Scaffold `~/.loom/loom.toml` and `~/.loom/.env`. Prints `exists` for any file already present. |
| `loom init --force`              | Overwrite existing user-global templates (will wipe your API key in `.env`!). |
| `loom where`                     | Show config + .env discovery order for the current cwd. |
| `loom --provider openrouter`     | One-shot provider override.                   |
| `loom --provider vertex`         | One-shot provider override.                   |
| `loom --config <path>`           | Use a specific `loom.toml` for this session.  |
| `loom --env <path>`              | Use a specific `.env` for this session.       |

## Skills (per-project + user-global)

Skills are plain markdown files appended to the system prompt. Loom merges
them from these places, with later sources overriding earlier ones of the
same filename:

1. `~/.loom/skills/`           your personal library
2. `<cwd>/skills/`             project-shared, visible (check this into git!)
3. `<cwd>/.loom/skills/`       project-local, hidden (overrides shared)
4. `<cwd>/<skills_dir>/`       configurable via `loom.skills_dir` in `loom.toml`

Drop any `.md` file in there and it shows up in `/skills`.

## Adding MCP servers

Drop entries into `loom.toml`:

```toml
[[mcp_servers]]
name = "filesystem"
command = "npx"
args = ["-y", "@modelcontextprotocol/server-filesystem", "."]
```

Loom keeps a single persistent stdio session per server for the lifetime of
the REPL.

## Layout

```
loom/
  cli.py            REPL + interrupt handling
  agent.py          streaming agent loop
  config.py         TOML + env loader
  vault.py          Vault AppRole client
  mcp_runtime.py    background asyncio loop driving MCP sessions
  skills.py         skill .md loader
  providers/
    base.py         unified Message/Tool/StreamEvent
    openrouter.py   OpenAI-compat SSE
    vertex.py       anthropic[vertex] SDK
  tools/
    filesystem.py   read/write/edit/list/tree/copy/move/delete/mkdir
    search.py       grep / find_files
    shell.py        run_shell / run_python / platform_info
    excel.py        excel_read / excel_write / excel_sheets
```

## Colors

Loom mimics Claude Code's accent palette (orange brand `rgb(215,119,87)`,
plus semantic red/yellow/green/blue for errors/warnings/success/info, and a
dim grey for tool-call lines and result previews). Cross-platform: Windows
11, Linux, and macOS. On Windows the agent enables
`ENABLE_VIRTUAL_TERMINAL_PROCESSING` automatically, so no `colorama`
dependency is required.

**Apple Terminal users:** Terminal.app does not faithfully render 24-bit
RGB, so Loom auto-detects it (`TERM_PROGRAM=Apple_Terminal`) and emits
256-color sequences with hand-picked nearest matches - same orange brand,
correct rendering. iTerm2, Ghostty, Kitty, Alacritty, WezTerm and friends
all advertise `COLORTERM=truecolor` and get the full 24-bit palette. Force
the choice manually with `LOOM_TRUECOLOR=1` or `LOOM_TRUECOLOR=0`.

Toggle in `loom.toml`:

```toml
[loom]
color = "auto"   # default: on if stdout is a TTY and NO_COLOR is not set
# color = "on"   # force on (alias: "true", "dark")
# color = "light"
# color = "off"  # never colorize (alias: "false", "none")
```

Or via env (highest precedence):

```bash
LOOM_COLOR=off    # explicit Loom-only override
NO_COLOR=1        # global convention (https://no-color.org/) - all CLI tools
FORCE_COLOR=1     # force on even when stdout isn't a TTY
```

## Wrapping

The model's text is word-wrapped to the current terminal width on each
turn, so window resizes between turns are honoured automatically. Code
blocks (``` ... ```) are streamed verbatim - source code layout is never
chopped at a column.

```toml
[loom]
wrap = "auto"   # default: current terminal columns
# wrap = "off"  # raw streaming, terminal hard-wraps as it sees fit
# wrap = 100    # fixed column count (useful when piping to a file)
```

Or via env: `LOOM_WRAP=off` / `LOOM_WRAP=120`.

## Tests

```bash
pytest -q
```

## Changelog

### 1.3.3
- Apple Terminal.app fix. Mac's built-in Terminal doesn't render 24-bit RGB
  faithfully (it tends to mash everything into a green-ish blob). Loom now
  detects `TERM_PROGRAM=Apple_Terminal` (and any other terminal that doesn't
  advertise `COLORTERM=truecolor`) and downgrades to 256-color sequences
  with hand-picked xterm-256 nearest matches - same orange brand, just
  emitted as `38;5;173` instead of `38;2;215;119;87`.
- Override either way with `LOOM_TRUECOLOR=1` (force 24-bit) or
  `LOOM_TRUECOLOR=0` (force 256-color). For when our auto-detection misses
  your specific terminal.

### 1.3.2
- Skill discovery now also picks up `<cwd>/skills/` (the natural top-level
  location), in addition to `<cwd>/.loom/skills/` and `~/.loom/skills/`.
  Drop a `coding.md` next to your `loom.toml` and it just works -
  `/skills` will list it on startup.

### 1.3.1
- Streaming word-wrap. LLM output is now wrapped to the current terminal
  width at word boundaries, so long sentences no longer get sliced
  mid-character by the terminal. Code fences (``` ... ```) are detected
  and preserved verbatim - source code layout never gets clobbered.
  Toggle via `[loom] wrap = "auto" | "off" | <int>` (also `LOOM_WRAP`).
- Visible version line + shields in README so a new release is obvious from
  the GitHub landing page.

### 1.3.0
- Default `max_tokens` raised from 4096 to **128000** (Claude Opus 4.6's hard
  cap). It's a ceiling, not a target - you only pay for what the model
  actually emits, and adaptive thinking finally has the headroom it needs.
- New `[loom] color = "auto"` setting (also `LOOM_COLOR` env var). Adds a
  Claude-Code-inspired terminal palette: orange brand accent
  (`rgb(215,119,87)`) on the banner / prompt, dim grey for tool calls and
  result previews, semantic red/yellow/green/blue for errors / warnings /
  success / info. Values: `auto` (default; on if stdout is a TTY and
  `NO_COLOR` is not set), `on` / `dark`, `light`, or `off`.
- Cross-platform: Windows 11, Linux, and macOS. On Windows we flip
  `ENABLE_VIRTUAL_TERMINAL_PROCESSING` via `SetConsoleMode` so escape codes
  are interpreted natively without `colorama`. Honours the
  [NO_COLOR](https://no-color.org/) and `FORCE_COLOR` conventions.

### 1.2.4
- REPL prompt is now `>` instead of `loom>`.
- No more blank line under the user's input on the first agent step. Step 2+
  still gets a leading newline so the LLM turn is separated from prior tool
  output.

### 1.2.3
- REPL now prints a blank line after the model's text so the next prompt or
  `[tool] ...` line isn't visually glued to the LLM output. Idempotent: works
  whether the model's last token ends with a newline or not.

### 1.2.2
- `VAULT_TOKEN_PATH` / `vault.token_path` now tolerates a leading `v1/` (or
  `/v1/`, case-insensitive) and leading slashes - Loom always prepends `v1/`
  itself, so a doubled prefix would 404. Paste from `vault read v1/...` and
  it just works.

### 1.2.1
- Vertex provider: transparent one-shot retry on `401 AuthenticationError` /
  `403 PermissionDeniedError`. We force-refresh the GCP access token from
  Vault, rebuild the `AnthropicVertex` client, and reopen the stream once
  before propagating the error - covers clock skew and mid-session token
  revocation that the proactive 60-second leeway can't catch.

### 1.2.0
- Vault GCP secrets: if `GET <path>/token` returns 403 *permission denied*,
  Loom transparently retries as `POST` (Vault accepts both methods, and many
  site policies grant `update` rather than `read`).
- When both methods are denied, the error message now includes a copy-paste
  policy snippet and a `vault token capabilities` diagnostic command, so it's
  obvious whether you need a Vault admin or just the wrong path.

### 1.1.0
- TLS controls for outbound HTTPS: `tls_verify` and `tls_ca_bundle` (also
  via `LOOM_TLS_*` env vars) apply to Vault, Vertex, and OpenRouter.
- Vault-specific overrides `vault.tls_verify` / `vault.tls_ca_bundle` (also
  `VAULT_TLS_*` env vars) for the common case where an internal Vault has a
  CA your OS doesn't trust but external services like Vertex still do.
- Loud startup warning when verification is disabled; urllib3's per-request
  `InsecureRequestWarning` is silenced so logs stay readable.

### 1.0.0
- Initial release. Multi-turn agentic loop, streaming with Ctrl+C interrupt,
  Vertex (via Vault AppRole) and OpenRouter providers, real MCP support,
  layered config (`~/.loom`, project, OS env), per-project + user-global
  skills, builtin filesystem/search/shell/excel tools.

## License

Loom is free software: you can redistribute it and/or modify it under the
terms of the GNU General Public License as published by the Free Software
Foundation, either version 3 of the License, or (at your option) any later
version. See [LICENSE](LICENSE) for the full text.

Loom is distributed in the hope that it will be useful, but WITHOUT ANY
WARRANTY; without even the implied warranty of MERCHANTABILITY or FITNESS
FOR A PARTICULAR PURPOSE.

Copyright (C) 2026 Garland Glessner &lt;gglessner@gmail.com&gt;
