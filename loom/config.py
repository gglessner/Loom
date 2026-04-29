"""Configuration: loom.toml + environment variables.

Environment always wins over the TOML file so secrets can stay out of git.
"""

from __future__ import annotations

import os
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Optional

from dotenv import load_dotenv

if sys.version_info >= (3, 11):
    import tomllib  # type: ignore
else:
    import tomli as tomllib  # type: ignore


REPO_ROOT = Path(__file__).resolve().parent.parent
USER_HOME = Path.home() / ".loom"


@dataclass
class OpenRouterConfig:
    api_key: str = ""
    model: str = "anthropic/claude-opus-4.7"
    base_url: str = "https://openrouter.ai/api/v1"


@dataclass
class VertexConfig:
    project_id: str = ""
    region: str = "us-east5"
    model: str = "claude-opus-4-6"


@dataclass
class VaultConfig:
    url: str = ""
    namespace: str = ""
    role_id: str = ""
    secret_id: str = ""
    token_path: str = ""
    approle_mount: str = "approle"

    @property
    def configured(self) -> bool:
        return bool(self.url and self.role_id and self.secret_id and self.token_path)


@dataclass
class MCPServerConfig:
    name: str
    command: str
    args: list[str] = field(default_factory=list)
    env: dict[str, str] = field(default_factory=dict)


@dataclass
class LoomConfig:
    provider: str = "vertex"
    max_tokens: int = 4096
    temperature: float = 0.4
    max_agent_steps: int = 30
    # Project-local skills directory (relative to CWD). User-global skills
    # always come from ~/.loom/skills in addition to this.
    skills_dir: str = ".loom/skills"
    system_prompt: str = (
        "You are Loom, a focused coding assistant running in an agentic loop "
        "on the user's local machine. You have file, search, shell, and "
        "spreadsheet tools, plus any MCP tools the user has wired in, and a "
        "set of skill files for additional context. Prefer calling tools over "
        "guessing; you may call several tools across multiple turns and the "
        "results will be returned to you before you respond again. When the "
        "task is complete, reply to the user with a concise final answer and "
        "no further tool calls. Be terse, accurate, and platform-aware "
        "(this may be Windows or macOS)."
    )

    openrouter: OpenRouterConfig = field(default_factory=OpenRouterConfig)
    vertex: VertexConfig = field(default_factory=VertexConfig)
    vault: VaultConfig = field(default_factory=VaultConfig)
    mcp_servers: list[MCPServerConfig] = field(default_factory=list)


