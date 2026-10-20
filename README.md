# 台风与海洋灾害知识问答系统

当前版本：`phase1.8.4-grounded-recovery`（2026-10-20）。本次在 Claude 的 P0–P6 方案上继续修复子问归属、局部恢复、额外缺项、证据与数值校验，保留 DeepSeek、本地知识库、官方联网检索、地图和业务计算模块。

**Claude 从 [本轮复审任务](docs/CLAUDE_FOLLOWUP_20261020.md) 开始；可直接使用 [评审提问](docs/CLAUDE_PROMPT.md)。** 原方案针对 `a6113ee`，见 [NEXT_PHASE_PLAN_FOR_CODEX.md](docs/NEXT_PHASE_PLAN_FOR_CODEX.md)。本轮请只读审查并设计下一阶段方案。

## 验证状态

- 完整本机代码：1485 项测试、102 项子测试通过。
- 公开代码快照：1433 项测试、102 项子测试通过；52 项依赖本机私有录制的测试跳过。
- 用先前 24 次真实录制做确定性阶段复核，识别错误完整标签、来源识别和缺项生命周期问题。
- 本轮新增付费模型问答为 0，未生成新版 24 次或 160 题端到端回答；没有完整正确率或专家验收结论。

详见 [验证记录](docs/SNAPSHOT_VALIDATION.md)、[离线证据目录](evaluations/p1_p6_followup_20261020/README.md)。`evaluations/previous_160.json` 和 `evaluations/public_services_12.json` 为 10 月 19 日历史结果。新增 `prior_live_24.json` 保存首轮 P1–P6 的旧实际回答，不代表本轮代码输出。HTTP 成功、引用存在、模型 full 标签和程序测试通过分别报告。

## 代码入口

| 环节 | 文件 |
|---|---|
| HTTP 和连续追问 | `server.py`、`marine_kb/chat_api.py`、`marine_kb/conversation.py` |
| 本地与官方网页检索 | `marine_kb/crag.py`、`marine_kb/retrieval.py`、`marine_kb/web_supplement.py` |
| 统一子问和文档证据 | `marine_kb/facet_contract.py`、`marine_kb/evidence_units.py` |
| 问答调度与局部恢复 | `marine_kb/answer_pipeline.py`、`marine_kb/recovery.py` |
| 事实、归纳与交付校验 | `marine_kb/typed_grounding.py`、`marine_kb/facet_delivery.py`、`marine_kb/relation_guard.py` |
| 缺项范围与生命周期 | `marine_kb/scope_gate.py` |
| 私有录制与严格回放 | `marine_kb/replay.py`、`tools/replay/README.md` |
| 预报与地形人口计算 | `forecast_runtime/`、`spatial_runtime/` |
| 地图统一对话前端 | `frontend/index.html`、`frontend/map_assistant.js` |
| 160 题需求台账 | `data/requirements/catalog.json` |

## 离线测试

使用 Python 3.11 或更高版本，在仓库根目录执行：

```powershell
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements-dev.txt
$env:PYTHONIOENCODING = 'utf-8'
$env:MARINE_FORECAST_WORKER = 'false'
$env:MARINE_WARNING_WORKER = 'false'
$env:MARINE_EXTERNAL_MODEL_ENABLED = 'false'
$env:MARINE_WEB_ENABLED = 'false'
$env:TYPHOON_WEB_SEARCH_ENABLED = 'false'
$env:TYPHOON_WEB_READ_ENABLED = 'false'
.\.venv\Scripts\python.exe -m pytest tests -q
```

这些测试使用临时数据和模拟依赖。完整业务演示还需单独恢复数据库与业务文件，见 [数据资产说明](docs/DATA_ASSETS.md)。私有录制不包含在公开仓库，相关测试按设计跳过，不提供真实 API 回退。可独立运行 `tests/test_followup_pipeline_contracts.py`、`tests/test_followup_recovery_delivery.py`、`tests/test_scope_lifecycle_followup.py` 验证本轮通用机制。

浏览空库界面时，保持以上外部调用开关关闭，设置 `APP_HOST=127.0.0.1`、`APP_PORT=8771`，再运行 `.\.venv\Scripts\python.exe .\production_server.py`。完整本地安装还提供 `start_p1_p6_followup.ps1`，沿用父目录 `.venv` 的既有目录结构。

## 仍待解决

受限归纳如何在首次生成时稳定提供证明、不同阶段模型审查的分歧如何裁决、通用完整交付如何超越关键词规则，仍需继续评审和新的真实模型验证。旧记录中 22 个归纳未提供证明、1 个审查记录因片段边界变化不能复用、17 次正文哈希未匹配均如实保留。

SWL、MWD、L2/L5 等业务定义仍待数据方回复；时间重合不能证明 NC 由某个台风造成。地形和人口筛查不等于精细淹没或人员转移计算。

## 公开范围

本仓库现为公开仓库，包含源代码、测试、少量公开来源 fixture、需求台账和脱敏诊断。真实 `.env`、密钥、会话凭据、业务数据库、NC、栅格、原始模型请求响应及私有 cassette 不上传。文件清单见 `HANDOFF_MANIFEST.json`。没有为项目新增开源许可证，第三方许可保留。

10 月 19 日交接与验证说明保留在 `docs/archive/`，原历史资料以其文件日期判断；旧数字和旧行号不代表本轮代码。
