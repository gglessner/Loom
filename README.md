# Loom

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
loom init                          # creates ~/.loom/loom.toml + ~/.loom/.env
# edit ~/.loom/.env and put in OPENROUTER_API_KEY (or VAULT_* + VERTEX_*)

# 3. cd to any codebase and go
cd path/to/some/project
loom
```

If `loom` isn't found after install, see [Putting `loom` on your PATH](#putting-loom-on-your-path).

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

Python 3.10+. This installs a `loom` console script.

### Putting `loom` on your PATH

`pip install` may print a warning that the Scripts directory isn't on your
PATH. The exact path depends on your platform; pip will print it. Common
locations:

- **Windows (cmd.exe / PowerShell)**: `%APPDATA%\Python\Python3xx\Scripts`,
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

If you don't want to touch PATH at all, `python -m loom` always works as
long as `python` is on your PATH. Everything documented for `loom` below
works as `python -m loom` too.

## Configure once

```bash
loom init
```

This creates `~/.loom/loom.toml` and `~/.loom/.env`. Open them and put your
provider details in - this is your user-global default that `loom` will use
from any directory.

```bash
loom where     # show which config files Loom would load right now
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
over any `.env` file (so `OPENROUTER_API_KEY=... loom` works as you'd expect).

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

## Run

```bash
cd path/to/some/codebase
loom
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

| Command                          | What it does                                  |
| -------------------------------- | --------------------------------------------- |
| `loom`                           | Start a session in the current directory.     |
| `loom init`                      | Scaffold `~/.loom/loom.toml` and `~/.loom/.env`. |
| `loom init --force`              | Overwrite existing user-global templates.     |
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
2. `<cwd>/.loom/skills/`       project-local, hidden directory
3. `<cwd>/<skills_dir>/`       project-local, configurable via `loom.skills_dir` in `loom.toml`

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

## Tests

```bash
pytest -q
```

## License

Loom is free software: you can redistribute it and/or modify it under the
terms of the GNU General Public License as published by the Free Software
Foundation, either version 3 of the License, or (at your option) any later
version. See [LICENSE](LICENSE) for the full text.

Loom is distributed in the hope that it will be useful, but WITHOUT ANY
WARRANTY; without even the implied warranty of MERCHANTABILITY or FITNESS
FOR A PARTICULAR PURPOSE.

Copyright (C) 2026 Garland Glessner &lt;gglessner@gmail.com&gt;