def _load_toml(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {}
    with path.open("rb") as f:
        return tomllib.load(f)


def _apply_section(target: Any, data: dict[str, Any]) -> None:
    for key, value in data.items():
        if hasattr(target, key):
            setattr(target, key, value)


def _merge_toml_into(cfg: LoomConfig, data: dict[str, Any]) -> None:
    if "loom" in data:
        _apply_section(cfg, data["loom"])
    if "openrouter" in data:
        _apply_section(cfg.openrouter, data["openrouter"])
    if "vertex" in data:
        _apply_section(cfg.vertex, data["vertex"])
    if "vault" in data:
        _apply_section(cfg.vault, data["vault"])
    for server in data.get("mcp_servers", []) or []:
        cfg.mcp_servers.append(
            MCPServerConfig(
                name=server["name"],
                command=server["command"],
                args=list(server.get("args", [])),
                env=dict(server.get("env", {})),
            )
        )


def discover_config_paths(
    toml_path: Optional[Path] = None,
    env_path: Optional[Path] = None,
) -> tuple[list[Path], list[Path]]:
    """Return ordered (env_files, toml_files) lists, lowest -> highest priority.

    Layering:
      1. ~/.loom/         (user-global defaults)
      2. ./.loom/         (project-local hidden dir)
      3. ./               (project-local, current directory)
      4. --config / --env (explicit override; highest priority)
    """
    cwd = Path.cwd()
    env_files = [
        USER_HOME / ".env",
        cwd / ".loom" / ".env",
        cwd / ".env",
    ]
    toml_files = [
        USER_HOME / "loom.toml",
        cwd / ".loom" / "loom.toml",
        cwd / "loom.toml",
    ]
    if env_path:
        env_files.append(env_path)
    if toml_path:
        toml_files.append(toml_path)
    return _dedupe(env_files), _dedupe(toml_files)


def _dedupe(paths: list[Path]) -> list[Path]:
    """Drop duplicates while preserving the first occurrence (lowest priority)."""
    seen: set[Path] = set()
    out: list[Path] = []
    for p in paths:
        try:
            key = p.resolve(strict=False)
        except OSError:
            key = p
        if key in seen:
            continue
        seen.add(key)
        out.append(p)
    return out


def load_config(
    toml_path: Optional[Path] = None,
    env_path: Optional[Path] = None,
) -> LoomConfig:
    """Resolve config. Layers user-global -> project-local -> explicit overrides.

    .env priority (highest -> lowest): OS env, --env, ./.env, ./.loom/.env,
    ~/.loom/.env. We apply load_dotenv with ``override=False`` in that order
    so anything already set in the environment - including the user's shell -
    is never silently clobbered by a config file.

    TOML priority (lowest -> highest): ~/.loom, ./.loom, ./, --config. Each
    layer merges on top of the previous so a project can override individual
    keys without having to re-state the whole config.
    """
    env_files, toml_files = discover_config_paths(toml_path, env_path)

    for env_file in reversed(env_files):
        if env_file.exists():
            load_dotenv(env_file, override=False)

    cfg = LoomConfig()
    for toml_file in toml_files:
        if toml_file.exists():
            _merge_toml_into(cfg, _load_toml(toml_file))

    # Env overrides
    cfg.provider = os.getenv("LOOM_PROVIDER", cfg.provider).lower()

    cfg.openrouter.api_key = os.getenv("OPENROUTER_API_KEY", cfg.openrouter.api_key)
    cfg.openrouter.model = os.getenv("OPENROUTER_MODEL", cfg.openrouter.model)

    cfg.vertex.project_id = os.getenv("VERTEX_PROJECT_ID", cfg.vertex.project_id)
    cfg.vertex.region = os.getenv("VERTEX_REGION", cfg.vertex.region)
    cfg.vertex.model = os.getenv("VERTEX_MODEL", cfg.vertex.model)

    cfg.vault.url = os.getenv("VAULT_URL", cfg.vault.url)
    cfg.vault.namespace = os.getenv("VAULT_NAMESPACE", cfg.vault.namespace)
    cfg.vault.role_id = os.getenv("VAULT_ROLE_ID", cfg.vault.role_id)
    cfg.vault.secret_id = os.getenv("VAULT_SECRET_ID", cfg.vault.secret_id)
    cfg.vault.token_path = os.getenv("VAULT_TOKEN_PATH", cfg.vault.token_path)

    return cfg


def validate_for_provider(cfg: LoomConfig) -> list[str]:
    """Return a list of human-readable errors. Empty list = ready to run."""
    errors: list[str] = []
    if cfg.provider == "openrouter":
        if not cfg.openrouter.api_key:
            errors.append("OPENROUTER_API_KEY is not set.")
        if not cfg.openrouter.model:
            errors.append("openrouter.model is empty.")
    elif cfg.provider == "vertex":
        if not cfg.vertex.project_id:
            errors.append("vertex.project_id (or VERTEX_PROJECT_ID) is not set.")
        if not cfg.vertex.region:
            errors.append("vertex.region is not set.")
        if not cfg.vertex.model:
            errors.append("vertex.model is not set.")
        if not cfg.vault.configured:
            errors.append(
                "Vault is not fully configured. Need url, role_id, secret_id, "
                "and token_path (set via [vault] in loom.toml or VAULT_* env vars)."
            )
    else:
        errors.append(f"Unknown provider: {cfg.provider!r} (expected 'vertex' or 'openrouter').")
    return errors
