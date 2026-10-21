# 第三功能（客观预报二维地图与报告）复审及下一阶段方案（交 Codex 实施）

## 0. 审查对象与本轮实际执行

| 项目 | 内容 |
|---|---|
| 实际读取的提交 | `5d619da54f8fe2b92b96fc3bc9f157ed1a719790`（父提交 `333d45d`，再上 `6777f92`、`a6113ee`） |
| 已读文档 | `docs/CLAUDE_CURRENT_HANDOFF_20261021.md`、`docs/OBJECTIVE_MAP_RENDERING_GUIDE.md`、`docs/SNAPSHOT_VALIDATION.md`、`evaluations/objective_map_20261021/summary.json`、`docs/NEXT_PHASE_PLAN_FOR_CODEX.md`、`docs/CLAUDE_FOLLOWUP_20261020.md`、`docs/CLAUDE_TYPHOON_DELIVERY_REVIEW_20261020.md` |
| 已读代码 | `forecast_runtime/objective*.py`、`marine_kb/objective_context.py`、`frontend/objective_panel.js`、`frontend/map_assistant.js`；P5 相关的 `marine_kb/answer_pipeline.py`、`typed_grounding.py`（只读了与跟踪项有关的函数） |
| 依赖安装 | **失败**。审查环境无法访问 PyPI，`h5py`、`pytest`、`waitress` 都缺。可用的有 Python 3.13、numpy 2.5.3、scipy 1.18.1、matplotlib 3.11.2、Flask 3.1.3、Node 22.22.0 |
| Python 全量测试 | **未重跑**。2289 / 2231 两个数字都来自仓库记录，不是本轮结果 |
| Node 测试（本轮实际运行） | `node --test tests/test_objective_panel.cjs tests/test_objective_map_actions.cjs tests/test_knowledge_connection.cjs` → **3 pass，0 fail**（309 ms） |
| 合成 NC 复现（本轮实际运行） | 见附录 A。用 scipy 写 NetCDF3 classic 格式的 L2 风格 WAV/SSW 文件（21×17 格点、13 个逐时原生时次），走 `ObjectiveForecast` 的 scipy 读取分支。因为缺 h5py，用一个只有 `is_hdf5()→False` 的测试替身让模块能导入；替身不读写任何 HDF5，也不改动被测代码 |
| 前端探针（本轮实际运行） | 复用 `tests/test_objective_map_actions.cjs` 的最小 DOM，追加两段探针（附录 A.4） |
| 未执行 | 收费模型调用、生产服务访问、真实 NC、私有 cassette、浏览器视觉验收。本轮新增模型调用 **0 次** |

**证据层级标记**（每条结论都标一个）：
- 【码】代码可确证
- 【合】合成测试已复现（本轮实际跑过）
- 【录】需原始录制
- 【新】需新的真实问答
- 【业】需业务方确认

---

## 1. 结论摘要

1. **P1 未登记地点会静默退回全省或沿用旧地点。【码】【合】**
   - “绘制嵊泗/象山/洋山港/东极岛/东海/杭州湾……海浪二维图”全部解析为 `zhejiang`。
   - **“绘制厦门……海浪二维图”同样返回浙江近海结果**：厦门不在地名表里，题目中的“二维”又满足了路由条件。
   - 追问“同一时刻换成嵊泗”“换成普陀附近”会沿用上一轮的朱家尖范围，回答标题仍写“朱家尖近海参考统计框”。
   - 另外，“明天08时嵊泗海浪怎么样”这类没有产品词的问法，不会进入客观预报路由（`objective_question` 返回 False）。它最终走哪条路由需要接口测试确认。
2. **P2 首帧掩膜导致报告与图件互相矛盾。【码】【合】** 合成场中，一个海点首帧缺测，4 小时后恢复为 9.0 m，成为窗口峰值：
   - `stats72`、报告：峰值 1.0 m，超阈时段 0，缺测样本 0，状态 `ready`，没有任何提示。
   - `temporal_maximum`、逐格最大值图：9.0 m。
   - 地图单时刻海浪图（前端不加掩膜）：9.0 m。
   - 报告的峰值快照 PNG（加了掩膜）：1.0 m。
   - 报告的地图动作取了错误的峰值时刻。
   - 同一海点的海面风在恢复后的时刻也一直被遮掉。
   - 始终缺测的点在各输出中都是透明（None），没有被当成 0，这一点是对的。
3. **P3 风浪双要素只交付首要素地图，部分问法连文字都丢了一个要素。【码】【合】**
   - 地图动作只有一个；`map_assistant.js` 即使收到 `map_actions` 数组也只执行第一个。
   - 在面板里切换到海面风后，时刻沿用海浪峰值时刻，而不是风速峰值时刻。
   - 追问“同一时刻风浪都显示”“同一时刻换成风场和浪场”时，**文字和地图都只剩一个要素**。原因是两个模块对“风浪”“浪场”的词表不一致，加上规范化问句里的 `地图要素：X` 标记优先。
4. **P4 精细岸线与带底图导出是扩展，不属于必修。** 必修的是 P1–P3 的数值、范围和交付一致性。
5. **P5 原 P1–P6 跟踪项仍开着。**
   - 跨阶段争议目前是“后一个审查直接暂缓前一个审查已通过的原子”，没有真正复核。决策标签写着 `pending_recheck`，但代码里没有任何复核调用。【码】
   - 报告类问答每次约 8 次模型调用，而 6 次报告的最终展示都是 `business_data`，解读部分可能全部被弃用，需要按录制统计确认。【录】

