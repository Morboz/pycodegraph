"""Configuration for CodeGraph projects."""

from __future__ import annotations

import json
from dataclasses import dataclass, field, asdict
from pathlib import Path
from typing import Optional

from .types import Language


CODEGRAPH_DIR = ".codegraph"


@dataclass
class CodeGraphConfig:
    version: int = 1
    root_dir: str = "."
    db_url: Optional[str] = None
    include: list[str] = field(default_factory=lambda: [
        "**/*.py",
        "**/*.ts", "**/*.tsx", "**/*.js", "**/*.jsx",
        "**/*.go", "**/*.rs", "**/*.java",
        "**/*.c", "**/*.h", "**/*.cpp", "**/*.hpp", "**/*.cc", "**/*.cxx",
        "**/*.cs", "**/*.php", "**/*.rb", "**/*.swift",
        "**/*.kt", "**/*.kts", "**/*.dart",
        "**/*.scala", "**/*.sc",
    ])
    exclude: list[str] = field(default_factory=lambda: [
        "**/.git/**",
        "**/node_modules/**", "**/vendor/**", "**/Pods/**",
        "**/dist/**", "**/build/**", "**/out/**", "**/bin/**",
        "**/target/**",
        "**/__pycache__/**", "**/.venv/**", "**/venv/**",
        "**/site-packages/**", "**/.pytest_cache/**", "**/.mypy_cache/**",
        "**/*.min.js", "**/*.bundle.js",
        "**/.gradle/**", "**/.idea/**",
        "**/coverage/**",
    ])
    languages: list[str] = field(default_factory=list)
    max_file_size: int = 1024 * 1024  # 1MB
    extract_docstrings: bool = True
    track_call_sites: bool = True


def get_config_path(project_root: str | Path) -> Path:
    return Path(project_root) / CODEGRAPH_DIR / "config.json"


def get_db_path(project_root: str | Path) -> Path:
    return Path(project_root) / CODEGRAPH_DIR / "codegraph.db"


def get_db_url(project_root: str | Path, config: Optional[CodeGraphConfig] = None) -> str:
    """Return a SQLAlchemy database URL.

    Uses config.db_url if set, otherwise falls back to local SQLite.
    """
    if config and config.db_url:
        return config.db_url
    db_path = get_db_path(project_root)
    return f"sqlite:///{db_path}"


def save_config(project_root: str | Path, config: CodeGraphConfig) -> None:
    config_path = get_config_path(project_root)
    config_path.parent.mkdir(parents=True, exist_ok=True)
    with open(config_path, "w") as f:
        json.dump(asdict(config), f, indent=2)


def load_config(project_root: str | Path) -> CodeGraphConfig:
    config_path = get_config_path(project_root)
    with open(config_path) as f:
        data = json.load(f)
    return CodeGraphConfig(**{k: v for k, v in data.items() if k in CodeGraphConfig.__dataclass_fields__})


def create_default_config(root_dir: str) -> CodeGraphConfig:
    return CodeGraphConfig(root_dir=root_dir)
