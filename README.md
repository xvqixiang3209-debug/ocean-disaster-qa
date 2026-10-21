# 台风与海洋灾害知识问答系统

当前代码快照：**2026-10-21，客观预报与地点地图版**。保留DeepSeek、CRAG、本地资料/官方网络联合检索、相似台风、地图连续对话；新增或完善L2/L5风浪产品、地点/日期/追问联动、分区过程、72小时逐格最大值图和报告复用。`VERSION.txt`保留原应用版本标识，当前源码以所在提交和`HANDOFF_MANIFEST.json`为准。

**给Claude的入口：[当前交接与复审任务](docs/CLAUDE_CURRENT_HANDOFF_20261021.md) · [如何按提问地点绘制二维风浪地图](docs/OBJECTIVE_MAP_RENDERING_GUIDE.md) · [可复制的评审提问](docs/CLAUDE_PROMPT.md)。**

## 三个功能

| 功能 | 当前行为与边界 |
|---|---|
| 灾害/预警知识问答 | 本地资料、官方网页和程序数据联合检索；支持连续追问。子问归属、归纳证明、审查冲突仍有复审任务 |
| 相似台风 | 历史路径、机构和相似度查询及地图显示；路径相似不等于灾损相似 |
| 客观预报产品 | L2/L5风浪获取/保留/复用；按地点和时段计算二维场、原生过程和逐格最大值，在当地地图显示，输出HTML/PNG/CSV/JSON报告 |

地图采用真实NC数值驱动的Canvas图层，不是让模型生成图片。单时刻海面风叠加对应u/v箭头；72小时最大风速各格峰值时间可能不同，不拼接风向。下载PNG是经纬度图，不是带底图的地图截图。

## 验证状态

- 完整本机应用最近记录：2289项测试、134项子测试通过。
- 本次公开快照重新运行：2231项通过、58项因私有录制或数据库依赖跳过、134项子测试通过，失败0。
- 本次Node地图/连接测试3个文件全部通过。
- 已有8次真实问答（7取得数据、1无覆盖）、8组产品检查及独立NC核验；本次上传没有新付费问答或160题重测。

见[验证记录](docs/SNAPSHOT_VALIDATION.md)和[统计摘要](evaluations/objective_map_20261021/summary.json)。HTTP200、full/partial、自评和程序检查分别报告，均不代替专家验收。

## 代码入口

| 链路 | 文件 |
|---|---|
| 提问地点、日期、图件意图 | `forecast_runtime/objective_bridge.py`、`objective_scope.py`、`marine_kb/objective_context.py` |
| NC解析、原生网格与二维图 | `forecast_runtime/objective.py`、`objective_time.py`、`objective_process.py` |
| L2/L5获取和选批次 | `forecast_runtime/objective_sources.py` |
| 地图/报告API与缓存 | `forecast_runtime/objective_api.py`、`objective_report_api.py`、`objective_report.py` |
| 地图动作与Canvas叠加 | `frontend/map_assistant.js`、`objective_panel.js`、`index.html` |
| 检索、子问、恢复和事实校验 | `marine_kb/crag.py`、`facet_contract.py`、`answer_pipeline.py`、`typed_grounding.py`、`facet_delivery.py` |
| 历史相似台风 | `typhoon/`、`forecast_runtime/analogue.py` |
| 原Claude方案与历史复审 | `docs/NEXT_PHASE_PLAN_FOR_CODEX.md`、`CLAUDE_FOLLOWUP_20261020.md`、`CLAUDE_TYPHOON_DELIVERY_REVIEW_20261020.md` |

## 离线开发测试

当前验证环境为Python3.13.9 x64。Windows示例：

```powershell
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements-dev.txt
$env:PYTHONIOENCODING = 'utf-8'
$env:MARINE_FORECAST_WORKER = 'false'
$env:MARINE_WARNING_WORKER = 'false'
$env:MARINE_OBJECTIVE_AUTO_SYNC = 'false'
$env:MARINE_EXTERNAL_MODEL_ENABLED = 'false'
$env:MARINE_WEB_ENABLED = 'false'
$env:TYPHOON_WEB_SEARCH_ENABLED = 'false'
$env:TYPHOON_WEB_READ_ENABLED = 'false'
.\.venv\Scripts\python.exe -m pytest tests -q
node --test tests/test_objective_panel.cjs tests/test_objective_map_actions.cjs tests/test_knowledge_connection.cjs
```

仓库中的合成NC测试不需要业务数据库或真实模型。私有依赖测试按设计跳过，不启用真实API回退。完整演示需另行恢复授权数据，见[数据资产说明](docs/DATA_ASSETS.md)。

本机空库预览保持外部调用和worker关闭，设置`APP_HOST=127.0.0.1`、`APP_PORT=8767`，再运行`production_server.py`。拥有授权NC后可运行`python tools/import_objective_nc.py <自己的NC目录>`；地图API及动态catalog示例见[绘图指南](docs/OBJECTIVE_MAP_RENDERING_GUIDE.md)。

## 当前复审重点与公开边界

未知地名是否静默退全省/保留旧地点、首帧缺测海点恢复后统计与最大图的一致性、多要素地图交付，属于本轮静态发现的待验证点。不能写成已实测失败，也不能把已有绘图功能说成尚未实现。地点目前是参考框，尚非精细海岸/行政海域多边形。

L2/L5变量沿用临时解释，未提供气压；MWD、单位、时区及部分业务定义待核定。时间重合不能证明NC来自某个台风，模式风浪不等于正式预警或活动许可。

公开仓库只含代码、测试、需求台账、最小可公开配置和统计；不含真实.env、密钥、业务DB/NC/栅格、原始模型请求响应或私有cassette。已配置密钥的完整内部交接包单独保存在本地，不上传本仓库。三个含大量来源正文的本地校录工具不公开，核心运行代码不依赖发布它们。文件清单和排除记录见`HANDOFF_MANIFEST.json`。既有第三方许可保留，本次没有新增项目开源许可证。

旧160题、24次、12题、时间交付等记录按原日期保留于`evaluations/`及`docs/archive/`，不代表本次代码的全量业务验收。