---

## 2. 现有链路：从问题到当地地图

```
用户问题 + 会话
 → qa.prepare_question → objective_context.product_question_plan     （不调用模型；继承或更正数据集、时段、要素、范围、视图）
 → forecast_runtime.service.matches/answer → objective_bridge.objective_question（固定地名正则把关，L189–202）
 → objective_bridge.answer_objective → query_objective（L270–324）
      requested_window → resolve_scope(question)（L284） → ObjectiveSources.select（来源/层级/起报/覆盖）
      ├─ _field_query → _field_product（L81–126）：每个要素调一次 engine.frame；map_action 只取 fields[0]（L118、L126）
      └─ 报告 → ObjectiveReport.report → engine.stats72（全范围 + 各分区） + image_png 峰值快照 + image_temporal_maximum_png
                 map_action：首要素峰值时刻的快照，或在明确要求时给 objective_maximum（L312–323）
 → 回答 meta.map_action → map_assistant.js L139（只取一个动作） → ObjectivePanel.applyAction（校验白名单后再请求 /frame 或 /maximum/frame）
 → renderLayer/draw：Canvas 按 lat/lon/values 投影，fitBounds 定位到实际格点范围
```

**二维数组的形成**：`_native` 按原生时次和矩形切片（北到南、西到东），先识别缺测，再做缩放和偏移，风速逐格计算 `hypot(u,v)`；`frame` 先在原生网格上统计，再做显示采样；`temporal_maximum` 逐时次在原生网格上归约。这一段的设计是对的，本方案不改动数组的形成方式，只统一掩膜语义和范围、要素的交付。

---

## 3. P1 地点解析：根因与最小修改

### 3.1 证据

| 现象 | 位置 | 层级 |
|---|---|---|
| 没有命中 6 个固定地名时，直接返回浙江框 | `objective_scope.py:resolve_scope` L64–70 | 【码】 |
| 追问只认浙江、舟山、朱家尖、宁波、台州、温州、嘉兴、“地图范围”这几个词，其他地名一律沿用旧范围 | `objective_context.py` L181–182 | 【码】 |
| 路由门槛的地名正则同样固定，但出现“二维”“近海”时也会放行 | `objective_bridge.py` L202 | 【码】 |
| 超出浙江的判断依赖 `leaf_places`（PATHS 表加“省/市/县/区/镇”后缀），“厦门”既不在表里也没有后缀 | `objective_context.py` L138–147 | 【码】 |
| 合成运行：嵊泗、嵊泗县、象山、象山港、洋山港、东极岛、厦门、东海、杭州湾 → `zhejiang`；福建、上海 → 澄清；“舟山普陀” → `zhoushan`（父级框，回答里没有说明普陀未单独登记） | 附录 A.2 | 【合】 |
| 合成运行：朱家尖地图后追问“同一时刻换成嵊泗”“换成普陀附近” → 仍是 `zhujiajian`；“换成宁波” → `ningbo`（正确） | 附录 A.2 | 【合】 |
| 合成运行：“绘制嵊泗……”的回答标题是“浙江近海参考统计框二维预报图”，数值取自全省；没有任何一句说明嵊泗未登记 | 附录 A.2 | 【合】 |

**根因**：系统有两套地名知识。PATHS 知道嵊泗、普陀属于舟山、浙江；`_BOXES` 只有 6 个计算框。两套之间没有“已识别但没有计算框”这一状态，所以只能落到默认浙江或旧范围。

### 3.2 最小修改（新增一个解析结果，原接口保留）

**新函数** `forecast_runtime/objective_scope.py`：

```python
SCOPE_VERSION = 'zhejiang_reference_boxes_v2'          # 升版本：进入报告缓存键和 scope.version

def resolve_scope_request(question, bounds=None, previous=None) -> dict:
    """返回 ScopeResolution；resolve_scope 保留为兼容包装。"""
    # 1) 显式 bounds 或“地图范围：”优先 → state='resolved', kind='map_bounds'
    # 2) mentions = place_mentions(question)        # 见下
    # 3) 没有提到任何地点 → previous 存在则沿用（state='inherited'），否则用浙江（state='default'）；
    #    两种情况都必须写 notice
    # 4) 逐个提到的地点查注册表：
    #      有计算框            → matched
    #      PATHS 已知但无计算框 → parent = 最近的、有计算框的祖先（嵊泗→舟山，象山→宁波）
    #      都不认识            → unknown
    # 5) 决策：
    #      全部 matched                     → state='resolved'
    #      有 parent、无 unknown             → 按策略 OBJECTIVE_UNBOXED_POLICY
    #                                          'parent_with_notice'（默认）：state='parent_fallback'
    #                                          'clarify'：state='needs_scope'
    #      有 unknown                       → state='needs_scope'（不计算）
    #      越界（福建、上海等）              → state='out_of_region'
    #      同一句里多个地点且彼此无包含关系   → 保留现有“同级多地联合框”行为
    return {'state': ..., 'requested_places': [...], 'matched': [...], 'parents': {'嵊泗': '舟山'},
            'unknown': [...], 'scope': scope_or_None,
            'notice': '嵊泗未单独登记参考框；以下为舟山近海参考框结果，不代表嵊泗当地。',
            'registry_version': SCOPE_VERSION}

def place_mentions(text) -> list[str]:
    # 先去掉“地图范围：…”、要素名、时间等，再合并三类来源：
    #   leaf_places(text)；
    #   注册表别名（含“普陀山”“嵊泗列岛”等）；
    #   通用地名尾缀 r'([一-鿿]{2,6}?)(?:岛|港|湾|列岛|海域|附近|一带|沿海|近海)'，
    #     但排除“浙江近海”中的“浙江”这类已登记前缀，以及“近海”“海域”这类泛词本身
    # 结果按出现顺序去重；“换成/改成”之后的对象优先（沿用现有 correction 逻辑）
```

