from __future__ import annotations

import os
from pathlib import Path
from typing import Optional, Set, List
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Application settings loaded from environment variables."""

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="ignore"
    )

    # NVIDIA NIM API
    nvidia_api_key: str = ""
    nim_model: str = "meta/llama-3.1-70b-instruct"
    nim_base_url: str = "https://integrate.api.nvidia.com/v1"
    nim_temperature: float = 0.2
    nim_max_tokens: Optional[int] = None
    request_timeout: float = 60.0

    # MCP Configuration
    mcp_config_path: Path = Path("mcp_config.json")

    # Workspace
    workspace_root: Path = Path(".").resolve()
    # Only safe, non-interpreter commands. Interpreters removed to prevent
    # arbitrary code execution via arguments (e.g., python -c "malicious code")
    allowed_commands: Set[str] = {"ls", "cat", "grep", "find", "git", "cargo", "go", "pip", "pip3"}

    # Chat Storage
    chats_dir: Path = Path("chats")

    # Streamlit
    streamlit_port: int = 8501


_settings: Optional[Settings] = None


def get_settings() -> Settings:
    """Get singleton settings instance."""
    global _settings
    if _settings is None:
        _settings = Settings()
        # Ensure directories exist
        _settings.workspace_root.mkdir(parents=True, exist_ok=True)
        _settings.chats_dir.mkdir(parents=True, exist_ok=True)
    return _settings


def reset_settings() -> None:
    """Reset settings (for testing)."""
    global _settings
    _settings = None