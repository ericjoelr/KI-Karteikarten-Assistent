"""The three LLM providers the UI switches between: their models and activating one.

llama.cpp and mlx run as local servers the backend starts itself (ADR-0009):
llama.cpp once, in router mode, loading any cached model on demand; mlx once per
LoRA adapter, because the adapter is fixed when the server starts.
"""

import atexit
import json
import subprocess
import time

import requests
from openai import OpenAI, OpenAIError

from backend.config import get_settings

# =========================================================
# Local servers
# =========================================================
# Local servers check no key, but the OpenAI client insists on one.
LOCAL_API_KEY = "local"

# The servers this process started, by provider.
_servers: dict[str, subprocess.Popen] = {}


def _local_url(port: int) -> str:
    return f"http://127.0.0.1:{port}/v1"


def _is_running(port: int) -> bool:
    try:
        return requests.get(f"{_local_url(port)}/models", timeout=1).ok
    except requests.RequestException:
        return False


def _stop(provider: str) -> None:
    process = _servers.pop(provider, None)
    if process:
        process.terminate()
        process.wait(timeout=10)


def _start(provider: str, command: list[str], port: int, wait_seconds: float) -> None:
    try:
        process = subprocess.Popen(command, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    except OSError as exc:
        raise ProviderError(f"Server konnte nicht gestartet werden: {exc}") from exc
    _servers[provider] = process

    # German: the messages below are shown in the UI.
    deadline = time.monotonic() + wait_seconds
    while time.monotonic() < deadline:
        if _is_running(port):
            return
        if process.poll() is not None:
            _servers.pop(provider)
            raise ProviderError(
                f"Der Server hat sich beendet – Port {port} belegt oder Modell nicht ladbar?"
            )
        time.sleep(1)
    raise ProviderError("Der Server antwortet nicht – Modell zu groß oder Pfad falsch?")


# Servers outliving the backend would hold their model in memory for nothing.
atexit.register(lambda: [_stop(provider) for provider in list(_servers)])


# =========================================================
# Providers
# =========================================================
class ProviderError(Exception):
    """A provider cannot be reached or started; the message is for the UI."""


def active() -> dict:
    """The active provider and model, and whether its server answers."""
    settings = get_settings()
    ready = {
        "cloud": True,
        "llamacpp": _is_running(settings.llamacpp_port),
        "mlx": _is_running(settings.mlx_port),
    }[settings.provider]
    return {
        "provider": settings.provider,
        # For mlx the adapter is what tells the models apart, not the base model.
        "model": settings.mlx_adapter if settings.provider == "mlx" else settings.default_model,
        "ready": ready,
        "cloud_base_url": settings.cloud_base_url,
    }


def list_models(provider: str, *, base_url: str | None = None, api_key: str | None = None) -> list[str]:
    """The models a provider offers; starts the llama.cpp server if it is not running.

    base_url/api_key: cloud only; without a value the stored cloud slot is used.
    """
    settings = get_settings()

    if provider == "cloud":
        client = OpenAI(
            base_url=base_url or settings.cloud_base_url,
            api_key=api_key or settings.cloud_api_key,
        )
        try:
            # Gemini prefixes its ids; the chat endpoint takes them without.
            return sorted(m.id.removeprefix("models/") for m in client.models.list())
        except OpenAIError as exc:
            raise ProviderError(f"Cloud-Anbieter nicht erreichbar: {exc}") from exc

    if provider == "llamacpp":
        port = settings.llamacpp_port
        if not _is_running(port):
            _start("llamacpp", [settings.llamacpp_server, "--port", str(port)], port, 30)
        models = requests.get(f"{_local_url(port)}/models", timeout=10).json()["data"]
        # Only models that can see the slide image.
        return [
            m["id"] for m in models
            if "image" in m.get("architecture", {}).get("input_modalities", ["image"])
        ]

    if settings.mlx_adapters_dir is None or not settings.mlx_adapters_dir.is_dir():
        raise ProviderError("MLX_ADAPTERS_DIR ist nicht gesetzt oder existiert nicht.")
    return sorted(
        p.name for p in settings.mlx_adapters_dir.iterdir()
        if (p / "adapters.safetensors").exists()
    )


def activate(provider: str, model: str, *, base_url: str | None = None, api_key: str | None = None) -> None:
    """Makes a model the one every request goes to, starting its server if needed."""
    settings = get_settings()

    if provider == "cloud":
        base_url = base_url or settings.cloud_base_url
        api_key = api_key or settings.cloud_api_key
        settings.save_provider(
            provider=provider, provider_base_url=base_url, provider_api_key=api_key,
            default_model=model, cloud_base_url=base_url, cloud_api_key=api_key,
        )
        return

    # One local model at a time: two side by side do not fit into the 24 GB of
    # unified memory. Only activating stops the other server — listing the
    # llama.cpp models starts an idle router that holds no model yet.
    if provider == "llamacpp":
        _stop("mlx")
        port = settings.llamacpp_port
        if not _is_running(port):
            _start(provider, [settings.llamacpp_server, "--port", str(port)], port, 30)
        # The router loads the model with its first request.
        settings.save_provider(
            provider=provider, provider_base_url=_local_url(port),
            provider_api_key=LOCAL_API_KEY, default_model=model,
        )
        return

    # mlx: restart even when running — the adapter is fixed at start. The
    # request has to name the base model, then the server applies the adapter.
    port = settings.mlx_port
    adapter = settings.mlx_adapters_dir / model
    base_model = json.loads((adapter / "run_config.json").read_text())["model"]
    _stop("llamacpp")
    _stop(provider)
    _start(provider, [
        settings.mlx_server, "--host", "127.0.0.1", "--port", str(port),
        "--model", base_model, "--adapter-path", str(adapter), "--max-tokens", "1024",
    ], port, 180)
    settings.save_provider(
        provider=provider, provider_base_url=_local_url(port),
        provider_api_key=LOCAL_API_KEY, default_model=base_model, mlx_adapter=model,
    )
