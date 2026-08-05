from backend.config.settings import get_settings


def test_bensim_env_alias_precedes_legacy(monkeypatch):
    get_settings.cache_clear()
    monkeypatch.setenv("BENSIM_APP_NAME", "Bensim Alias API")
    monkeypatch.setenv("OPENTERMINALUI_APP_NAME", "Legacy API")

    settings = get_settings()

    assert settings.app_name == "Bensim Alias API"
    get_settings.cache_clear()


def test_legacy_env_still_supported(monkeypatch):
    get_settings.cache_clear()
    monkeypatch.delenv("BENSIM_SQLITE_URL", raising=False)
    monkeypatch.setenv("OPENTERMINALUI_SQLITE_URL", "sqlite:///:memory:")

    settings = get_settings()

    assert settings.sqlite_url == "sqlite:///:memory:"
    get_settings.cache_clear()
