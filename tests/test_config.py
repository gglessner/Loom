"""Config + provider validation tests."""

from __future__ import annotations

import os
from pathlib import Path

import pytest

from loom.config import (
    LoomConfig,
    load_config,
    resolve_tls_verify,
    resolve_vault_tls_verify,
    validate_for_provider,
)


def _clear_loom_env(monkeypatch: pytest.MonkeyPatch) -> None:
    """Wipe any LOOM_/VAULT_/VERTEX_/OPENROUTER_ env vars so tests don't
    pick up settings from the developer's shell."""
    for key in list(os.environ):
        if (
            key.startswith("LOOM_")
            or key.startswith("VAULT_")
            or key.startswith("VERTEX_")
            or key == "OPENROUTER_API_KEY"
            or key == "OPENROUTER_MODEL"
        ):
            monkeypatch.delenv(key, raising=False)


def test_validate_openrouter_requires_api_key(monkeypatch: pytest.MonkeyPatch) -> None:
    _clear_loom_env(monkeypatch)
    cfg = LoomConfig(provider="openrouter")
    errors = validate_for_provider(cfg)
    assert any("OPENROUTER_API_KEY" in e for e in errors)


def test_validate_vertex_requires_vault(monkeypatch: pytest.MonkeyPatch) -> None:
    _clear_loom_env(monkeypatch)
    cfg = LoomConfig(provider="vertex")
    cfg.vertex.project_id = "proj"
    cfg.vertex.region = "us-east5"
    cfg.vertex.model = "claude-opus-4-6"
    errors = validate_for_provider(cfg)
    assert any("Vault" in e for e in errors)


def test_validate_vertex_happy_path() -> None:
    cfg = LoomConfig(provider="vertex")
    cfg.vertex.project_id = "proj"
    cfg.vertex.region = "us-east5"
    cfg.vertex.model = "claude-opus-4-6"
    cfg.vault.url = "https://vault.example"
    cfg.vault.role_id = "r"
    cfg.vault.secret_id = "s"
    cfg.vault.token_path = "gcp/token/example"
    assert validate_for_provider(cfg) == []


