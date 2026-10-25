import pytest

from backend.config import get_settings


@pytest.fixture(autouse=True)
def temp_card_dirs(tmp_path, monkeypatch):
    """Redirects the card store into a temporary directory.

    get_settings is cached -> clear the cache before and after every test,
    otherwise the redirect does not take effect, or sticks around for later
    tests. The graphics mode is pinned to its code default, so a local .env
    that switches it off (fine-tuned model) cannot flip the tests;
    model and API key are pinned so the tests pass on a fresh clone without a .env.
    """
    monkeypatch.setenv("CARDS_USER_DIR", str(tmp_path / "user"))
    monkeypatch.setenv("GRAPHICS_ENABLED", "true")
    monkeypatch.setenv("DEFAULT_MODEL", "test-model")
    monkeypatch.setenv("PROVIDER_API_KEY", "test-key")
    get_settings.cache_clear()
    yield
    get_settings.cache_clear()
