"""Tests for per-plugin settings: prefixes, paths, modes, precedence, laziness."""

from __future__ import annotations

import os
import warnings
from pathlib import Path

import pytest
from pydantic import ValidationError
from pydantic_settings import BaseSettings

from mcp_plugin_gateway import settings as settings_module
from mcp_plugin_gateway.settings import (
    CONFIG_FILE,
    MODE_ENV_VAR,
    clear_settings_cache,
    env_prefix_for,
    get_settings,
    settings_class_for,
)


@pytest.fixture(autouse=True)
def _clean(monkeypatch: pytest.MonkeyPatch) -> None:
    """Isolate every test from the real environment and settings cache."""
    for key in list(os.environ):
        if key.startswith(("ALPHA_", "BETA_", "MY_PLUGIN_")):
            monkeypatch.delenv(key, raising=False)
    monkeypatch.delenv(MODE_ENV_VAR, raising=False)
    clear_settings_cache()
    yield
    clear_settings_cache()


def _config_file(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, text: str) -> Path:
    """Point the module's config file at ``tmp_path`` and write ``text``."""
    path = tmp_path / ".env"
    path.write_text(text)
    monkeypatch.setattr(settings_module, "CONFIG_FILE", path)
    return path


def _secrets_dir(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """Point the module's secrets directory at ``tmp_path/secrets``."""
    path = tmp_path / "secrets"
    path.mkdir()
    monkeypatch.setattr(settings_module, "SECRETS_DIR", path)
    return path


# --- 3.1 prefix isolation -------------------------------------------------


def test_prefix_is_upper_case_with_underscore() -> None:
    assert env_prefix_for("alpha") == "ALPHA_"
    assert env_prefix_for("my-plugin") == "MY_PLUGIN_"


def test_two_plugins_read_different_values(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _config_file(
        tmp_path, monkeypatch, "ALPHA_API_KEY=alpha-key\nBETA_API_KEY=beta-key\n"
    )

    class Config(BaseSettings):
        api_key: str = "default"

    alpha = get_settings("alpha", Config)
    beta = get_settings("beta", Config)

    assert alpha.api_key == "alpha-key"
    assert beta.api_key == "beta-key"


def test_unprefixed_variable_is_not_consumed(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _config_file(tmp_path, monkeypatch, "API_KEY=unprefixed\n")

    class Config(BaseSettings):
        api_key: str = "default"

    assert get_settings("alpha", Config).api_key == "default"


def test_settings_are_cached() -> None:
    class Config(BaseSettings):
        value: str = "default"

    first = get_settings("alpha", Config)
    second = get_settings("alpha", Config)

    assert first is second


# --- 3.2 local mode -------------------------------------------------------


def test_local_mode_reads_user_config_file(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _config_file(tmp_path, monkeypatch, "ALPHA_TOKEN=from-file\n")

    class Config(BaseSettings):
        token: str = "default"

    assert get_settings("alpha", Config).token == "from-file"


def test_plugin_local_env_file_is_ignored(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _config_file(tmp_path, monkeypatch, "ALPHA_TOKEN=from-user-config\n")
    plugin_dir = tmp_path / "plugin"
    plugin_dir.mkdir()
    (plugin_dir / ".env").write_text("ALPHA_TOKEN=from-plugin-dir\n")
    monkeypatch.chdir(plugin_dir)

    class Config(BaseSettings):
        token: str = "default"

    assert get_settings("alpha", Config).token == "from-user-config"


def test_config_path_is_resolved_not_literal() -> None:
    """A literal ``~`` is not expanded by pydantic-settings and reads nothing."""
    assert "~" not in str(CONFIG_FILE)
    assert CONFIG_FILE.is_absolute()
    assert CONFIG_FILE == Path.home() / ".config" / "mcp_plugin_gateway" / ".env"


def test_literal_tilde_path_would_silently_fail(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Regression: prove the trap the resolved path avoids."""
    _config_file(tmp_path, monkeypatch, "ALPHA_TOKEN=from-file\n")

    class Literal(BaseSettings):
        model_config = settings_module.SettingsConfigDict(
            env_prefix="ALPHA_", env_file="~/.config/mcp_plugin_gateway/.env"
        )
        token: str = "default"

    assert Literal().token == "default", "literal ~ must not be expanded"


# --- 3.3 container mode ---------------------------------------------------


def test_container_mode_reads_secret_file(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    secrets = _secrets_dir(tmp_path, monkeypatch)
    (secrets / "ALPHA_TOKEN").write_text("from-secret\n")
    monkeypatch.setenv(MODE_ENV_VAR, "container")

    class Config(BaseSettings):
        token: str = "default"

    assert get_settings("alpha", Config).token == "from-secret"


def test_container_mode_omits_env_file(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _config_file(tmp_path, monkeypatch, "ALPHA_TOKEN=from-file\n")
    _secrets_dir(tmp_path, monkeypatch)
    monkeypatch.setenv(MODE_ENV_VAR, "container")

    class Config(BaseSettings):
        token: str = "default"

    assert get_settings("alpha", Config).token == "default"


def test_missing_secrets_dir_only_warns(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(settings_module, "SECRETS_DIR", tmp_path / "absent")
    monkeypatch.setenv(MODE_ENV_VAR, "container")

    class Config(BaseSettings):
        token: str = "default"

    with warnings.catch_warnings(record=True) as caught:
        warnings.simplefilter("always")
        value = get_settings("alpha", Config).token

    assert value == "default"
    assert any("does not exist" in str(w.message) for w in caught)


# --- 3.4 precedence -------------------------------------------------------


def test_precedence_env_over_file_over_default(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _config_file(tmp_path, monkeypatch, "ALPHA_FROM_FILE=file\n")
    monkeypatch.setenv("ALPHA_FROM_ENV", "env")

    class Config(BaseSettings):
        from_env: str = "default"
        from_file: str = "default"
        untouched: str = "default"

    config = get_settings("alpha", Config)

    assert config.from_env == "env"
    assert config.from_file == "file"
    assert config.untouched == "default"


def test_secret_beats_config_file_in_container_mode(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Container mode omits env_file, so a mounted secret always wins."""
    _config_file(tmp_path, monkeypatch, "ALPHA_TOKEN=from-file\n")
    secrets = _secrets_dir(tmp_path, monkeypatch)
    (secrets / "ALPHA_TOKEN").write_text("from-secret\n")
    monkeypatch.setenv(MODE_ENV_VAR, "container")

    class Config(BaseSettings):
        token: str = "default"

    assert get_settings("alpha", Config).token == "from-secret"


def test_process_env_beats_file(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _config_file(tmp_path, monkeypatch, "ALPHA_TOKEN=from-file\n")
    monkeypatch.setenv("ALPHA_TOKEN", "from-env")

    class Config(BaseSettings):
        token: str = "default"

    assert get_settings("alpha", Config).token == "from-env"


# --- 3.5 laziness ---------------------------------------------------------


def test_class_definition_reads_nothing(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _config_file(tmp_path, monkeypatch, "ALPHA_TOKEN=from-file\n")

    class Config(BaseSettings):
        token: str

    cls = settings_class_for("alpha", Config)

    assert cls.model_config["env_prefix"] == "ALPHA_"


def test_import_succeeds_without_required_variable() -> None:
    class Config(BaseSettings):
        token: str

    cls = settings_class_for("alpha", Config)

    with pytest.raises(ValidationError) as excinfo:
        cls()

    assert "token" in str(excinfo.value)


def test_first_access_raises_validation_error() -> None:
    class Config(BaseSettings):
        token: str

    with pytest.raises(ValidationError):
        get_settings("alpha", Config)


# --- 3.6 secret name prefixing -------------------------------------------


def test_prefixed_secret_name_is_read(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    secrets = _secrets_dir(tmp_path, monkeypatch)
    (secrets / "ALPHA_TOKEN").write_text("prefixed\n")
    monkeypatch.setenv(MODE_ENV_VAR, "container")

    class Config(BaseSettings):
        token: str = "default"

    assert get_settings("alpha", Config).token == "prefixed"


def test_lowercase_secret_name_is_ignored(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    secrets = _secrets_dir(tmp_path, monkeypatch)
    (secrets / "alpha_token").write_text("lowercase\n")
    monkeypatch.setenv(MODE_ENV_VAR, "container")

    class Config(BaseSettings):
        token: str = "default"

    config = get_settings("alpha", Config)

    if _case_insensitive_filesystem(secrets):
        pytest.skip("filesystem is case-insensitive; alpha_token is ALPHA_TOKEN")
    assert config.token == "default"


def test_secret_lookup_requests_upper_case_name() -> None:
    """The custom source asks for the upper-cased name, so lowercase is ignored.

    Asserted against a recording directory rather than the real filesystem, which
    on macOS resolves ``alpha_token`` and ``ALPHA_TOKEN`` to the same file.
    """
    from mcp_plugin_gateway.settings import ExactCaseSecretsSource

    class Config(BaseSettings):
        token: str = "default"

    class _File:
        def __init__(self, name: str) -> None:
            self.name = name

        def read_text(self, encoding: str = "utf-8") -> str:
            return "upper\n"

    class _Dir:
        def iterdir(self) -> list[_File]:
            return [_File("alpha_token")]

    source = ExactCaseSecretsSource(Config, env_prefix="ALPHA_")
    source.secrets_paths = [_Dir()]

    value, key, _ = source.get_field_value(Config.model_fields["token"], "token")

    assert value is None, "lowercase alpha_token must not satisfy ALPHA_TOKEN"
    assert key == "token"


def _case_insensitive_filesystem(directory: Path) -> bool:
    """Return True when ``directory`` resolves names case-insensitively."""
    probe = directory / "CaseProbe"
    probe.write_text("x")
    try:
        return (directory / "caseprobe").exists()
    finally:
        probe.unlink()


# --- 3.6 build-time check -------------------------------------------------


def test_doctor_reports_ok_and_failures(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """``doctor`` instantiates settings per plugin and never raises."""
    from mcp_plugin_gateway import settings as settings_module

    class Good(BaseSettings):
        token: str = "default"

    class Bad(BaseSettings):
        token: str

    class _Spec:
        def __init__(self, name: str, base: type[BaseSettings]) -> None:
            self.name = name
            self._base = base

        def loader(self):
            return self._base()

    class _Registry:
        def __iter__(self):
            return iter([_Spec("good", Good), _Spec("bad", Bad)])

    monkeypatch.setattr(
        "mcp_plugin_gateway.registry.build_registry", lambda: _Registry()
    )

    report = settings_module.doctor()

    assert report["good"] == "ok"
    assert "token" in report["bad"]
    assert "ValidationError" in report["bad"]


def test_doctor_main_exits_non_zero_on_failure(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setattr(
        settings_module, "doctor", lambda: {"good": "ok", "bad": "ValidationError: x"}
    )

    assert settings_module._main() == 1
    out = capsys.readouterr().out
    assert "good: ok" in out
    assert "bad: ValidationError: x" in out


def test_doctor_main_exits_zero_when_healthy(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(settings_module, "doctor", lambda: {"good": "ok"})

    assert settings_module._main() == 0
