"""Adapters for obtaining the current typhoon from replaceable data sources."""

from __future__ import annotations

import json
from pathlib import Path

from .config import DEFAULT_CURRENT_TYPHOON
from .schema import normalize_current_typhoon, TyphoonInputError


class CurrentTyphoonFileProvider:
    """Read the current typhoon from a JSON file supplied by an expert system."""

    def __init__(self, path: str | Path = DEFAULT_CURRENT_TYPHOON):
        self.path = Path(path).resolve()

    @property
    def available(self) -> bool:
        return self.path.exists()

    def get_current_typhoon(self, *, storm_id=None, agency=None, track_kind=None) -> dict:
        if not self.available:
            raise FileNotFoundError(
                f"当前台风文件不存在: {self.path}。"
                "请通过 /api/typhoon/similar 直接提交 JSON，或将专家数据写入该文件。"
            )
        with self.path.open("r", encoding="utf-8") as stream:
            payload=normalize_current_typhoon(json.load(stream))
        if storm_id is not None and str(storm_id)!=payload['sid']:
            raise TyphoonInputError('文件中的台风身份与所选storm_id不同，不改用其他台风。')
        if agency or track_kind not in (None,'','observed'):
            raise TyphoonInputError('文件来源未提供分机构预报路径；请直接提交对应路径JSON，不将文件已有路径伪作机构预报。')
        payload['track_context']={'track_kind':'supplied_path','agency':payload.get('source'),'issued_at':payload.get('issued_at'),
                                  'track_start':payload['track'][0]['time'],'track_end':payload['track'][-1]['time']}
        return payload

    def status(self) -> dict:
        return {"available": self.available, "path": str(self.path)}
