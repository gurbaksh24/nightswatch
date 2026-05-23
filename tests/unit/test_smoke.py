"""Smoke tests. Verify the package imports and basic plumbing works."""

import pytest


@pytest.mark.unit
def test_package_imports() -> None:
    import ai_sre
    import ai_sre.config
    import ai_sre.exceptions
    import ai_sre.main  # noqa: F401


@pytest.mark.unit
def test_settings_load() -> None:
    from ai_sre.config import get_settings
    settings = get_settings()
    assert settings.env in ("local", "dev", "staging", "prod")
