"""Loom REPL.

Interrupt handling: while the agent is generating, Ctrl+C sets a
``threading.Event`` that the provider polls between stream chunks; the
current generation aborts cleanly, the partial assistant message is kept
in history, and you're returned to the prompt. A second Ctrl+C at the
prompt exits Loom. (This works the same on Windows and macOS.)
"""

from __future__ import annotations

import argparse
import signal
import sys
from pathlib import Path
from threading import Event
from typing import Optional

from .agent import Agent
from .config import (
    LoomConfig,
    REPO_ROOT,
    USER_HOME,
    discover_config_paths,
    load_config,
    resolve_tls_verify,
    validate_for_provider,
)
from .mcp_runtime import MCPRuntime
from .providers import Message, build_provider
from .skills import SkillManager
from .tools.registry import ToolRegistry, builtin_tools


def _apply_tls_settings(cfg: LoomConfig) -> None:
    """Suppress urllib3's per-request warning when verification is off, and
    emit one loud startup line so the user is reminded their traffic is
    insecure. A custom CA bundle path is fine - no warning needed."""
    global_off = not cfg.tls_verify
    vault_off = cfg.vault.tls_verify is False
    any_off = global_off or vault_off
    if any_off:
        try:
            import urllib3

            urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)
        except Exception:
            pass
    if global_off:
        print(
            "[!] TLS verification is DISABLED globally (loom.tls_verify=false). "
            "Traffic to Vault, Vertex, and OpenRouter is encrypted but server "
            "certificates are NOT being validated. Prefer setting tls_ca_bundle "
            "to a CA file instead."
        )
    elif vault_off:
        print(
            "[!] TLS verification is DISABLED for Vault only "
            "(vault.tls_verify=false). Vertex/OpenRouter still verify normally."
        )
    if cfg.tls_ca_bundle:
        print(f"[*] Global TLS CA bundle: {cfg.tls_ca_bundle}")
    if cfg.vault.tls_ca_bundle:
        print(f"[*] Vault-specific TLS CA bundle: {cfg.vault.tls_ca_bundle}")


BANNER = """
  ╷  ┌─┐┌─┐┌┬┐
  │  │ ││ ││││
  └─╴└─┘└─┘╵ ╵   a minimal AI coder
"""


def _build_system_prompt(cfg: LoomConfig, skills: SkillManager, registry: ToolRegistry) -> str:
    parts = [cfg.system_prompt]
    skills_block = skills.system_block()
    if skills_block:
        parts.append("\n" + skills_block)
    if registry.names():
        parts.append(
            "\n# Tools\nYou can call any of the following tools by name: "
            + ", ".join(registry.names())
            + "."
        )
    return "\n".join(parts)