**注册表**（新文件 `forecast_runtime/objective_places.json`，可配置、带版本）：

```json
{"version": "zhejiang_reference_boxes_v2",
 "places": [
   {"id": "zhoushan", "names": ["舟山"], "bounds": [121.75,29.45,123.15,31.10], "source": "v1 参考框沿用"},
   {"id": "shengsi", "names": ["嵊泗","嵊泗县","嵊泗列岛"], "parent": "zhoushan", "bounds": null,
    "source": "待业务方给定计算框；未给定前按 parent_with_notice"}
 ]}
```

- **不得**由 Codex 自行估计新地点的经纬度框。新框必须由业务方提供来源和版本（【业】）；没有框的地点走父级或澄清。
- `PATHS` 作为“已识别地名与包含关系”的来源保留，注册表只负责“哪些地点有计算框”。

**接入点**

| 文件 | 修改 |
|---|---|
| `objective_bridge.query_objective` L284 | 改用 `resolve_scope_request`。`needs_scope` 或 `out_of_region` 时直接返回 `state='needs_scope'`，**不选数据、不计算、不给 map_action**；`parent_fallback` 时把 `notice` 写进 summary 第一段和 evidence |
| `objective_bridge.objective_question` L202 | 用 `place_mentions(q)` 或注册表命中替代固定地名正则（“二维”“近海”保留）。“明天08时嵊泗海浪怎么样”应进入客观路由，再由上面的状态决定是计算还是澄清 |
| `objective_context.product_question_plan` L181–182 | `named_scope` 改为 `bool(place_mentions(q))`。有新地点时重新解析，不继承旧范围；新地点未知时返回澄清 plan，**不得把旧范围拼进规范化问句** |
| `objective_bridge._field_product` L96 | 标题使用 `resolution` 的实际范围，并紧接 `notice` |
| `answer_meta` / 会话上下文 | 增加 `objective_scope_resolution`（服务端字段，不进 map_action） |
| `objective_panel.js` | 仅当 action 带新的白名单字段 `scope_note` 时，在状态栏显示“地图显示：舟山近海参考框（嵊泗未单独登记）”。**改 map_action 键名必须同时改 `validAction` 白名单（L162）和 cjs 测试**，否则整个地图动作会被拒绝（回答仍保留，但地图不显示） |

**澄清文案**（可见行为）：“嵊泗目前没有单独的计算范围。可以：①按舟山近海参考框计算（结果不代表嵊泗当地）；②在地图上框选范围后再问；③选择已登记的地点：浙江、嘉兴、宁波、舟山、台州、温州、朱家尖。”同时清除旧图层，不显示其他地点的图。

**兼容性**
- `resolve_scope` 签名不变，供 API、`product_context` 使用；scope 版本升到 v2 后，旧缓存不会命中（预期行为）。
- `product_context` 校验 `supplied.version` 时须接受 v1 历史上下文，并按 v1 框重放；或者遇到 v1 时要求重新确认。二选一，写进测试。

### 3.3 测试

| 类型 | 输入 | 期望 |
|---|---|---|
| 正例 | “绘制朱家尖明天08时海浪二维图” | `zhujiajian`，无提示 |
| 正例 | “舟山普陀明天08时海浪二维图” | `zhoushan` + notice“普陀未单独登记” |
| 父级 | “绘制嵊泗明天08时海浪二维图” | 默认策略：`parent_fallback`（舟山）且 notice 出现在首段；`clarify` 策略：`needs_scope`、无 map_action |
| 未知 | “绘制东极岛……”“绘制洋山港……” | `needs_scope`，不计算，不给 map_action |
| 越界 | “绘制厦门……”“绘制福建……” | `out_of_region` 澄清；**厦门不得再返回浙江** |
| 无地点 | “明天08时海浪二维图” | `default` 浙江 + notice“未指定地点，按浙江近海参考框” |
| 显式框 | “地图范围：122.1,29.6,122.6,30.1 明天08时海浪图” | `map_bounds` |
| 追问更正 | 朱家尖地图后“同一时刻换成嵊泗” | 不再是 `zhujiajian`；按策略得到父级或澄清 |
| 追问更正 | 朱家尖地图后“同一时刻换成普陀附近” | 同上，不得沿用朱家尖 |
| 追问继承 | 朱家尖地图后“同一时刻换成海面风” | 仍是 `zhujiajian`（不能退化） |
| 路由 | “明天08时嵊泗海浪怎么样” | 进入客观路由并给出父级结果或澄清，不再落到其他业务路由 |
| 反例 | “宁波和舟山明天08时海浪图” | 保持现有多地联合框与分地统计 |
| 反例 | 问句中出现“近海”“海域”泛词但没有地名 | 不产生 unknown |
| 反例 | 未知地名出现在“不是嵊泗，是舟山”中 | 更正后的“舟山”生效（沿用 correction 规则），不澄清 |

