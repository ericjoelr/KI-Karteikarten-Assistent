"""Central, env-driven configuration of the backend.

Everything adjustable lives in one settings object; `get_settings()` is cached,
so a run always sees the same values. The system prompt is composed here too,
because which parts of it apply is a matter of configuration.
"""

from functools import lru_cache
from pathlib import Path
from typing import Literal

from dotenv import set_key
from pydantic_settings import BaseSettings, SettingsConfigDict

# src/backend/config.py -> parents[2] == project root
PROJECT_ROOT = Path(__file__).resolve().parents[2]
# The prompts live next to the code that reads them.
BACKEND_DIR = Path(__file__).resolve().parent
# Read on start and written back when the provider is switched in the UI.
ENV_FILE = PROJECT_ROOT / ".env"


class Settings(BaseSettings):
    """All settings of the application, overridable via environment or .env."""

    model_config = SettingsConfigDict(env_file=ENV_FILE, extra="ignore")

    # --- LLM provider ---
    # The active one, where every request goes. Switched in the UI and written
    # back to .env, so the choice survives a restart.
    provider: Literal["cloud", "llamacpp", "mlx"] = "cloud"
    provider_api_key: str = ""
    provider_base_url: str = ""
    default_model: str = ""
    # The cloud slot is kept apart, so switching to a local server and back
    # does not lose URL and key.
    cloud_base_url: str = "https://generativelanguage.googleapis.com/v1beta/openai/"
    cloud_api_key: str = ""
    # Local servers the backend starts itself: executable and port.
    llamacpp_server: str = "llama-server"
    llamacpp_port: int = 9931
    mlx_server: str = "mlx_vlm.server"
    mlx_port: int = 9932
    # One sub folder per LoRA adapter (the DL repo's models/ folder).
    mlx_adapters_dir: Path | None = None
    mlx_adapter: str = ""  # the active adapter's folder name

    # --- Graphics ---
    # Default of the graphics mode; individual requests may override it.
    graphics_enabled: bool = True

    # --- Anki ---
    anki_server: str = "http://127.0.0.1:8765"

    # --- Paths ---
    pdf_folder: Path = PROJECT_ROOT / "data" / "pdf"
    prompts_dir: Path = BACKEND_DIR / "prompts"
    cards_user_dir: Path = PROJECT_ROOT / "data" / "cards" / "user"

    def _prompt(self, name: str) -> str:
        # Double the backslashes so LaTeX survives inside the prompts.
        return (self.prompts_dir / name).read_text(encoding="utf-8").replace("\\", "\\\\")

    def graphics_mode(self, requested: bool | None) -> bool:
        """The graphics mode of a run: without a value the configured default."""
        return self.graphics_enabled if requested is None else requested

    def save_provider(self, **values: str) -> None:
        """Sets the given provider fields and writes them to .env as well."""
        for name, value in values.items():
            setattr(self, name, value)
            set_key(ENV_FILE, name.upper(), value)

    def system_prompt(self, *, graphics: bool) -> str:
        """Base prompt; in graphics mode plus the GRAFIKEN section."""
        prompt = self._prompt("system_prompt.txt").rstrip()
        if graphics:
            prompt += "\n\n" + self._prompt("graphics_prompt.txt").rstrip()
        return prompt


@lru_cache
def get_settings() -> Settings:
    """The settings of this process; cached, so they are read exactly once."""
    return Settings()
