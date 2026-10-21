# 客观预报复审：实现与验证（2026-10-22）

版本：`phase1.12.0-objective-review`。代码基线：`5d619da54f8fe2b92b96fc3bc9f157ed1a719790`。

本轮按 [Claude 2026-10-21 复审方案](NEXT_PHASE_REVIEW_FOR_CODEX_20261021.md) 修改现有系统，处理的是地点、缺测掩膜、多要素产品交付这三类通用问题。本文的 P1–P3 指本轮地图产品复审，与此前问答改造 P1–P6 分开。

## 实现结果

| 项目 | 改动与可见行为 | 主要代码 |
|---|---|---|
| P1 地点 | 检索路由、计算、会话使用同一个四态解析结果：已命中、父级明示回退、待明确范围、越界。未知或外省地点不下载、不计算浙江数值并清旧图。只沿用已登记七个参考框，不估新坐标 | `objective_scope.py`、`objective_places.json`、`objective_bridge.py`、`objective_context.py` |
| P1 连续追问 | 出现新地点就重解析；仅改要素或时刻继承原范围。澄清时保留批次、时间和要素槽位，去掉旧范围；用户明确新地点后再恢复产品查询。兼容经核对的 v1 精确范围 | 同上，及 `marine_kb/qa.py` |
| P2 静态域与缺测 | 全源时间轴任一时刻波浪有效的格点构成静态海点域，各时次另判有效性。后来恢复的格点参与所有统计；永久无效透明；缺测不填零也不消失 | `objective.py` |
| P2 出口与缓存 | 单帧、过程、报告峰值快照、逐格最大图统一域；缓存按源 SHA、轴哈希和方法版本校验。报告缓存升级 v4，旧报告不会误复用 | `objective.py`、`objective_report.py` |
| P3 多要素 | 服务端返回动作组，保留旧单动作兼容；回答和面板都有明确要素切换。各要素使用本身峰值时刻；原始报告窗口另行保留，切图不改报告起始时刻 | `objective_bridge.py`、`chat_api.py`、`objective_panel.js`、`map_assistant.js` |
| P3 词表与错误 | 同一词表识别风浪、风和浪、风场、浪场等，支持多要素继承和收窄。动作组整体校验；过期动作失效；缺要素、越界或错误清图，保留回答 | `objective_fields.py`、前端上述文件 |
| T1 审查争议 | 已通过原子遭晚期异议后，用原文段和理由做一次定向复核；再次支持且文字、归属、引用、证明未变才保留。预算不足、异常、分歧写明状态，不再只标“待复核” | `marine_kb/answer_pipeline.py` |
| T5 解读成本 | 报告解读默认 `on_request`；普通数值产品不调用模型。明确要求解读才接现有 DeepSeek/CRAG 审查流程，程序数据仍完整保留 | `objective_bridge.py` |

### 地点与动作契约

`resolve_scope_request(question, bounds=None, previous=None)` 返回 `state/reason/requested_places/matched/parents/unknown/scope/notice/registry_version`。

- 明确经纬度框优先；未指定地点显示默认范围提示。
- 嵊泗、普陀等已知无独立框地点可按已登记父框计算，首段和地图同时提示“父级参考框，不代表当地”。`clarify` 配置可改为先明确范围。
- 未登记名称，包括具有地名后缀的名称和明确地点槽中的裸名称，保守澄清。它是范围保护，不是完整地理编码服务；新增别名继续进入版本化注册表。
- 外省或不支持海域不以浙江代答；不会用失败前的旧图代表新查询。
- 已有框为经纬度参考统计窗，不是行政海域或精细岸线。

`map_actions` 与旧 `map_action` 共存。组字段为 `group_id/group_index/group_size/time_role`；范围提示用 `scope_note`。峰值快照的 `valid_time` 是本要素峰值时刻，`window_start/window_hours` 是原报告查询窗口；二者分开。逐格最大值动作仍用 `start/hours`，不拼接各时刻风向箭头。澄清清图动作仅含 `{type:'objective_clear', reason:'…'}`。

### 配置

```dotenv
OBJECTIVE_UNBOXED_POLICY=parent_with_notice
OBJECTIVE_SEA_MASK=static_any_valid
MARINE_OBJECTIVE_INTERPRETATION=on_request
```

地点策略可设 `clarify`。掩膜可临时回滚 `first_frame`，回滚产品使用不同缓存身份，不把旧方法算作新结果。解读可设 `off/on_request/always`；测试与验证没有启用真实模型。

## 本轮实际验证

1. 使用本机真实 h5py/scipy/pytest，重新运行 Python 全量 `pytest tests -q`。最终计数见 [SNAPSHOT_VALIDATION](SNAPSHOT_VALIDATION.md)，不沿用 Claude 缺依赖环境的旧计数。
2. 重新运行三个 Node 文件：`test_objective_panel.cjs`、`test_objective_map_actions.cjs`、`test_knowledge_libraries.cjs`；包括整组拒绝、独立峰值时间、原报告窗口保持、过期按钮和异步响应、旧单动作兼容。
3. 原样合成条件对旧公开代码与新代码分别运行，使用实际 h5py/scipy，不加载附件 stub：旧报告 1 m、缺测 0、错误 ready；新报告与图件均 9 m、缺测 1、超阈样本 1、partial。永久无效格继续透明，恢复点风场不再丢失。
4. 用真实本地 L2/L5 NC 检查源域差异、风浪跨出口峰值/时间一致性。新域比首帧域多出有效候选格点；缺测因此可见。真实数值和源哈希仅保存在内部实测记录，未发布原始业务 NC。
5. 运行 `tools/test_objective_review_20261022.py --run`：13 条真实本地 NC 问答，覆盖复审10种场景及地点更换、未知岛和缓存复用；模型调用 0、下载/检索网络调用 0。没有把“本地最新已登记批次”写成“刚从网站取到的最新批次”。
6. 独立预览关闭外部模型、联网检索与后台 worker，检查实际页面的朱家尖双要素切换、宁波报告独立峰值时刻、父级提示和越界清图；截图和记录见内部报告。

真实数据问题可以返回 `partial`：它表示暴露出真实缺测，并非代码回归失败。`ready` 只描述程序交付，不替代变量口径核定或业务验收。

本轮没有付费模型问答，也没有重跑160题或重新录制此前24次基线。T1 使用 mock 裁决正反例；不能据此宣称真实模型审查已全部稳定。

## 保留的工作

- 原问答 P1–P6 的 T2 子问原子归属、T3 归纳证明首轮生成、T4 录制正文哈希仍保留跟踪，未用本轮地图测试冒充解决。
- P4 精细岸线、带底图图片导出、风浪同屏叠加及双屏对比未纳入本轮必修。
- 未登记地点要取得业务确认的范围后再增框；变量、时区等沿用已授权临时解释。L2/L5 气压仍未提供；MWD 浪向未绘制。
- 下一次收费验证可只对明确要求解读的报告和晚期争议复核录制，先确定问答数量/调用预算，再执行。无需为了检查程序图件支付语言模型费用。

## 复核命令

```powershell
python -m pytest tests -q
node --test tests/test_objective_panel.cjs tests/test_objective_map_actions.cjs tests/test_knowledge_libraries.cjs
# 以下需本机授权 NC 和 index；仅读取真实源，结果写 tmp/claude_review_20261022。
python tools/test_objective_review_20261022.py --run
```

公开快照不含 `.env`、业务数据或私有录制；相应测试按既有规则跳过。内部原始问答、图件和日志留在本机，与公开统计分开。