---

## 4. P2 掩膜契约：根因与最小修改

### 4.1 证据（附录 A.1，合成场 BOX=[122,29,123,30]，窗口 12 小时、13 个时次）

| 输出 | 波高峰值 | 说明 | 层级 |
|---|---|---|---|
| `stats72` → 报告正文、分区、过程、CSV | **1.0 m @08:00** | 恢复海点被首帧掩膜排除；`sea_count` 23；`missing_count` 0；阈值“>5 m”命中 0；报告 `state=ready`，没有缺口说明 | 【合】 |
| `temporal_maximum` → 逐格最大值图 | **9.0 m @12:00** | 海浪特例：`active_mask` 全为 True（L522），`display_mask = counts>0`（L528） | 【合】 |
| `frame(wave, 12:00, sea_mask=None)` → 地图单时刻海浪 | **9.0 m** | 前端 `params()` 只对风加 `wave_valid`（`objective_panel.js` L45） | 【合】 |
| `frame(wave, 12:00, sea_mask='wave_valid')` → 报告峰值快照 PNG | **1.0 m** | `objective_report.py` L521 对所有要素都用 `wave_valid` | 【合】 |
| `frame(wind, 12:00, wave_valid)` 在恢复海点 | **None** | 海面风在已恢复的海点上也被遮掉 | 【合】 |
| 报告的 map_action（宁波框） | `valid_time=08:00` | 取的是被掩膜后的峰值时刻，地图快照不会显示真实峰值 | 【合】 |
| 始终缺测的点（框内） | 各输出均为 None，计数 0 | 正确：既不当 0，也不当海点 | 【合】 |

**根因【码】**：`_mask`（L315–329）用**源文件首帧**波浪有限值作为海陆掩膜（L322），把“这个格点是不是海”和“这个时次有没有数”混成了一回事。`stats72`（L432）、`frame`、报告 PNG 都沿用这个首帧掩膜；`temporal_maximum` 却对海浪单独放开（L522、L528）。四个出口用了三种语义。

### 4.2 统一契约（写进 `objective.py` 文档和 API 说明）

| 概念 | 定义 | 用途 |
|---|---|---|
| **静态海点** `sea_static[y,x]` | 同数据集波浪源**整条原生时间轴**上任一时次波浪为有限值的格点；映射到其他要素网格时沿用现有最近邻和覆盖裁剪 | 判断“是不是海”；与查询窗口无关，同一源文件内恒定 |
| **时变有效** `valid[t,y,x]` | 该时次该要素为有限值 | 统计、阈值、峰值 |
| 统计域 | `sea_static & valid[t]` | 所有出口统一 |
| 缺测样本 | `sea_static & ~valid[t]` | 报告缺口与 `missing_count` |
| 从未有效 | `~sea_static`（框内） | 不计入海点，也不计缺测；显示透明；单独报告 `never_valid_count` |
| 时有时无的格点 | `sea_static` 中有效时次比例 < 1 的格点 | 单独报告 `intermittent_cell_count`，不改变统计 |

这样修正后：恢复海点在 t0 计为缺测（诚实提示），之后的时次正常参与统计；陆地点永远不会进入统计；四个出口语义一致。

### 4.3 最小修改

| 文件 / 函数 | 修改 |
|---|---|
| `objective.py:_static_sea_mask(dataset)`（新） | 对波浪源全时间轴、全域逐帧读取并做 OR 归约（一次只保留一帧，与 `temporal_maximum` 的内存策略一致），结果按 `(source sha256, 'static_any_valid_v1')` 缓存到 `index/masks/<sha>.npy`，同时记录经纬度轴哈希。L5 等大文件按块读取 |
| `objective.py:_mask` | `sea_mask='wave_valid'` 时改用静态掩膜按原生经纬度取子集；`mask_meta.method='static_any_valid_wave_native_sample_in_source_axis'`，并带 `source_sha256` 和 `never_valid_count` |
| `objective.py:temporal_maximum` L522、L528 | 删除海浪特例：`active_mask = mask`，`display_mask = mask & (counts>0)` |
| `objective.py:stats72` / `_stats` | 不变（已经用 `mask & finite`），只是 mask 换成静态掩膜；`missing_count` 自然包含 t0 的缺测 |
| `objective_report.py` | `REPORT_ALGORITHM_VERSION` 升到 `…-v4`；缓存身份中的 `'mask'` 改成静态掩膜方法名；`missing_count>0` 的缺口文案改为“N 个海上格点-时次样本缺测（含时有时无的格点 M 个）” |
| `objective_panel.js:params()` L45 | 数据集有波浪时，海浪也带 `sea_mask=wave_valid`。按新定义，对波浪本身是恒等变换，作用是让四个出口走同一路径 |
| `objective_bridge._field_product` L91 | 海浪也传 `wave_valid`，理由同上 |

### 4.4 测试（合成 NC，转为 pytest 时使用仓库现有的 h5py 写法，或 scipy classic 写法）

