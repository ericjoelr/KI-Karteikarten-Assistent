from unittest.mock import patch

import pytest
from fastapi.testclient import TestClient

from backend import config
from backend.config import get_settings
from backend.main import app
from backend.schemas import BatchProgress

client = TestClient(app)


@pytest.fixture
def env_file(tmp_path, monkeypatch):
    """Redirects the write-back of the provider choice away from the real .env."""
    path = tmp_path / ".env"
    path.touch()
    monkeypatch.setattr(config, "ENV_FILE", path)
    return path


def test_activating_a_cloud_model_switches_settings_and_persists_it(env_file):
    resp = client.post("/providers/activate", json={
        "provider": "cloud", "model": "gemini-x",
        "base_url": "https://cloud.example/v1", "api_key": "secret",
    })

    assert resp.status_code == 200
    assert resp.json()["model"] == "gemini-x"
    settings = get_settings()
    assert (settings.provider, settings.default_model) == ("cloud", "gemini-x")
    assert settings.provider_base_url == "https://cloud.example/v1"
    written = env_file.read_text()
    assert "DEFAULT_MODEL='gemini-x'" in written
    assert "CLOUD_API_KEY='secret'" in written


def test_cloud_activation_without_key_keeps_the_stored_one(env_file):
    client.post("/providers/activate", json={
        "provider": "cloud", "model": "a", "api_key": "secret",
    })
    client.post("/providers/activate", json={"provider": "cloud", "model": "b"})

    assert get_settings().provider_api_key == "secret"


def test_mlx_lists_only_folders_with_adapter_weights(tmp_path, monkeypatch):
    (tmp_path / "lora-a").mkdir()
    (tmp_path / "lora-a" / "adapters.safetensors").touch()
    (tmp_path / "notes").mkdir()
    monkeypatch.setenv("MLX_ADAPTERS_DIR", str(tmp_path))
    get_settings.cache_clear()

    resp = client.post("/providers/models", json={"provider": "mlx"})

    assert resp.json() == {"models": ["lora-a"]}


def test_mlx_listing_without_adapter_dir_is_a_readable_error(monkeypatch):
    monkeypatch.setenv("MLX_ADAPTERS_DIR", "/does/not/exist")
    get_settings.cache_clear()

    resp = client.post("/providers/models", json={"provider": "mlx"})

    assert resp.status_code == 502
    assert "MLX_ADAPTERS_DIR" in resp.json()["detail"]


def test_switching_the_model_is_refused_during_a_batch_run(env_file):
    running = BatchProgress(pdf_name="x.pdf", total=3)
    with patch("backend.main.batch.status", return_value=running):
        resp = client.post("/providers/activate", json={"provider": "cloud", "model": "b"})

    assert resp.status_code == 409
    assert get_settings().default_model != "b"