def test_env_overrides_toml(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    _clear_loom_env(monkeypatch)
    toml = tmp_path / "loom.toml"
    toml.write_text(
        "[loom]\nprovider = 'openrouter'\n[openrouter]\nmodel = 'from-toml'\n",
        encoding="utf-8",
    )
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("OPENROUTER_API_KEY", "secret-key")
    monkeypatch.setenv("OPENROUTER_MODEL", "from-env")

    cfg = load_config(toml_path=toml)
    assert cfg.provider == "openrouter"
    assert cfg.openrouter.api_key == "secret-key"
    assert cfg.openrouter.model == "from-env"


def test_invalid_provider() -> None:
    cfg = LoomConfig(provider="bogus")
    errors = validate_for_provider(cfg)
    assert any("Unknown provider" in e for e in errors)


def test_project_toml_overrides_user_toml(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _clear_loom_env(monkeypatch)
    user_home = tmp_path / "user"
    project = tmp_path / "project"
    (user_home).mkdir()
    project.mkdir()

    (user_home / "loom.toml").write_text(
        "[loom]\nprovider = 'vertex'\nmax_tokens = 1000\n"
        "[openrouter]\nmodel = 'user-model'\n",
        encoding="utf-8",
    )
    (project / "loom.toml").write_text(
        "[loom]\nmax_tokens = 9999\n", encoding="utf-8"
    )

    monkeypatch.setattr("loom.config.USER_HOME", user_home)
    monkeypatch.chdir(project)

    cfg = load_config()
    assert cfg.provider == "vertex"  # came from user-global
    assert cfg.max_tokens == 9999  # project overrode user
    assert cfg.openrouter.model == "user-model"  # untouched layer kept


def test_os_env_beats_dotenv(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _clear_loom_env(monkeypatch)
    user_home = tmp_path / "user"
    project = tmp_path / "project"
    user_home.mkdir()
    project.mkdir()

    (user_home / ".env").write_text("OPENROUTER_API_KEY=from-user-env\n", encoding="utf-8")
    (project / ".env").write_text("OPENROUTER_API_KEY=from-project-env\n", encoding="utf-8")

    monkeypatch.setattr("loom.config.USER_HOME", user_home)
    monkeypatch.chdir(project)
    monkeypatch.setenv("OPENROUTER_API_KEY", "from-shell")

    cfg = load_config()
    assert cfg.openrouter.api_key == "from-shell"


def test_tls_verify_defaults_to_true() -> None:
    cfg = LoomConfig()
    assert resolve_tls_verify(cfg) is True


def test_tls_ca_bundle_wins_over_verify_flag() -> None:
    cfg = LoomConfig(tls_verify=True, tls_ca_bundle="/etc/ssl/corp.pem")
    assert resolve_tls_verify(cfg) == "/etc/ssl/corp.pem"


def test_tls_verify_false_propagates() -> None:
    cfg = LoomConfig(tls_verify=False)
    assert resolve_tls_verify(cfg) is False


def test_loom_tls_verify_env_parses_falsey(monkeypatch: pytest.MonkeyPatch) -> None:
    _clear_loom_env(monkeypatch)
    for value in ("false", "False", "0", "no", "off"):
        monkeypatch.setenv("LOOM_TLS_VERIFY", value)
        cfg = load_config()
        assert cfg.tls_verify is False, f"value {value!r} should disable verify"
    for value in ("true", "1", "yes"):
        monkeypatch.setenv("LOOM_TLS_VERIFY", value)
        cfg = load_config()
        assert cfg.tls_verify is True, f"value {value!r} should enable verify"


def test_loom_tls_ca_bundle_env(monkeypatch: pytest.MonkeyPatch) -> None:
    _clear_loom_env(monkeypatch)
    monkeypatch.setenv("LOOM_TLS_CA_BUNDLE", "/tmp/ca.pem")
    cfg = load_config()
    assert cfg.tls_ca_bundle == "/tmp/ca.pem"
    assert resolve_tls_verify(cfg) == "/tmp/ca.pem"


def test_vault_tls_inherits_global_by_default() -> None:
    cfg = LoomConfig(tls_verify=False)
    assert resolve_vault_tls_verify(cfg) is False
    cfg = LoomConfig(tls_ca_bundle="/etc/ssl/corp.pem")
    assert resolve_vault_tls_verify(cfg) == "/etc/ssl/corp.pem"


def test_vault_tls_verify_overrides_global() -> None:
    cfg = LoomConfig(tls_verify=True)  # global stays strict
    cfg.vault.tls_verify = False
    assert resolve_vault_tls_verify(cfg) is False
    # but the global resolver is unaffected
    assert resolve_tls_verify(cfg) is True


def test_vault_tls_ca_bundle_overrides_global() -> None:
    cfg = LoomConfig(tls_ca_bundle="/etc/ssl/global.pem")
    cfg.vault.tls_ca_bundle = "/etc/ssl/internal-vault.pem"
    assert resolve_vault_tls_verify(cfg) == "/etc/ssl/internal-vault.pem"
    assert resolve_tls_verify(cfg) == "/etc/ssl/global.pem"


def test_vault_tls_env_overrides(monkeypatch: pytest.MonkeyPatch) -> None:
    _clear_loom_env(monkeypatch)
    monkeypatch.setenv("VAULT_TLS_VERIFY", "false")
    cfg = load_config()
    assert cfg.vault.tls_verify is False
    assert resolve_vault_tls_verify(cfg) is False
    # global flag wasn't touched
    assert cfg.tls_verify is True
    assert resolve_tls_verify(cfg) is True

    monkeypatch.setenv("VAULT_TLS_CA_BUNDLE", "/tmp/internal-vault.pem")
    cfg = load_config()
    assert resolve_vault_tls_verify(cfg) == "/tmp/internal-vault.pem"


def test_project_dotenv_beats_user_dotenv(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _clear_loom_env(monkeypatch)
    user_home = tmp_path / "user"
    project = tmp_path / "project"
    user_home.mkdir()
    project.mkdir()

    (user_home / ".env").write_text("OPENROUTER_API_KEY=from-user-env\n", encoding="utf-8")
    (project / ".env").write_text("OPENROUTER_API_KEY=from-project-env\n", encoding="utf-8")

    monkeypatch.setattr("loom.config.USER_HOME", user_home)
    monkeypatch.chdir(project)

    cfg = load_config()
    assert cfg.openrouter.api_key == "from-project-env"