class LoomCLI:
    def __init__(self, cfg: LoomConfig) -> None:
        self._cfg = cfg
        self._registry = ToolRegistry()
        self._registry.register_many(builtin_tools())
        cwd = Path.cwd()
        skill_dirs = [
            USER_HOME / "skills",          # ~/.loom/skills
            cwd / ".loom" / "skills",      # ./.loom/skills
            cwd / cfg.skills_dir,          # configurable, default ./.loom/skills
        ]
        self._skills = SkillManager(skill_dirs)
        self._mcp = MCPRuntime(cfg.mcp_servers)
        self._provider = build_provider(cfg)
        self._agent = Agent(
            self._provider,
            self._registry,
            max_tokens=cfg.max_tokens,
            temperature=cfg.temperature,
            max_steps=cfg.max_agent_steps,
        )
        self._messages: list[Message] = []
        self._cancel: Optional[Event] = None

    # ----- lifecycle ---------------------------------------------------------

    def init(self) -> None:
        skill_names = self._skills.discover()
        mcp_names = self._mcp.start_and_register(self._registry)

        print(BANNER)
        print(f"  cwd      : {Path.cwd()}")
        print(f"  provider : {self._provider.name}")
        print(f"  model    : {self._provider.model}")
        print(f"  tools    : {len(self._registry.names())} ({', '.join(self._registry.names()[:8])}{'...' if len(self._registry.names()) > 8 else ''})")
        print(f"  skills   : {len(skill_names)} {tuple(skill_names) if skill_names else ''}")
        print(f"  mcp      : {len(mcp_names)} {tuple(mcp_names) if mcp_names else ''}")
        print()
        print("Type a message, or /help for commands. Ctrl+C interrupts a")
        print("running response; press Ctrl+C again at the prompt to exit.\n")

        system_prompt = _build_system_prompt(self._cfg, self._skills, self._registry)
        self._messages = [Message(role="system", content=system_prompt)]

    def shutdown(self) -> None:
        self._mcp.stop()

    # ----- repl --------------------------------------------------------------

    def loop(self) -> None:
        while True:
            try:
                line = input("loom> ").rstrip()
            except (EOFError, KeyboardInterrupt):
                print()
                return
            if not line:
                continue
            if line.startswith("/"):
                if self._handle_command(line):
                    return
                continue
            self._handle_user_turn(line)

    def _handle_command(self, line: str) -> bool:
        """Return True if the CLI should exit."""
        parts = line.split(None, 1)
        cmd = parts[0].lower()
        arg = parts[1] if len(parts) > 1 else ""

        if cmd in ("/quit", "/exit"):
            return True
        if cmd == "/help":
            print(_HELP_TEXT)
        elif cmd == "/clear":
            sys_msg = self._messages[0] if self._messages and self._messages[0].role == "system" else None
            self._messages = [sys_msg] if sys_msg else []
            print("[history cleared]")
        elif cmd == "/tools":
            print("\n".join(self._registry.names()) or "(no tools)")
        elif cmd == "/skills":
            print(self._skills.short_summary())
        elif cmd == "/config":
            self._print_config()
        elif cmd == "/system":
            sys_msg = self._messages[0] if self._messages else None
            print(sys_msg.content if sys_msg else "(no system prompt)")
        elif cmd == "/history":
            for i, m in enumerate(self._messages):
                head = (m.content or "").replace("\n", " ")[:90]
                tc = f" [tool_calls={[c.name for c in m.tool_calls]}]" if m.tool_calls else ""
                print(f"{i:>3} {m.role:<9} {head}{tc}")
        else:
            print(f"unknown command: {cmd} (try /help)")
        return False

    def _handle_user_turn(self, text: str) -> None:
        self._messages.append(Message(role="user", content=text))

        cancel = Event()
        self._cancel = cancel

        # Replace the SIGINT handler for the duration of generation so Ctrl+C
        # signals cancellation rather than tearing down the process.
        prev_handler = signal.getsignal(signal.SIGINT)

        def handler(signum, frame):
            cancel.set()

        signal.signal(signal.SIGINT, handler)
        try:
            self._agent.run(self._messages, cancel=cancel)
        except Exception as e:
            print(f"\n[error] {type(e).__name__}: {e}\n")
            # Roll back the user turn so it can be retried.
            if self._messages and self._messages[-1].role == "user":
                self._messages.pop()
        finally:
            signal.signal(signal.SIGINT, prev_handler)
            self._cancel = None

    def _print_config(self) -> None:
        c = self._cfg
        print(f"provider          {c.provider}")
        print(f"model             {self._provider.model}")
        print(f"max_tokens        {c.max_tokens}")
        print(f"temperature       {c.temperature}")
        print(f"max_agent_steps   {c.max_agent_steps}")
        if c.provider == "vertex":
            print(f"vertex.project    {c.vertex.project_id}")
            print(f"vertex.region     {c.vertex.region}")
            print(f"vault.url         {c.vault.url}")
            print(f"vault.namespace   {c.vault.namespace or '(none)'}")
            print(f"vault.token_path  {c.vault.token_path}")


_HELP_TEXT = """\
Commands:
  /help              show this message
  /quit, /exit       leave Loom
  /clear             clear conversation history (keeps system prompt)
  /tools             list available tools
  /skills            list discovered skills
  /system            print the active system prompt
  /history           print conversation history
  /config            show current configuration

While the model is generating, Ctrl+C aborts the response.
At the prompt, Ctrl+C exits Loom.
"""


# ----- entry point -----------------------------------------------------------


def _force_utf8_io() -> None:
    """Reconfigure stdout/stderr to UTF-8 so emojis and box-drawing don't crash
    the Windows console (which defaults to cp1252)."""
    for stream in (sys.stdout, sys.stderr):
        reconfigure = getattr(stream, "reconfigure", None)
        if callable(reconfigure):
            try:
                reconfigure(encoding="utf-8", errors="replace")
            except Exception:
                pass