构造一个波浪场：首帧缺测的海点 R 在 t=4 取 9.0（窗口峰值）；框内始终缺测的点 L；另一处次峰 4.0；风场在 R 处为常值，另在 t=9 某点为 20 m/s。

1. `stats72` 峰值 == `temporal_maximum` 峰值 == 报告峰值 == `frame(峰值时刻)` 最大值 == 9.0，位置和时刻一致。
2. 报告的 map_action `valid_time` 等于 9.0 出现的时刻；峰值快照 PNG 由同一时刻、同一掩膜生成（可比较 `frame` 的统计字段）。
3. 阈值 “>5 m” 的命中样本 = 1；“≥9 m” = 1；“>9 m” = 0。
4. t0 的 `missing_count` 包含 R（=1）；L 不计缺测，`never_valid_count`=1；L 在所有数组中为 None，计数为 0。
5. R 处的风在 t≥1 时为有限值并参与风统计；在 t0 依然显示（风本身有值），只是不进入“缺测”计数。这一点需要业务确认【业】：风在波浪缺测时次是否算有效。默认采用静态海点规则。
6. `maxsize` 取 2、80、600，原生峰值、位置、时刻都不变。
7. 回归：现有 `test_objective_process_upgrade.py` 的逐格最大值、计数、阈值时长断言全部保持。
8. 反例：整个窗口都缺测 → 没有峰值，状态 no_data，不出现 0。
9. 反例：某个格点只在窗口**之外**的时次有效 → 属于静态海点，窗口内全部计为缺测（不会消失，也不会算成陆地）。

**风险**
- L5 大文件首次构建静态掩膜会耗时，按源 sha 缓存后只需一次。
- 模型干湿格点会增加缺测计数，用 `intermittent_cell_count` 解释，不改成“陆地”。

**真实数据回归（不收费）**：用真实 NC 统计 `first_frame_mask != static_mask` 的格点数和位置，作为 P2 影响面的实际证据【录】。如果为 0，说明当前数据下 P2 不触发，但契约修改仍然必要。

---

## 5. P3 风浪双要素交付：根因与最小修改

### 5.1 证据

| 现象 | 位置 | 层级 |
|---|---|---|
| 单时刻：每个要素各计算一帧，map_action 只用 `fields[0]` | `objective_bridge.py` L118、L126 | 【码】【合】 |
| 报告：map_action 只用首要素峰值时刻 | L313–323 | 【码】【合】 |
| 前端只执行 `map_action` 或 `map_actions[0]` | `map_assistant.js` L139；探针 `applied_actions:["wave"]` | 【码】【合】 |
| 面板切到风后，时刻和范围不变 | `objective_panel.js` L203、`availableTimes()`；探针：切换后 `valid_time` 仍是海浪动作的时刻 | 【合】 |
| 报告中风速峰值在 17:00，海浪峰值在 08:00；切到风后显示的是 08:00 的风，文字写的是 17:00 的峰值 | 附录 A.2 宁波报告行 | 【合】 |
| “同一时刻风浪都显示” → 文字只剩海浪，地图也只有海浪 | `_explicit_map_fields` 不认“风浪”（`objective_context.py` L33–34）→ field_mode → 规范化问句写入“地图要素：海浪” → `_fields` 只返回 wave（`objective_bridge.py` L56–57） | 【合】 |
| “同一时刻换成风场和浪场” → 只剩海面风 | `_requested_fields` 不认“浪场”（L37） | 【合】 |
| “同一时刻海浪和海面风都显示” → 文字两个要素都有，地图只有海浪 | 同上 | 【合】 |

### 5.2 三种方案比较

| 方案 | 改动 | 优点 | 风险 |
|---|---|---|---|
| **A. 动作组 + 明确切换（推荐，必修）** | 服务端返回 `map_actions` 数组；前端在回答下方显示“本回答图层：海浪 \| 海面风”切换条；当前要素高亮；切换时执行对应动作 | 不混色标；复用现有 `applyAction`、取消和清图逻辑；改动小 | 用户一次只能看一个要素（界面上要说明） |
| B. 波高底色 + 同时刻风箭头叠加（扩展） | `draw()` 增加 `overlay` 参数：底色用海浪 values，箭头用同一时刻的风 u/v，单独加箭头图例 | 一屏就能比较风浪，是常见的气象图式 | 仅限单时刻；风速只能靠箭头长度粗略表达；最大值图不能叠加 |
| C. 双地图分屏（扩展） | 第二个 Leaflet 实例，同步平移缩放 | 两个色标并列 | 瓦片加载翻倍，移动端布局、天地图配额都要评估 |

### 5.3 方案 A 的接口（与现有 `objective_field` / `objective_maximum` 兼容）

**服务端** `objective_bridge.py`

