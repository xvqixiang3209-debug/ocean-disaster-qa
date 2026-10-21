# 台风与海洋灾害知识问答系统

当前代码快照：**2026-10-22，客观预报复审实现版**（`phase1.12.0-objective-review`）。按Claude复审实现地点四态解析、整源静态海点域、多要素地图动作与切换，并增加一次晚期争议复核和按需模型解读。保留DeepSeek、CRAG、本地资料/官方网络联合检索、相似台风、地图连续对话和报告复用。当前源码以所在提交和`HANDOFF_MANIFEST.json`为准。

**给Claude的入口：[本轮实现与验证](docs/OBJECTIVE_REVIEW_IMPLEMENTATION_20261022.md) · [原复审方案](docs/NEXT_PHASE_REVIEW_FOR_CODEX_20261021.md) · [海点与缺测契约](docs/OBJECTIVE_SEA_MASK_CONTRACT_20261022.md) · [可复制的评审提问](docs/CLAUDE_PROMPT.md)。**

按提问地点绘制二维风浪地图的原理和接口见[绘图指南](docs/OBJECTIVE_MAP_RENDERING_GUIDE.md)；该指南按2026-10-21版本编写，新增的地点、掩膜和动作组接口以本轮实现记录为准。[上一轮交接](docs/CLAUDE_CURRENT_HANDOFF_20261021.md)保留作历史背景。

## 三个功能

| 功能 | 当前行为与边界 |
|---|---|
| 灾害/预警知识问答 | 本地资料、官方网页和程序数据联合检索；支持连续追问；晚期异议新增一次定向复核，子问归属、首轮归纳证明及旧录制哈希仍有跟踪项 |
| 相似台风 | 历史路径、机构和相似度查询及地图显示；路径相似不等于灾损相似 |
| 客观预报产品 | 按地点和时段从L2/L5计算二维场、原生过程及逐格最大值；未知/越界地点阻断，父级回退明示；恢复海点参与统计，风浪分别切换及使用自身峰值时刻；输出HTML/PNG/CSV/JSON报告 |

地图采用真实NC数值驱动的Canvas图层，不是让模型生成图片。单时刻海面风叠加对应u/v箭头；72小时最大风速各格峰值时间可能不同，不拼接风向。下载PNG是经纬度图，不是带底图的地图截图。

## 验证状态

- 本轮完整本机应用实际重跑：2447项测试、134项子测试通过，失败0。
- 本次公开快照独立重跑：2389项通过、58项私有依赖跳过、134项子测试通过，失败0（26.57秒）。
- 本次Node地图/知识库测试3个文件全部通过。
- 本轮13条真实本地NC问答、跨出口峰值核对、旧版/新版合成复现对照和浏览器检查已执行。模型调用0次、网络下载0次；未重跑160题，也未重新录制24次基线。

见[验证记录](docs/SNAPSHOT_VALIDATION.md)和[本轮统计摘要](evaluations/objective_review_20261022/summary.json)。[2026-10-21统计](evaluations/objective_map_20261021/summary.json)独立保留。HTTP200、full/partial、自评和程序检查分别报告，均不代替专家验收。

## 代码入口

| 链路 | 文件 |
|---|---|
| 提问地点、日期、图件意图 | `forecast_runtime/objective_bridge.py`、`objective_scope.py`、`objective_places.json`、`objective_fields.py`、`marine_kb/objective_context.py` |
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
node --test tests/test_objective_panel.cjs tests/test_objective_map_actions.cjs tests/test_knowledge_libraries.cjs
```

仓库中的合成NC测试不需要业务数据库或真实模型。私有依赖测试按设计跳过，不启用真实API回退。完整演示需另行恢复授权数据，见[数据资产说明](docs/DATA_ASSETS.md)。

本机空库预览保持外部调用和worker关闭，设置`APP_HOST=127.0.0.1`、`APP_PORT=8767`，再运行`production_server.py`。拥有授权NC后可运行`python tools/import_objective_nc.py <自己的NC目录>`；地图API及动态catalog示例见[绘图指南](docs/OBJECTIVE_MAP_RENDERING_GUIDE.md)。

## 当前复审重点与公开边界

本轮重点复查地点四态保护是否存在误默认或误澄清、原生有效性与各图表是否一致、各要素峰值时刻与报告窗口是否隔离，以及晚期争议复核是否真正执行且不改写已核对内容。本轮程序测试不能证明真实模型审查已稳定。地点目前是参考框，尚非精细海岸/行政海域多边形；风浪同屏叠加、双屏和带底图导出仍为扩展。

默认策略为`OBJECTIVE_UNBOXED_POLICY=parent_with_notice`、`OBJECTIVE_SEA_MASK=static_any_valid`、`MARINE_OBJECTIVE_INTERPRETATION=on_request`。普通程序图件和报告不调用模型；明确要求解读时才进入现有模型流程。公开模板保持全部凭据为空、外部调用与自动获取关闭。

L2/L5变量沿用临时解释，未提供气压；MWD、单位、时区及部分业务定义待核定。时间重合不能证明NC来自某个台风，模式风浪不等于正式预警或活动许可。

公开仓库只含代码、测试、需求台账、最小可公开配置和统计；不含真实.env、密钥、业务DB/NC/栅格、原始模型请求响应或私有cassette。已配置密钥的完整内部交接包单独保存在本地，不上传本仓库。三个含大量来源正文的本地校录工具不公开，核心运行代码不依赖发布它们。文件清单和排除记录见`HANDOFF_MANIFEST.json`。既有第三方许可保留，本次没有新增项目开源许可证。

旧160题、24次、12题、时间交付等记录按原日期保留于`evaluations/`及`docs/archive/`，不代表本次代码的全量业务验收。