_USER_TOML_TEMPLATE = """\
# Loom user-global configuration. Lives at ~/.loom/loom.toml.
# Project-local files at ./loom.toml or ./.loom/loom.toml override this.

[loom]
provider = "openrouter"
max_tokens = 4096
temperature = 0.4

# --- TLS (apply to Vault, Vertex, and OpenRouter) ---
# If you hit SSLError behind a corporate proxy, point this at its CA bundle:
# tls_ca_bundle = "/path/to/corporate-ca.pem"
# Last resort (NOT recommended):
# tls_verify = false

[openrouter]
# Set OPENROUTER_API_KEY in ~/.loom/.env (preferred) or your shell.
model = "anthropic/claude-opus-4.7"

[vertex]
project_id = ""
region = "us-east5"
model = "claude-opus-4-6"

[vault]
url = ""
namespace = ""
role_id = ""
# secret_id should come from VAULT_SECRET_ID in ~/.loom/.env, never this file.
secret_id = ""
token_path = ""
"""

_USER_ENV_TEMPLATE = """\
# Loom user-global secrets. Lives at ~/.loom/.env.
# Project-local ./.env overrides any values set here.

# OpenRouter
OPENROUTER_API_KEY=
OPENROUTER_MODEL=anthropic/claude-opus-4.7

# Vertex via Vault
VAULT_URL=
VAULT_NAMESPACE=
VAULT_ROLE_ID=
VAULT_SECRET_ID=
VAULT_TOKEN_PATH=
VERTEX_PROJECT_ID=
VERTEX_REGION=us-east5
VERTEX_MODEL=claude-opus-4-6

# TLS (uncomment if you're behind a TLS-intercepting proxy)
# LOOM_TLS_CA_BUNDLE=/etc/ssl/certs/corporate-ca.pem
# LOOM_TLS_VERIFY=false
"""


def _do_init(force: bool) -> int:
    """Create ~/.loom/{loom.toml,.env,skills/} so `loom` works from any cwd."""
    home = USER_HOME
    home.mkdir(parents=True, exist_ok=True)
    (home / "skills").mkdir(parents=True, exist_ok=True)

    targets = {
        home / "loom.toml": _USER_TOML_TEMPLATE,
        home / ".env": _USER_ENV_TEMPLATE,
    }
    for path, body in targets.items():
        if path.exists() and not force:
            print(f"  exists  {path} (use --force to overwrite)")
            continue
        path.write_text(body, encoding="utf-8")
        print(f"  wrote   {path}")

    print()
    print(f"User-global Loom config is at: {home}")
    print("Edit loom.toml + .env, then run `loom` from any directory.")
    return 0


def main(argv: Optional[list[str]] = None) -> int:
    _force_utf8_io()
    parser = argparse.ArgumentParser(prog="loom", description="Loom - a minimal AI coder.")
    sub = parser.add_subparsers(dest="cmd")

    p_init = sub.add_parser("init", help="Scaffold ~/.loom/ with a config template.")
    p_init.add_argument("--force", action="store_true", help="Overwrite existing files.")

    p_where = sub.add_parser("where", help="Show which config/.env files Loom would load.")

    parser.add_argument(
        "--config", type=Path, default=None, help="Path to loom.toml (overrides default search)."
    )
    parser.add_argument(
        "--env", type=Path, default=None, help="Path to .env file (overrides default search)."
    )
    parser.add_argument(
        "--provider",
        choices=["vertex", "openrouter"],
        default=None,
        help="Override the provider for this session.",
    )
    args = parser.parse_args(argv)

    if args.cmd == "init":
        return _do_init(force=args.force)

    if args.cmd == "where":
        env_files, toml_files = discover_config_paths(args.config, args.env)
        print("env files (lowest -> highest priority):")
        for p in env_files:
            print(f"  [{'x' if p.exists() else ' '}] {p}")
        print("toml files (lowest -> highest priority):")
        for p in toml_files:
            print(f"  [{'x' if p.exists() else ' '}] {p}")
        return 0

    cfg = load_config(toml_path=args.config, env_path=args.env)
    if args.provider:
        cfg.provider = args.provider

    errors = validate_for_provider(cfg)
    if errors:
        print("Loom configuration errors:")
        for e in errors:
            print(f"  - {e}")
        print()
        print("Hint: run `loom init` to create ~/.loom/, then edit ~/.loom/.env.")
        return 2

    _apply_tls_settings(cfg)

    cli = LoomCLI(cfg)
    try:
        cli.init()
        cli.loop()
    finally:
        cli.shutdown()
    return 0


if __name__ == "__main__":
    sys.exit(main())
