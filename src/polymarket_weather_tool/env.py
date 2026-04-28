from __future__ import annotations

import os
from pathlib import Path


def load_project_env(root: str | Path | None = None, *, override: bool = False) -> Path:
    """Load simple KEY=VALUE entries from a project-local .env file."""
    project_root = Path(root).resolve() if root else Path(__file__).resolve().parents[2]
    env_path = project_root / ".env"
    if not env_path.exists():
        return env_path

    for raw_line in env_path.read_text(encoding="utf-8-sig").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        key = key.strip()
        if not key:
            continue
        value = _strip_env_value(value.strip())
        if override or key not in os.environ:
            os.environ[key] = value
    return env_path


def _strip_env_value(value: str) -> str:
    if len(value) >= 2 and value[0] == value[-1] and value[0] in {'"', "'"}:
        return value[1:-1]
    return value