```python
def _map_actions(dataset, fields, *, mode, bounds, valid=None, start=None, hours=None,
                 peaks=None, states=None):
    """mode='field'：所有动作共享 valid；mode='peak'：每个要素用自己的峰值时刻；mode='maximum'：共享 start/hours。"""
    group = 'oa_' + hashlib.sha256(json.dumps([dataset['dataset_id'], fields, mode, bounds,
                                               valid, start, hours]).encode()).hexdigest()[:12]
    actions = []
    for i, name in enumerate(fields):
        if mode == 'maximum':
            a = _maximum_action(dataset, name, start, hours, bounds, states[name])
        else:
            a = _map_action(dataset, name, (peaks or {}).get(name) or valid, bounds, states[name])
        a.update(group_id=group, group_index=i, group_size=len(fields),
                 time_role='peak_of_this_field' if mode == 'peak' else 'shared')
        actions.append(a)
    return actions

# _field_product：result['map_actions'] = _map_actions(..., mode='field', valid=valid)
#                 result['map_action']  = result['map_actions'][0]   # 兼容旧前端
# 报告：mode='peak'，peaks={field: 该要素峰值时刻}；明确要求逐格最大值时 mode='maximum'
```

- `answer_objective`：`current['map_actions']=result.get('map_actions')`，`map_action` 保留。
- 文字首段增加一句：“地图当前显示海浪；本回答还包含海面风，可在图层切换中查看（范围、批次相同；报告模式下各自为本要素峰值时刻）。”

**前端**
- `validAction` 白名单增加 `group_id`、`group_index`、`group_size`、`time_role`，并逐项校验类型和取值范围。
- `map_assistant.js` L139：先取 `map_actions` 数组；校验同一组的 `dataset_id`、`bounds`、类型一致后，调用 `ObjectivePanel.applyActionGroup(actions,{isCurrent})`。旧回答只有 `map_action` 时行为不变。
- `objective_panel.js`：新增 `applyActionGroup`，渲染切换条（按钮文字取 `title`），当前要素高亮，执行第 `primary` 个动作。切换时执行对应动作；**不复用面板下拉的时刻**，因为报告模式下每个要素用各自的峰值时刻。新一组动作到达时，`actionEpoch` 使旧组失效，旧切换条清除。
- 状态栏和图例必须显示“当前：海面风 · 2026-10-21 17:00（本要素峰值时刻）”。

**上下文** `objective_context.product_context`：增加可选 `fields` 列表（服务端生成），`field` 保留为主要素。追问“同一时刻”时继承全部要素；“换成海面风”时收窄为单要素。

**词表统一（必修）**：新建 `forecast_runtime/objective_fields.py` 提供唯一的 `requested_fields(text)`，`objective_context._explicit_map_fields` 和 `objective_bridge._requested_fields` 都改调它：
- `风浪`、`风和浪`、`风浪场`、`浪场和风场` → `['wave','wind']`；
- `浪场`、`涌浪` → wave；
- 风场、风速 → wind；
- 继续保留“换成/改成”之后的对象优先。

`product_question_plan` L175 的 `len(explicit_fields) <= 1` 改为允许多要素的单时刻地图（`field_mode` 带 `fields`），规范化问句写成 `地图要素：海浪、海面风`；`_fields` 解析这个多值标记。

### 5.4 测试

- 单时刻“风浪”：`map_actions` 长度 2，共享 `valid_time` 和 `bounds`；文字两个要素都有。
- 追问“同一时刻风浪都显示”“同一时刻换成风场和浪场”“同一时刻海浪和海面风都显示”：三者都得到两个要素（文字 + 动作）。
- 追问“换成海面风”：只剩风，并继承时刻和范围。
- 报告“风浪简报”：两个动作，`time_role='peak_of_this_field'`，各自时刻等于各要素的报告峰值时刻（与 P2 修复联测）。
- 报告 + “逐格最大值图”：两个 `objective_maximum`，共享 `start`、`hours`，无箭头。
- 前端（cjs）：
  - 收到两个动作时只渲染第一个，切换条有两个按钮；
  - 点第二个按钮会请求 `field=wind` 和该动作自己的时刻；
  - 新回答到达后旧组按钮失效；
  - 第二个动作的 `bounds` 与第一个不同时整组拒绝，但回答仍保留；
  - 白名单外的键整组拒绝；
  - 旧格式单个 `map_action` 的行为不变。
- 反例：数据集没有风时，风动作为 `not_provided`，切换后清图并显示说明，不借用 ECMWF。

---

## 6. P4 精细岸线与带底图导出（扩展，与必修分开）

| 项目 | 方案要点 | 不得做 |
|---|---|---|
| 精细海陆 | 统计仍在原生格点和静态海点上进行。显示层可以用矢量海岸线对格子做**显示裁剪**（canvas clip），并对与海岸线相交的格子加“近岸粗格”标记 | 不插值、不平滑，不用裁剪后的面积重算统计，不宣称“已精确到岸线” |
| 边界数据 | 候选：Natural Earth 10m（公共领域，精度有限）；国家基础地理信息数据或天地图矢量（许可和精度需【业】确认）。记录来源、版本、坐标系（WGS84 或 CGCS2000 与 GCJ-02 的偏移需核对）、缓存哈希 | 不混用偏移坐标系；不用未经授权的商业底图 |
| 带底图导出 | ①服务端用 matplotlib 加矢量海岸线和行政界线绘制（离线、可复现，但不是天地图样式）；②前端截图合成（天地图瓦片多半不允许跨域读取，画布会被污染，需要先确认瓦片服务的跨域策略和使用条款） | 公开发布含底图的地图图片涉及**审图号**等地图合规要求【业】，需业务方确认后再做 |
| 现有 PNG | 保留带经纬度坐标轴的无底图 PNG，用途写清楚“数据图，不是地图截图” | — |

