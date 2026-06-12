"""SEC-006 — API_DOCS_ENABLED=false hides /openapi.json."""

from __future__ import annotations

import pytest


def test_docs_enabled_returns_true_by_default(monkeypatch):
    """_docs_enabled() must return True when API_DOCS_ENABLED is not set."""
    monkeypatch.delenv("API_DOCS_ENABLED", raising=False)
    # Import fresh to avoid module-level cache
    import importlib
    import src.api.main as main_mod
    importlib.reload(main_mod)
    assert main_mod._docs_enabled() is True


def test_docs_enabled_returns_false_when_env_false(monkeypatch):
    """_docs_enabled() must return False when API_DOCS_ENABLED=false."""
    monkeypatch.setenv("API_DOCS_ENABLED", "false")
    import importlib
    import src.api.main as main_mod
    importlib.reload(main_mod)
    assert main_mod._docs_enabled() is False


@pytest.mark.parametrize("value", ["0", "no", "off", "False", "FALSE"])
def test_docs_enabled_returns_false_for_falsy_strings(monkeypatch, value):
    monkeypatch.setenv("API_DOCS_ENABLED", value)
    import importlib
    import src.api.main as main_mod
    importlib.reload(main_mod)
    assert main_mod._docs_enabled() is False
