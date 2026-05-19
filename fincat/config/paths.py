"""Runtime path helpers derived from the active config context."""

from __future__ import annotations

from pathlib import Path

from fincat.config.loader import get_config_path
from fincat.utils.helpers import ensure_dir


def get_data_dir() -> Path:
    """Return the instance-level runtime data directory."""
    return ensure_dir(get_config_path().parent)


def get_runtime_subdir(name: str) -> Path:
    """Return a named runtime subdirectory under the instance data dir."""
    return ensure_dir(get_data_dir() / name)


def get_media_dir(channel: str | None = None) -> Path:
    """Return the media directory, optionally namespaced per channel."""
    base = get_runtime_subdir("media")
    return ensure_dir(base / channel) if channel else base


def get_cron_dir() -> Path:
    """Return the cron storage directory."""
    return get_runtime_subdir("cron")


def get_logs_dir() -> Path:
    """Return the logs directory."""
    return get_runtime_subdir("logs")


def get_workspace_path(workspace: str | None = None) -> Path:
    """Resolve and ensure the agent workspace path."""
    path = Path(workspace).expanduser() if workspace else Path.home() / ".fincat" / "workspace"
    return ensure_dir(path)


def is_default_workspace(workspace: str | Path | None) -> bool:
    """Return whether a workspace resolves to fincat's default workspace path."""
    current = Path(workspace).expanduser() if workspace is not None else Path.home() / ".fincat" / "workspace"
    default = Path.home() / ".fincat" / "workspace"
    return current.resolve(strict=False) == default.resolve(strict=False)


def get_cli_history_path() -> Path:
    """Return the shared CLI history file path."""
    return Path.home() / ".fincat" / "history" / "cli_history"


def get_bridge_install_dir() -> Path:
    """Return the shared WhatsApp bridge installation directory."""
    return Path.home() / ".fincat" / "bridge"


def get_legacy_sessions_dir() -> Path:
    """Return the legacy global session directory used for migration fallback."""
    return Path.home() / ".fincat" / "sessions"


def get_resources_dir() -> Path:
    """Return the L1 resources directory (~/.fincat/resources/)."""
    return ensure_dir(Path.home() / ".fincat" / "resources")


def get_vector_dir() -> Path:
    """Return the FAISS vector index directory (~/.fincat/vector/)."""
    return ensure_dir(Path.home() / ".fincat" / "vector")


def get_memory_db_path() -> Path:
    """Return the memory SQLite database path (~/.fincat/memory.db)."""
    return Path.home() / ".fincat" / "memory.db"


def get_knowledge_dir() -> Path:
    """Return the private knowledge base directory (~/.fincat/workspace/knowledge/)."""
    base = get_workspace_path() / "knowledge"
    ensure_dir(base / "pdfs")
    ensure_dir(base / "vectors")
    return base
