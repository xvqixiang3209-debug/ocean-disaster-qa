# 台风与海洋灾害知识问答系统

当前版本：`phase1.8.5-typhoon-time-delivery`（2026-10-20）。在Claude P0–P6实现上继续修复今日/历史时间路由、活动台风数量和中心动态字段交付，以及不同业务类型的缺项模板。保留DeepSeek、CRAG、本地资料、官方联网检索和地图连续问答。

**Claude先读[本轮专项复审](docs/CLAUDE_TYPHOON_DELIVERY_REVIEW_20261020.md)，可复制[评审提问](docs/CLAUDE_PROMPT.md)。** 原P0–P6方案和上一轮未解决项保留于 [NEXT_PHASE_PLAN_FOR_CODEX.md](docs/NEXT_PHASE_PLAN_FOR_CODEX.md) 和 [CLAUDE_FOLLOWUP_20261020.md](docs/CLAUDE_FOLLOWUP_20261020.md)。

## 验证状态

- 本机1694项测试、102项子测试通过；公开快照1642项通过、52项依赖私有录制的测试跳过、102项子测试通过。
- 本轮9次真实问答，内部共57次模型调用。最后3题执行期间源码稳定：今日full、历史partial、多子问full；字段值与工具结果一致。
- 历史partial保留旧记录及具体数值，只缺截止时点前进一步更新，未用今日数据回填。
- 最后增加的展示去重补丁用实际录制做离线展示阶段回归，没有重新收费生成答案；原始回答与离线展示结果分别保存。
- 没有本轮160题、24题或专家验收结果；程序full不能等同于单位已核定、事实全部正确或公众活动许可。

详见[验证记录](docs/SNAPSHOT_VALIDATION.md)、[公开实测说明](evaluations/typhoon_time_delivery_20261020/README.md)、[完整实际回答与离线展示对照](evaluations/typhoon_time_delivery_20261020/REPORT.md)。旧24次和160题文件均保留原日期，不代表当前代码输出。

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

浏览空库界面时，保持以上外部调用开关关闭，设置 `APP_HOST=127.0.0.1`、`APP_PORT=8772`，再运行 `.\.venv\Scripts\python.exe .\production_server.py`。完整本地安装还提供 `start_p1_p6_followup.ps1`，沿用父目录 `.venv` 的既有目录结构。

## 仍待解决

父级对象继承造成的多子问归属过宽、跨类型工具字段交付如何扩展、受限归纳如何在首次生成时稳定提供证明、不同阶段模型审查的分歧如何裁决、通用完整交付如何超越关键词规则，仍需继续评审和新的真实模型验证。旧记录中 22 个归纳未提供证明、1 个审查记录因片段边界变化不能复用、17 次正文哈希未匹配均如实保留。

SWL、MWD、L2/L5 等业务定义仍待数据方回复；时间重合不能证明 NC 由某个台风造成。地形和人口筛查不等于精细淹没或人员转移计算。

## 公开范围

本仓库现为公开仓库，包含源代码、测试、少量公开来源 fixture、需求台账和脱敏诊断。真实 `.env`、密钥、会话凭据、业务数据库、NC、栅格、原始模型请求响应及私有 cassette 不上传。文件清单见 `HANDOFF_MANIFEST.json`。没有为项目新增开源许可证，第三方许可保留。

10 月 19 日交接与验证说明保留在 `docs/archive/`，原历史资料以其文件日期判断；旧数字和旧行号不代表本轮代码。
