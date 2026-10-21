"""Fact-level validation for externally returned typhoon analyses."""

from __future__ import annotations

from dataclasses import dataclass


class FactValidationError(RuntimeError):
    pass


@dataclass(frozen=True)
class ValidationResult:
    warnings: tuple[str, ...] = ()

    @property
    def status(self) -> str:
        return "pass_with_warnings" if self.warnings else "pass"


def validate_analysis_result(result: dict) -> ValidationResult:
    errors: list[str] = []
    warnings: list[str] = []
    current = result.get("current")
    analogues = result.get("analogues")
    if not isinstance(current, dict):
        errors.append("缺少 current 台风结果")
    if not isinstance(analogues, list) or not analogues:
        errors.append("历史相似案例列表为空")
        analogues = []

    expected_ranks = list(range(1, len(analogues) + 1))
    actual_ranks = [item.get("rank") for item in analogues]
    if actual_ranks != expected_ranks:
        errors.append("历史案例排名不连续或顺序被修改")
    sids = [str(item.get("sid") or "") for item in analogues]
    if any(not sid for sid in sids) or len(set(sids)) != len(sids):
        errors.append("历史案例 SID 缺失或重复")

    for item in analogues:
        label = f"第{item.get('rank')}名{item.get('name') or item.get('sid')}"
        if not str(item.get("analysis") or "").strip():
            errors.append(f"{label}缺少文本分析")
        surge = item.get("storm_surge") or {}
        if surge.get("is_estimate"):
            forbidden = (
                "max_surge_m",
                "max_surge_station",
                "max_water_level_m",
                "max_water_level_station",
                "warning_level",
            )
            if any(surge.get(field) not in (None, "", []) for field in forbidden):
                errors.append(f"{label}为代理判断，却包含伪实测风暴潮字段")
        if surge.get("max_surge_m") is not None and not surge.get("max_surge_station"):
            errors.append(f"{label}有最大增水数值但缺少出现站点")
        if surge.get("max_water_level_m") is not None and not surge.get("max_water_level_station"):
            errors.append(f"{label}有最高潮位数值但缺少出现站点")
        if not surge.get("is_estimate") and surge.get("record_count", 0) == 0:
            warnings.append(f"{label}标记为实测，但record_count为0")

    report = str(result.get("report") or "").strip()
    if not report:
        errors.append("缺少综合分析报告")
    if errors:
        raise FactValidationError("；".join(errors))
    return ValidationResult(tuple(warnings))