---

## 7. P5 原 P1–P6 问题继续跟踪（不扩大成重写）

| 编号 | 问题 | 证据 | 层级 | 最小下一步 |
|---|---|---|---|---|
| T1 | 覆盖审查对逐句审查已通过的原子提出 `unsupported_claim` 时，`withhold_disputed_atoms` 直接暂缓发布；`drop_rule='final_review_disputed_pending_recheck'`，但**没有任何复核调用**，实际效果是后一个审查覆盖前一个 | `answer_pipeline.py` L87–132、L217、L334 | 【码】 | 冲突键 =（原子文本 sha、fid、引用段集合）。对冲突原子做**一次**定向复核：把审查理由和原段一起交给逐句审查，只判这一个原子。两次结论一致为支持则发布；仍有分歧则暂缓并在回答中写明。预算计入现有 `judge_recheck` |
| T2 | 子问归属：标题继承会把标题下所有原子绑到标题所属子问（`_binding` 的 `inherited_heading`）；父问题对象过宽时出现多重归属（台风专项 4.1） | `typed_grounding.py` L87–118 | 【码】部分；影响面【新】 | 用（对象、谓词、时间角色）三元约束替代单纯对象词命中。有意共享的解释原子必须带 `shared=True` 和理由，否则多重归属只保留得分最高的一个 |
| T3 | 受限归纳：22 条旧记录缺证明字段 | 旧离线诊断 | 【新】 | 首次生成时就要求输出 premises 和关系槽位；用新问答检验，不倒填旧记录 |
| T4 | 17 次正文哈希未匹配 | 旧离线诊断 | 【录】 | 按记录逐条对照当时的 `evidence_view` 截取版本 |
| T5 | 客观报告的语言解读：6 次报告观察到 48 次模型调用，最终展示都是 `business_data`。按代码，只有 `_language_problem` 非空时才会这样，说明解读可能全部被弃用 | `objective_bridge.py` L371–404；`summary.json` | 【码】+【录】 | 用录制统计 `objective_language_state` 的分布；如果多数是 `incomplete_interpretation_not_appended`，改为默认不调用模型，或只在用户明确要求“解读”时调用（开关 `MARINE_OBJECTIVE_INTERPRETATION`，取值 off、on_request、always） |
| T6 | 工具字段在各阶段被误删或重复 | 客观路由以程序答案为主，解读只能追加，静态审查没有发现误删 | 【码】 | 保留现有测试；P3 的 `map_actions` 改造不得改变程序答案正文 |

---

## 8. 实施顺序、可见行为与验收

### 8.1 顺序与开关

1. **P2 掩膜**：`OBJECTIVE_SEA_MASK=static_any_valid`（默认开；`first_frame` 可回滚），报告算法版本升级。
2. **P1 地点**：`OBJECTIVE_UNBOXED_POLICY=parent_with_notice|clarify`，scope 版本升到 v2。
3. **P3 双要素与词表统一**：`map_actions` 与 `map_action` 并存，前端向后兼容。
4. P5 中的 T1、T5（与地图无关，可并行）。
5. P4 扩展：需业务确认后再开。

### 8.2 失败时的可见行为

| 情况 | 文字 | 地图 |
|---|---|---|
| 未知地点 | 澄清（选项见 3.2） | 清除旧图，不显示其他地点 |
| 父级回退 | 首段写明“X 未单独登记，按 Y 参考框” | 定位到 Y 的框，状态栏显示同样的说明 |
| 越界地点 | 说明不在浙江计算范围内，不用浙江结果代答 | 清除旧图 |
| 时次不存在 | 说明该批次没有该原生时刻，不插值、不换时次 | 清图并显示说明 |
| 要素不存在（如气压） | 说明数据集未提供，不借用其他来源 | 该要素的按钮可点，点击后显示“未提供”并清图 |
| 部分覆盖或缺测 | 写出缺测样本数和覆盖交集 | 缺测格透明 |
| 地图请求失败 | 回答完整保留 | 显示“地图暂未显示：原因” |

### 8.3 通用验收标准（与示例地名无关）

1. **范围一致**：回答标题、`scope.id`、`bounds`、map_action 的 `bounds`、报告 `scope` 来自同一个 `ScopeResolution`。有未知地名时，绝不出现浙江或旧地点的数值。用 10 个以上未登记地名（岛、港、湾、县、乡镇、外省城市各若干）做参数化测试，**全部**为 `needs_scope`、`parent_fallback` 或 `out_of_region`。
2. **追问继承**：只改要素或时刻时，范围不变；出现新地名时，范围必须重新解析；用户更正后，旧参数不再继承。
3. **掩膜一致**：同一数据集、窗口、范围、要素下，报告峰值、过程峰值、逐格最大值图峰值、峰值时刻单时刻图最大值四者相等（值、位置、时刻）；`maxsize` 不改变这些结果。
4. **缺测诚实**：缺测不为 0，也不消失；首帧缺测、之后恢复的格点计入缺测样本并在报告中提示。
5. **双要素**：用户明确要两个要素时，文字有两个要素的数值，`map_actions` 有两个；界面标明当前要素和时刻；切换后数据集、批次、范围一致，时刻按 `time_role` 确定；旧请求不会覆盖新图层。
6. **无代答**：任何失败路径都不借用其他批次、来源、时次或地点。
7. **可追溯**：map_action 的数据集、要素、时刻、范围能与回答 evidence 中的 sha、批次逐项对应。

