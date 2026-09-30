import re
from pathlib import Path
from typing import Literal

import yaml
from pydantic import BaseModel, ConfigDict, Field

from prism.errors import PrismError


class Settings(BaseModel):
    model_config = ConfigDict(extra="forbid")
    data: str = "data"
    model_path: str = ".cache/model"
    synonyms_path: str | None = "examples/synonyms.json"
    host: str = "127.0.0.1"
    port: int = Field(default=8080, ge=1, le=65535)
    workers: int = Field(default=4, ge=1, le=64)
    role: Literal["local", "shard", "coordinator"] = "local"
    topology: str | None = None
    index_name: str = "demo"
    shard_id: int | None = None
    snapshot_path: str | None = None
    epoch_path: str | None = None


def load_settings(path=None):
    if path is None:
        return Settings()
    return Settings.model_validate(yaml.safe_load(Path(path).read_text(encoding="utf-8")) or {})


def index_path(settings, name):
    if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_-]{0,63}", name):
        raise PrismError("INVALID_INDEX_NAME", "Use a safe alphanumeric index name")
    return Path(settings.data) / name
