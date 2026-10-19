"""Adapters for obtaining the current typhoon from replaceable data sources."""

from __future__ import annotations

import json
from pathlib import Path

from .config import DEFAULT_CURRENT_TYPHOON
from .schema import normalize_current_typhoon


class CurrentTyphoonFileProvider:
    """Read the current typhoon from a JSON file supplied by an expert system."""

    def __init__(self, path: str | Path = DEFAULT_CURRENT_TYPHOON):
        self.path = Path(path).resolve()

    @property
    def available(self) -> bool:
        return self.path.exists()

    def get_current_typhoon(self) -> dict:
        if not self.available:
            raise FileNotFoundError(
                f"当前台风文件不存在: {self.path}。"
                "请通过 /api/typhoon/similar 直接提交 JSON，或将专家数据写入该文件。"
            )
        with self.path.open("r", encoding="utf-8") as stream:
            return normalize_current_typhoon(json.load(stream))

    def status(self) -> dict:
        return {"available": self.available, "path": str(self.path)}
