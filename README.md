# 台风与海洋灾害知识问答系统

本仓库是 2026 年 10 月 19 日 `phase1.8.3-public-evidence` 的代码交接快照，用于请 Claude 分析尚未解决的问题并设计下一阶段方案，再由 Codex 实施。原应用继续在本机运行，本快照没有修改原应用。

**从 [Claude 交接说明](docs/CLAUDE_HANDOFF.md) 开始阅读，将 [Claude 任务说明](docs/CLAUDE_PROMPT.md) 作为提问。当前阶段只做诊断和方案设计。**

## 当前功能

- 在历史台风地图同一页面内提问和连续追问，保留历史台风查询与路径类比。
- 本地文档检索、官方网页检索和正文阅读，按子问题评价证据并进行有界纠错检索。
- 通过 DeepSeek 组织回答，进行来源、数值、版本和子问题覆盖检查。
- 提供预报 NC、天文潮、岸段阈值、历史灾害、官方预警和空间数据的查询计算模块。

这些是已有代码能力，不代表所有业务答案已经正确完整。详细失败样例和数据边界见交接说明。

## 代码和证据入口

| 内容 | 路径 |
|---|---|
| HTTP 服务和统一问答接口 | `server.py`、`marine_kb/chat_api.py` |
| 检索、生成、纠错、校验 | `marine_kb/qa.py`、`marine_kb/crag.py`、`marine_kb/claim_grounding.py` |
| 预报查询计算 | `forecast_runtime/` |
| 地形人口模块 | `spatial_runtime/` |
| 台风历史与网页读取 | `typhoon/` |
| 统一地图与对话前端 | `frontend/index.html`、`frontend/map_assistant.js` |
| 原始 160 题需求台账 | `data/requirements/catalog.json` |
| 较早 160 题前后实际回答 | `evaluations/previous_160.json` |
| 最新 12 题实际回答与校验诊断 | `evaluations/public_services_12.json` |
| 重点失败记录 | `evaluations/failures/` |
| 本快照验证 | `docs/SNAPSHOT_VALIDATION.md` |

160 题记录早于最后一轮公开服务修复；最新版本只有 12 题专项 HTTP 记录。两者不能合并称为新版 160 题通过率。HTTP 成功、引用存在、程序测试通过和模型 full 标签都不等于业务验收。

## 本地分析与测试

使用 Python 3.11 或更高版本。示例命令在仓库根目录执行：

```powershell
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements-dev.txt
$env:PYTHONIOENCODING = 'utf-8'
$env:MARINE_FORECAST_WORKER = 'false'
$env:MARINE_WARNING_WORKER = 'false'
$env:MARINE_EXTERNAL_MODEL_ENABLED = 'false'
$env:MARINE_WEB_ENABLED = 'false'
.\.venv\Scripts\python.exe -m pytest tests -q
```

上述是以临时测试数据或 mock 为主的程序测试，不发起真实模型验收。完整问答联调需要另外恢复本地业务数据并配置凭据，参见 [数据资产说明](docs/DATA_ASSETS.md)。不要把空库界面当成完整业务演示。

需要浏览空库界面时，保持以上 worker 和联网开关关闭，另设：

```powershell
$env:APP_HOST = '127.0.0.1'
$env:APP_PORT = '8770'
.\.venv\Scripts\python.exe .\production_server.py
```

然后打开 `http://127.0.0.1:8770/`。前端天地图需要自行配置授权密钥。示例使用独立端口，避免占用原运行服务的 8769 端口。

## 上传范围

仓库包含源代码、测试、需求台账、脱敏评测记录、历史技术说明，以及保持原哈希的少量公开资料测试附件与活动规则导出 fixture。真实 `.env`、API 密钥、会话标识、业务原始资料、NC、地形人口栅格、SQLite 数据库和下载缓存均未上传。清单和来源哈希见 `HANDOFF_MANIFEST.json`。

原有 `docs/` 中的早期说明属于历史材料，版本、数量、试算口径以文件日期和当前交接说明共同判断。公开 PDF 仅用于来源提取及校验测试，不表示其现行效力已经核定。仓库保持私有；没有为项目新增开源许可，已有第三方许可随代码保留。