### 8.4 真实数据回归与收费调用最小清单

**不收费（本机，授权 NC）**
- 扩展 `tools/test_objective_upgrade_products_20261021.py`：静态掩膜与首帧掩膜的差异格点数；四个出口的峰值一致性；嵊泗、厦门等地名的 API 级状态；双要素的 `map_actions`。
- 浏览器人工检查：朱家尖、宁波、父级回退三种情况各截一张图，核对图例、批次、时刻、范围和状态栏文字。

**收费（需项目方批准）**：单时刻地图类问答不调用模型，0 次；报告类每次约 8 次（按已有记录 48/6 估算）。

| # | 问题（时刻替换为 catalog 中真实存在的原生时次） | 预计模型调用 |
|---|---|---|
| 1 | 绘制嵊泗明天08时海浪二维图 | 0 |
| 2 | 追问：同一时刻换成普陀附近 | 0 |
| 3 | 绘制厦门明天08时海浪二维图 | 0 |
| 4 | 明天08时朱家尖附近风浪二维图 | 0 |
| 5 | 追问：同一时刻风浪都显示 | 0 |
| 6 | 从某日08时起宁波未来72小时风浪简报，浪高超过2米的时段 | ≈8（T5 若关闭解读则为 0） |
| 7 | 追问：同一时段换成海面风逐格最大值图 | ≈8 或 0 |
| 8 | 地图范围：W,S,E,N 某日08时海浪图 | 0 |
| 9 | 明天08时海浪二维图（未指定地点） | 0 |
| 10 | 无覆盖日期的报告请求 | 0 |

合计约 16 次（关闭 T5 解读则为 0）。每次保留原始响应和 trace，并逐项核对 8.3 的验收标准。

---

## 附录 A 本轮合成复现记录

文件位于本轮交付附带的 `objective_review_harness_20261021.zip`。在仓库根目录之外运行，脚本把仓库加入 `sys.path`。

### A.1 合成 NC（`make_nc.py`，节选）

```python
LAT = np.arange(26.5, 31.5001, 0.25); LON = np.arange(119.5, 123.5001, 0.25); HOURS = range(13)
wav = 1.0 everywhere; u, v = 3, 4 (5 m/s)
wav[0,  R] = -999 (首帧缺测)；wav[4, R] = 9.0（窗口峰值），R=(29.75N,122.5E)
wav[:, L] = -999；u/v[:, L] = -999（始终缺测）
u/v[9, (29.0N,122.5E)] = 12/16 → 20 m/s（风峰值在另一个时刻）
文件名 PZJ_BEN_OSM_ZJS_202610210800_{WAV,SSW}_00_L2.nc；time units 'hours since initial time'；_FillValue=-999
```

### A.2 本轮实际输出摘要

```
stats72 wave peak                      1.0 m @08:00 (sea_count 23, missing_count 0, ">5m" hits 0)
temporal_maximum wave peak             9.0 m @12:00 (29.75N,122.5E)
frame(wave,12:00,sea_mask=None)        9.0 m      ← 地图单时刻
frame(wave,12:00,sea_mask=wave_valid)  1.0 m      ← 报告峰值 PNG
frame(wind,12:00,wave_valid) @R        None
always-missing L in maximum/frame      None, count 0
report(wave,wind) state                ready, wave peak 1.0 m @08:00

resolve:   嵊泗/嵊泗县/象山/象山港/洋山港/东极岛/厦门/东海/杭州湾 → zhejiang
           福建/上海 → clarification；舟山普陀 → zhoushan；朱家尖 → zhujiajian
answer:    "绘制嵊泗…海浪二维图" → 标题“浙江近海参考统计框二维预报图”，全省最大 9.0 m
follow-up (from 朱家尖 map): 换成普陀附近 → zhujiajian；换成嵊泗 → zhujiajian；换成宁波 → ningbo；
           换成海面风 → zhujiajian + wind
two fields: 同一时刻海浪和海面风都显示 → text [wave, wind], map wave
            同一时刻风浪都显示       → text [wave],       map wave
            同一时刻换成风场和浪场   → text [wind],       map wind
route gate: “明天08时嵊泗海浪怎么样” objective_question=False；“…舟山…”=True
```

### A.3 运行方式

```text
export REPO=<仓库根目录>        # common.py 用它设置 sys.path；合成文件写到 harness/work/
python3 -I p2_mask.py          # P2 掩膜一致性
python3 -I p1_p3_routes.py     # P1 地点、追问继承，P3 双要素
python3 -I p1_names.py         # P1 地名解析与路由门槛
cd $REPO && node <harness>/probe_switch.cjs   # 需在仓库根目录运行，读取 tests/ 与 frontend/
```

`stub/h5py.py` 只提供 `is_hdf5()→False`，用于本审查环境缺 h5py 的情况；仓库测试环境有 h5py 时，不需要这个替身。

### A.4 前端探针输出

```
{"after_switch_field":"wind","valid_time":"2026-10-20T20:00:00+08:00","bounds":"120,27,124,32","sea_mask":"wave_valid"}
{"applied_actions":["wave"]}
```
