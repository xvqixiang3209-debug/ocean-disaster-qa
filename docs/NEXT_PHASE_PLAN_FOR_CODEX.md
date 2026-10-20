# 海洋灾害问答系统下一阶段方案（交 Codex 执行）

- 评审对象：`xvqixiang3209-debug/ocean-disaster-qa`，commit `a6113ee`，版本 `phase1.8.3-public-evidence`（2026-10-19）
- 评审范围：README、`docs/CLAUDE_HANDOFF.md`、`docs/DATA_ASSETS.md`、`docs/SNAPSHOT_VALIDATION.md`，`marine_kb/` 问答主链代码，`evaluations/` 全部记录
- 本轮约束：只做诊断和设计；没有改代码，没有调用 DeepSeek，没有访问生产服务。文中“确证”指代码路径和真实 trace 能同时证明；“待验证”指需要补充 trace 或重放才能定论
- 行号均对应上述 commit

---

## 0. 结论摘要

1. **G3 不是缺数据。** 系统已经取得过渡期生活救助资金条款（据逐句判定理由，出自 2024 年修订的国家救助预案类资料）、冬春救助“户报、村评、乡审、县定”程序和宁波公共巨灾保险理赔流程的正文段落。最终只剩一句生效日期，原因是“恢复”链路有 5 个确证缺陷叠加：
   - 逐句校验只认“原段直接支持”，没有“受证据约束的归纳”，所以“三者不是一回事”这个核心结论被删掉了；
   - 复合句只要局部失配就整句丢弃；
   - 文件名、发文机关等身份信息只存在标题里，被判为“仅标题相关”；
   - 限制性语句被一条正则过滤掉了，而审计记录里仍显示 `limitation`；
   - 恢复后没有“最少交付”门槛，覆盖审查失败时照样发布残片。
2. **主路径和恢复路径的事实标准不一致。** 主路径 `_validate_model_answer` 只核对带数字的行和引文是否存在。恢复路径对每句 ≥8 字的话都要求原段直接蕴含。同一句“国家自然灾害救助应急预案规定……”在 G1 能通过，在 G3 被删。结果取决于有没有走进恢复路径，而不是事实本身。
3. **A2/C3/M3 的额外缺项来自数据流，不只是提示词问题。**
   - `evidence_applicability` 给通用资料加上“来源未明确覆盖所问地点”；
   - 标题含“预报”的科普页被判成 `forecast`，因而带上“未核实有效时段”；
   - 这些理由经 metadata 原样送进生成模型。
   - 覆盖审查已经精确指出 `unasked_gap`，但系统唯一的处理办法是让模型重写，重写到第 3 次后原样发布。
4. **M1 的鼠媒/蚊媒混写是“联合主语塌缩”。** 原文写“鼠类和蚊虫……可能传播出血热、钩体病、登革热、基孔肯雅热”。回答放在“蚊子多会不会传染病”之下转述，读者会把出血热、钩体病理解成蚊媒病。防范规则只写在提示词里（`public_service_intents.py:115`），没有程序检查，覆盖审查也判了 pass。
5. **最小有效改造**：先补可观测性和离线重放（P0），再依次做统一子问契约（P1）、证据对象与文档卡（P2）、分型逐句校验与局部恢复（P3）、范围闸门（P4）、关系守卫（P5）、连接器（P6）。不更换 DeepSeek，不整体重写，保留 CRAG、地图整合、连续追问和预报模块。工具调度作为 P7 可选项，只用于数据型业务路由。

---

## 1. 这个快照能验证什么、不能复现什么

| 能在快照内完成 | 不能在快照内完成 |
|---|---|
| 阅读并静态追踪全部问答控制流；`claim_grounding.draft_claims`、限制语过滤、`decode_review` 等纯函数可直接离线重放（本评审已用 G3 的 11 条陈述离线复现：2 条 `limitation` 都被过滤器丢弃） | 重现 G3 的原始生成稿、每轮覆盖审查的 issues、补全审查的具体 ValueError（记录中没有保存） |
| 用 mock/录制回放（P0 新增）对新逻辑做确定性单元测试和接口测试 | 本地知识库检索（`knowledge.sqlite3` 2797 源 / 25101 切片未上传）、历史台风库、NC/天文潮计算、空间格网查询 |
| 校验评测 JSON、答案哈希、源码哈希一致性 | 任何真实 DeepSeek 回答质量（需要凭据，且属收费调用，须用户授权） |
| 用已导出的官方网页片段设计负例测试 | 网页实时可读性（nhc.gov.cn 等站点会变，且有访问限制） |

> 本评审环境无法安装 `requirements.txt`（无 PyPI 网络），未重跑 1031 项测试，沿用 `SNAPSHOT_VALIDATION.md` 的记录。Codex 在本地环境执行 P0 前，先确认 `pytest tests -q` 仍为 1031 passed。

---

## 2. 现有架构与关键数据流

### 2.1 入口与装配

| 环节 | 文件 / 函数 | 输入 | 输出 |
|---|---|---|---|
| HTTP | `marine_kb/chat_api.py: create_chat_blueprint().ask` | `question`、`conversation_id`、`selected_sid` | 调用 `qa.prepare_question` 与 `qa.answer(..., allow_web=True, question_plan=plan)` |
| 装配 | `marine_kb/bootstrap.py: install` | 环境变量 | `MarineQuestionService(search_policy='always', crag_enabled=True)`；`coverage_review_enabled` 默认 true（L54）；`OfficialWebSupplement` |
| 模型调用 | `qa.py: MarineQuestionService._call_model`（L642–650）→ `model_layer.call_model` | system、prompt | 字符串；`timeout=25`、`max_retries=0`、`temperature=.1`、JSON 模式；**不返回 token 用量** |

### 2.2 问答主链（CRAG 开启时）

```
ask → prepare_question → _normalize(NORMALIZE_PROMPT)            [模型调用 1]
    → _answer
        ├─ system_question → joint_evidence(pinned=system_hit)
        ├─ forecast_runtime.matches → ForecastRuntime.answer      [确定性工具 → _organized_answer]
        └─ _hybrid_answer → joint_evidence → CorrectiveRetrieval.retrieve
              ├─ _local(_collect_local_hits) ‖ _fetch_public(OfficialWebSupplement.fetch)
              ├─ select_evidence（本地 5 / 网页 6）→ _prepare（逐 facet assess_page）
              ├─ _evaluate(CRAG PROMPT)                            [模型调用 2]
              ├─ 缺口 → 补检 + 再评估                               [模型调用 3]
              └─ 选源、重编号、写 crag_passage_ids、crag_evidence_plan
           → _organized_answer
              ├─ 证据封装：text[:3000]、_evidence_passages(400/80)、按 crag_passage_ids 过滤
              ├─ 生成 STRUCTURED_ANSWER_PROMPT（最多 2–3 次）       [模型调用 4–6]
              ├─ _decode_model_answer / _validate_model_answer / validate_business_delivery
              ├─ coverage_review（每次生成后）                     [模型调用 +1/次]
              ├─ 失败 → claim_grounding.review_claim_batches       [模型调用 +1]
              │        → apply_attribution → answer_completeness [模型调用 +1]
              └─ 返回 answer + sources + answer_meta
    → crag.finalize → （可选）guidance 通用解释兜底
```

### 2.3 各阶段文件、函数与输入输出

| 阶段 | 文件:行 | 输入 | 输出 / 关键行为 |
|---|---|---|---|
| 问题拆分 | `qa.py:_normalize` L652–719；`_question_parts` L133–153 | 原问题、历史 | `normalized_question`、`retrieval_queries`≤4、`subquestions`≤6，`{id,question}` 只是序号，没有类型 |
| 问题契约 | `question_contract.py:question_contract` L5–23 | 整题文本 | 整题一个 `mode`（scenario / dated_facts / method_or_knowledge / unspecified），**不按子问区分** |
| 本地检索 | `qa.py:_collect_local_hits` L1437–1493；`retrieval.search` | 问题、子问查询 | ≤8 条命中；动态题清空档案 |
| 网页检索 | `web_supplement.py:OfficialWebSupplement.fetch` L518–755 | 问题、子问 | 查询 ≤10；读取 ≤12 页；`focused_public_text` 截取 ≤2400 字；hit `text[:3000]`（L707）；`assess_page` 写 `applicability` |
| 正文聚焦 | `web_supplement.py:focused_public_text` L164–274 | 全文、子问 | **先放“适用/施行/废止”段（L233–240），再放 focus_groups 段，再放词频段**，以 `\n\n` 拼接，不保留原顺序和标题链 |
| 适用性 | `evidence_applicability.py:assess_page` L254–360；`_scope` L188–212；`document_kind` L170–185 | 子问、页面 | `kind/use/geography/temporal/reasons`；policy 且未核现行 → `use=reference`（L345–346） |
| CRAG 评估 | `crag.py:CorrectiveRetrieval._evaluate` L101–141 | 子问、≤16 候选（各取 `text[:3000]` 切成段） | 按 facet 给 correct/ambiguous 及段号；`use=reference` 强制降为 ambiguous（L129） |
| CRAG 选源 | `crag.py:retrieve` L157–234 | 决策 | ≤10 源；`crag_passage_ids` 写入 metadata；`remap` 重编号（L218–221） |
| 证据封装 | `qa.py:_organized_answer` L1084–1099 | hits | `evidence[i] = {citation,title,locator,status,metadata,text}`；生成器只看 `passages`（被 `crag_passage_ids` 过滤），不看 `text` |
| 覆盖矩阵 | `evidence_applicability.py:coverage_matrix` L431–447 | 子问、hits | `candidate_citations` / `reference_citations` |
| 生成 | `qa.py` L1157–1282 | payload（约 30 个指令字段） | JSON：`answerability/answer/evidence_quotes/missing_information/subquestion_coverage` |
| 主校验 | `qa.py:_decode_model_answer` L177–241，`_validate_model_answer` L387–421 | 原稿、证据 | 引文段存在；**只对含数量的行核数字**（L406–417）；单位、表格、现行版本 |
| 覆盖审查 | `coverage_review.py`；`qa.py` L1208–1232 | 答案、缺项、`cited_passages=text[:3000]` | `unasked_gap` → 重写（L1222–1230）；`unsupported_claim/contradiction` → 抛 `semantic_evidence_support_failed`（L1231–1232） |
| 子问标签 | `qa.py:_part_coverage` L156–174；L1233–1256 | 模型自报覆盖 | 有缺项即 full→partial（L171–172）；适用性不匹配 → `subquestion_evidence_scope_mismatch` |
| 逐句恢复 | `claim_grounding.py`；`qa.py` L1336–1406 | 失败原稿 | 按句切分（L77）→ 模型判定 → 精确引文与数字复核 → 只保留 supported 与少量 limitation |
| 恢复后覆盖 | `answer_completeness.py`；`qa.py` L1371–1398 | 恢复答案 | 失败则全部 partial，缺项写成“待核对” |
| 最终来源 | `qa.py:_sources_with_selected_passages` L298–317；L1399 | 引用 | 只列被保留陈述引用的来源 |
| 数据工具 | `forecast_runtime/service.py:ForecastRuntime.answer` L756+；`answer_facts.delivery_facts` | 问题 | 资料1 = 程序计算文本；`required_business_facts` 强制交付（`business_validation.validate_business_delivery`） |

### 2.4 结构性观察

- **同一份证据在不同阶段有三种视图。** CRAG 评估看前 3000 字的全部段落；生成器和逐句校验只看 `crag_passage_ids` 选中的段（qa.py L1093–1096、L1099、L1347）；覆盖审查看 `text[:3000]` 全文（L1214）。三方对“原文有没有”的判断依据不同。
- **子问只有序号，没有类型和范围。** CRAG facet、生成 `subquestion_coverage`、补全审查都靠 `id` 对齐，但没有任何一层知道“这个子问要的是结论、依据文件、程序还是当前状态”。
- **校验结果大多由模型给出，再由正则把关。** 正则覆盖不全时会静默丢弃内容，例如限制语过滤。

---

## 3. G3 端到端追踪

问题：“台风后政府的过渡期生活救助、冬春救助和巨灾保险赔付是不是一回事？分别依据什么办理？”
记录：`evaluations/failures/G3.json`（与 `public_services_12.json` 中 G3 完全一致），耗时 47.6 秒，HTTP 200。

### 3.1 逐阶段

| # | 阶段 | trace 事实 | 代码 | 判断 |
|---|---|---|---|---|
| 1 | 拆分 | 4 个 facet：①是否一回事 ②过渡期依据 ③冬春依据 ④巨灾保险依据 | `_normalize` | 拆分正确；facet①是“区别/归纳”类，但系统没有这种类型 |
| 2 | 两路检索 | 本地 5 候选、选 0；网页查询 6 条，读取候选 9 个，2 个读取失败（typhoon.nmc.cn、typhoon.slt.zj.gov.cn，均为 JS 页面且与救助无关） | `crag.retrieve`；`fetch` | 网页取得了相关正文；两次失败的读取白白耗时 |
| 3 | CRAG 首轮 | facet③ supported、④ supported；① partial（ref 3、7、5）；② partial（ref 7） | `_evaluate` | 见第 5 行 |
| 4 | 补检 | 查询 facet①②，新增 6 源，状态不变 | `crag.py` L172–201 | **花了钱没有改善**：② partial 的原因不是缺资料（见下行） |
| 5 | ② 为何 partial | facet② 只有 reference_sources [3]。代码中，未核现行的 policy → `use=reference` → CRAG 强制 ambiguous；C3 资料5、G2 资料2 的导出记录可见同一机制 | `evidence_applicability.py` L345–346；`crag.py` L129 | **机制确证；G3 中是否由该规则触发待 P0 确认**（资料3 的 metadata 因未被最终引用而没有导出，也可能是评估模型自己选了 reference）。对“依据什么办理”这类问题，未核现行的国家预案本来就是正确答案，加版本说明即可 |
| 6 | 选源与封装 | 最终 4 个网页源；生成器只看各源被选中的段 | `crag.py` L203–221；`qa.py` L1093–1096 | 资料4（宁波方案）的段1以“本方案自2024年4月30日起施行。”开头 |
| 7 | 段落来源 | 宁波方案正文中，“施行”句被 `focused_public_text` 提到最前，紧接理赔段 | `web_supplement.py` L233–240 | **确证**：唯一存活的陈述正来自这个被前置的元信息句 |
| 8 | 生成 | 原稿至少 11 句（恢复记录 `claim_count=11`），结构为“总结论 + 1. 是否一回事：不是 + 2/3/4 各自依据” | L1157–1282 | 原稿内容切题 |
| 9 | 覆盖审查 | 最终报出 `semantic_evidence_support_failed` | L1231–1232 | **确证**：触发原因是审查返回了 `unsupported_claim` 或 `contradiction`；**具体是哪几句没有保存**（恢复分支的返回 L1401–1406 不带 `review`，导出为 `coverage_review: null`） |
| 10 | 逐句恢复 | 11 句：supported 1、limitation 2、unsupported 8 | `claim_grounding.py` | 见 3.2 |
| 11 | 恢复后覆盖 | `recovered_completeness_review: {state: unavailable, error_type: ValueError}` | `qa.py` L1393–1398 | **确证**：只记录了异常类型，没有记录错误码 |
| 12 | 发布 | 答案 = 唯一 supported 句 + 固定后缀；来源只剩资料4 | `claim_grounding.py` L209–211；`qa.py` L1399 | **确证**：没有最少交付门槛 |

### 3.2 逐句判定逐条核对（`claim_attribution_recovery.decisions`）

| id | 陈述（节选） | 判定 | 原因类别 | 删除是否合理 |
|---|---|---|---|---|
| 1 | 过渡期、冬春属政府救助制度，巨灾保险属保险理赔，三者依据、主体、程序不同 | unsupported | 归纳，无单段直接表述 | **不合理**：应作为“受证据约束的归纳”，前提是 3/7/9 中被支持的部分 |
| 2 | 1. 是否一回事：不是。 | unsupported | 归纳 | **不合理**：这是 facet① 的核心答案 |
| 3 | 过渡期……下达[资料3]；冬春……[资料1]；巨灾保险……[资料2] | unsupported | 复合句，第一分句判“不在资料3原段” | **部分不合理**：模型自己承认冬春、巨灾保险两个分句有支持；整句丢弃 |
| 4 | 过渡期依据《国家自然灾害救助应急预案》办理 | unsupported | 原段无文件名 | **大概率不合理**：文件身份应核对元数据而不是正文段。G1 引用同一预案时资料标题为“国务院办公厅关于印发《国家自然灾害救助应急预案》的通知”；G3 资料3 的标题未随记录导出，P0 确认 |
| 5 | 该预案规定……财政部会同应急管理部……下达过渡期生活救助资金 | unsupported | 原段有内容，但无文件名 | **不合理**：内容有支持，文件名问题同 4 |
| 6 | 该资料为2024年修订版，但现行有效性尚未核实[资料3] | limitation，但**未保留** | 限制语过滤要求不带引用（L192） | **不合理**：这是诚实的版本说明 |
| 7 | 冬春救助依据……通过国家自然灾害救助资金管理系统……“户报、村评、乡审、县定”[资料1] | unsupported | 原段缺“应急管理部会同财政部”和系统全称 | **可能合理**：G1 中“国家自然灾害救助资金管理系统”出自另一资料（新闻发布会），原稿可能确有错误归因（**待验证 H4**） |
| 8 | 4. 巨灾保险赔付依据：依据保险合同及地方公共巨灾保险实施方案 | unsupported | 归纳 | 应作为归纳处理，前提是 9 和资料2 |
| 9 | 以宁波为例，依据《宁波市公共巨灾保险工作实施方案（2024—2026年）》……按合同支付 | unsupported | 原段无完整文件名 | **不合理**：资料4 标题就是该通知标题 |
| 10 | 该方案自2024年4月30日起施行[资料4] | supported | — | 保留合理，但单独发布没有意义 |
| 11 | 其他地区的具体依据，现有资料未覆盖 | limitation，但**未保留** | 用的是“未覆盖”，不在正则词表里（L192） | 实际是**未问缺项**，删除结果碰巧正确，但原因不对 |

另一处异常：第 3、5 句在**同一次**判定调用中针对同一个资料3原段，一个说“不在原段中”，一个说“原段有资金下达表述”。这说明判定模型对复合句不稳定，不能把单次判定当作确定结论。

### 3.3 确证原因与待验证假设

**确证（代码 + trace 均可证明）**

- **C1 归纳没有合法类别。** `claim_grounding.PROMPT`（L37–51）只有 supported / limitation / unsupported / irrelevant，并明令“原文直接支持该句的全部事实”。比较题的结论句天然没有单段原文，必然被删。
- **C2 复合句全有或全无。** `apply_attribution` 对每个 claim 只给一个状态（L147–200），不支持分句保留。
- **C3 文档身份不能由元数据核对。** 生成器能看到 `title`（qa.py L1087、L1099），判定提示却要求“仅标题相关 → unsupported”。主路径不核对没有数字的句子（L406–417），所以同类句子在 G1 通过、在 G3 被删。
- **C4 限制语过滤错杀，审计也有误导。** L192 要求句子不带引用，并且必须命中有限词表。决策记录写的是 `limitation`，实际并未保留（`retained_claims=1`）。本评审已离线重放确认。
- **C5 没有最少交付门槛。** 只要有 1 条引文就返回（L209）。`qa.py` L1354–1406 不检查各 facet 是否交付，completeness 失败时仍然发布。
- **C6 诊断字段缺失。** 恢复分支丢掉了 `coverage_review` 和每次生成的 `validation_error` 序列；completeness 只记录 `ValueError` 类型。
- **C7 版本未核即降级（机制确证）。** policy 且 `version_unverified` → `reference` → CRAG 强制 ambiguous。G3 中 facet② partial 并触发了一轮没有改善的补检（新增 6 源，状态不变，多 1 次检索、1 次模型调用）；这一轮是否正由该规则触发，归入 H7 待验证。
- **C8 元信息句被前置。** `focused_public_text` 把“施行/适用”句放到最前（L233–240），切段后与操作条款粘在同一段。

**待验证（需要 P0 trace 或重放）**

- **H1** 覆盖审查判为 `unsupported_claim` 的是哪几句，第 3 次生成稿与第 1 次有何不同。
- **H2** 资料3的 `crag_passage_ids` 是否漏掉了含过渡期资金条款或文件名的段。如果是，第 3 句“不在原段”的判定可能是对的，问题出在 CRAG 选段过窄。
- **H3** completeness 的具体错误码，可能是 `delivery_span_not_in_answer`、`partial_delivery_needs_specific_gap` 或 `delivery_citation_not_in_span`。
- **H4** 原稿第 7 句是否把新闻发布会里的内容错归到资料1。如果是，删除正确；修复不能使这类句子“复活”。
- **H5** 重编号是否引入不一致。代码上 `remap`（crag.py L218–221）与生成器编号（qa.py L1100–1101）一致，风险低，P0 用每阶段段落哈希断言确认。
- **H6** 3000 字截断是否截掉了长预案的操作段（`web_supplement.py` L707、`qa.py` L1089、`crag.py` L105 三处都有）。
- **H7** G3 资料3 的标题与 `applicability`（是否为国务院预案、是否 `use=reference`），以及 facet② 为 reference 是规则强制还是评估模型自选。

### 3.4 同类问题的规模

- 旧 160 题“修复后”共 7 题答案带恢复后缀“部分子项的依据尚未核对完整”（Q-024、Q-038、Q-044、Q-058、R-009、R-091、R-094）。其中 **R-094 与 M1 是同一问题**，当时也退化成一句元说明加后缀（72 字），说明这是一类会重复出现的退化，不是 G3 独有。
- 11/160 题出现 validation error，27/160 走 `general_explanation` 兜底（无证据的通用解释）。

---

## 4. A2 / C3 / M3 范围问题，M1 关系问题

### 4.1 A2（温州网箱）和 C3（路径能否替代预警）

| 事实 | 来源 |
|---|---|
| A2 资料1（农业农村部网箱技术手册）`applicability.reasons=['来源未明确覆盖所问地点']`，四个 facet 都是 `use=direct` | A2.json；`evidence_applicability.py` L198–204：问题有地名、来源无地名、kind=knowledge → `general` 加该理由 |
| C3 资料2“台风的预报与防御”被判 `kind=forecast`、`temporal=validity_unknown`、理由“未核实有效时段” | `document_kind` L179：标题含“预报”且不含“方法/系统/规范/介绍/如何” |
| `applicability` 原样进入生成 payload | `qa.py:_safe_metadata` L335 允许 `applicability`；L1087 |
| 生成稿把这些理由写成缺项：“温州专用规程”“当前实时发布状态” | A2/C3 `missing_information` |
| 覆盖审查准确识别为 `unasked_gap`，target 精确到 `m1/m2` | `coverage_review` 输出 |
| 系统对 `unasked_gap` 只做重写（L1222–1230）；最后一次仍有 `unasked_gap` 时直接发布（L1231 只对 unsupported/contradiction 抛错） | qa.py |
| 只要有缺项，`_part_coverage` 就把 full 降为 partial（L171–172） | qa.py |
| G1、G2 最终记录也带 `verdict: revise` 和同类 `unasked_gap` | public_services_12 |

**确证根因**：契约只有整题一个 mode，地名在“方法题中作背景”还是“要求当地规定”无从区分。适用性理由不区分“限制使用”和“仅作说明”，统统作为生成器输入，生成器就照写成缺项。审查层能识别问题，但审查结论只能触发概率性的重写，不能直接修正缺项列表。

### 4.2 M3

“蚊子多是否说明暴发登革热”：回答解释了“不能推出”，又因缺当地监测列 partial。机制同 4.1：本题不要求当地监测状态，facet 类型应为“风险关系判断”。

### 4.3 M1 鼠媒蚊媒混写

- 资料2原段：“洪水过后，**鼠类和蚊虫**活动增加，可能传播出血热、钩体病、登革热、基孔肯雅热等疾病。”
- 回答在“会存在虫媒传染病风险”之后照抄这句。逐字看没有错，但整段答的是“蚊子多会不会传染病”，读者会理解为出血热、钩体病也是蚊传。
- 另外，回答写了“菊酯类滞留喷洒”。`GENERATION_GUIDANCE` L114 写着“未问配药……不提供杀虫剂配方”。喷洒菊酯算不算“配方”有争议，但它说明提示词规则没有程序执行。
- `coverage_review` 判 pass，模型标签 full。
- **确证根因**：没有任何一层检查“主语—关系—宾语”。L115 的规则只是提示词。
- **待验证**：需要医学专业人员确认“风险提示”与“疾病—媒介对应”在回答中的最低表述要求。

---

## 5. 根因分类

| 类别 | 根因 | 真实案例 | 代码位置 | 状态 |
|---|---|---|---|---|
| **缺业务数据** | 指定日期地方指令、灾情、转移人数、停运复航、海堤台账、预警平均提前量无可读来源 | Q-030、Q-045、R-016、R-015、R-046、R-066、R-079（均为“未回答”） | `forecast_runtime/`、`official_warnings.py`；`remaining_data` | 确证缺数据；部分可由公开公告归档补，详见第 9 节 |
|  | 浙江省级预警接口无访问凭据；SWL/SSW 语义、高程基准、MWD 角度定义未核；谱峰周期变量缺失 | `previous_160.remaining_data`；Q-022（试算最高总潮位 685.86 cm 依赖 1985 基准与“SWL 纯增水”假设） | `forecast_runtime/service.py`、`answer_facts.py` | 确证为口径未核，不等同数值错误 |
| **连接器 / 正文提取** | nhc.gov.cn 14 次“网页返回错误或无可读取的正文”，正是蚊媒类最权威的来源 | M1/M2/M3 | `typhoon/web_reader.py`；`fetch` L601–602 | 确证失败；具体 HTTP 码未记录（待验证是否为反爬 412） |
|  | 与问题无关的 JS 路径页在 6/12 题中被读取并失败（含救助、养殖题） | G2、G3、A1–A3、C3 | `fetch` 候选构造 L553–592 | 确证浪费；来源（搜索结果或参考页）待验证 |
|  | 正文聚焦打乱原文顺序、丢失章节标题链、把元信息句前置；三处 3000 字截断 | G3 资料4 段1 | `focused_public_text` L164–274；L707；qa L1089；crag L105 | 确证；H6 待验证 |
|  | 文档元数据只有 title 和 publication_date；`document_issued_at` 只对自然资源部附件提取（L616–617）；没有文号、机关、施行日 | G3 第 4、5、9 句 | `web_supplement.py` L616–721 | 确证 |
| **检索及证据选择** | 未核现行的 policy 一律 `use=reference`，CRAG 强制 ambiguous，可能触发无效补检 | C3 资料5、G2 资料2（机制可见）；G3 facet②（H7） | `evidence_applicability.py` L345–346；`crag.py` L129 | 机制确证；G3 待验证 |
|  | CRAG 选段过窄后，生成器和校验器都只能看到选中的段 | G3（H2） | `qa.py` L1093–1096 | 待验证 |
|  | 三个阶段的证据视图不一致 | 全部 CRAG 路径 | qa L1099、L1214、L1347；crag L105 | 确证 |
| **范围判定** | 契约按整题判断，不知道地名在 facet 中的角色 | A2、C3、M3、G1、G2 | `question_contract.py` | 确证 |
|  | 通用知识也带地点理由；标题含“预报”的科普页被判 `forecast` | A2、C3 | `evidence_applicability.py` L204、L179 | 确证 |
|  | `unasked_gap` 只能触发重写，不能确定性修正 | A2、C3、G1、G2 | qa L1222–1231 | 确证 |
| **生成及校验恢复** | 无归纳类别、复合句全删、标题身份不认、限制语错杀、无最少交付、主路径与恢复路径标准不一 | G3、R-094 | `claim_grounding.py`；qa L1336–1406；L406–417 | 确证（C1–C5） |
|  | 关系与主语范围无检查；提示词规则无程序执行 | M1 | `public_service_intents.py` L114–115 | 确证 |
|  | 判定模型对同一原段前后矛盾 | G3 第 3 句与第 5 句 | — | 确证 |
| **评测问题** | full/partial 标签由同一个 DeepSeek 给出；160 题早于最后修改；只有 12 题专项 | 全部 | `previous_160.review_method` | 确证 |
|  | 缺失诊断字段，无法区分是模型错还是管线错；无 token 用量；无每阶段耗时 | G3 | `model_layer.call_model` 返回 str | 确证 |
|  | M1 被判 full 但存在关系风险，说明标签未覆盖“关系正确性” | M1 | — | 确证 |

---

## 6. 设计原则：陈述分型与验证方法

后续所有阶段共用下面这套陈述分型。

| 类型 | 定义 | 可引用的证据 | 验证方法 | 失败处理 |
|---|---|---|---|---|
| `direct_fact` | 原文直接表述的事实、条款、步骤 | 正文段 | 精确段号 + 数字/单位校验 + 模型蕴含判定（分句后） | 分句保留被支持的部分；整句不再全删 |
| `document_identity` | 文件名、发文机关、文号、发布/施行日期、地区、版本状态 | **文档卡**（第 0 段，由元数据确定性生成） | **确定性字符串比对**，不调用模型 | 不一致则删除；不得用来支持内容事实 |
| `synthesis` | 比较、分类、“不是一回事”、“依据合同而非财政”等受证据约束的归纳 | 不直接引段；列出 `premises`（同答案中已通过的 claim id） | 前提全部通过 + 不引入前提和问题以外的新实体或数字（确定性）+ 模型仅凭前提判定能否推出 | 前提不足则降级为“已知各自依据如下”，不发布结论 |
| `calculation` | 程序计算值 | 资料1（程序记录） | 现有 `validate_business_delivery` + 单位、基准、时刻一致 | 维持现状 |
| `gap` | 用户问到但缺依据的具体项目 | 无 | `gap_type` 必须在该 facet 的 `allowed_gaps` 中 | 不允许的移入 `scope_note` |
| `scope_note` | 适用范围、版本未核、地方以当地为准等说明 | 可引用对应资料或文档卡 | 不含新的肯定事实或数字（确定性） | 每个来源最多一条，不计入缺项 |

“引用都对但没有回答”不算成功：每个 facet 必须至少交付一条 `direct_fact` / `synthesis` / `calculation`，或者一条允许类型的 `gap`。

---

## 7. 分阶段实施

所有新行为都放在环境开关后面（默认关，先影子运行、再开启）；每个阶段可单独回滚。依赖顺序为 **P0 → P1 → P2 → P3 → P4**；P5、P6 依赖 P1；P7 可选。

### P0 可观测性与离线重放（不改变任何回答）

**目标**：让 G3 这类失败能确定定位，并让后续改动能用真实模型输出做确定性回归。

| 修改文件 | 内容 |
|---|---|
| `model_layer.py:call_model` | 新增 `return_usage=False` 参数；为 True 时返回 `(text, usage)`，`usage` 取 `response.usage`（prompt/completion tokens）。默认行为不变 |
| `marine_kb/trace.py`（新） | `AnswerTrace`：按调用记录 `stage, system_id, prompt_sha256, response_sha256, latency_ms, usage, error_code` |
| `marine_kb/qa.py:_call_model` | 改用包装器：写入 `self._trace`（线程局部）；`stage` 从调用方传入 |
| `qa.py:_organized_answer` | 每次生成记录 `attempt, validation_error, coverage_review(issues), part_rows`；恢复分支返回时加入 `coverage_review_history` 和 `attempt_errors`；completeness 失败时记录 `str(exc)`（错误码均为小写 ASCII，可安全输出） |
| `claim_grounding.apply_attribution` | 决策加 `retained: bool` 和 `drop_rule`（如 `limitation_has_citation`、`limitation_vocab_miss`） |
| `crag.py:retrieve` / `qa.py` | 每阶段记录 `{citation: [passage_id, sha256(text)]}`，断言跨阶段一致（验证 H5） |
| `web_supplement.fetch` | `read_failures` 增加 `http_status`、`final_url`；记录 `skipped_irrelevant` |
| `tools/replay/`（新） | `RecordingCaller` 把 `(system_sha, prompt_sha) → response` 存成 cassette JSONL（含原文，**只存本机，不进 Git**）；`ReplayCaller` 离线重放；网页读取同理（`ReaderCassette`） |

**数据结构**

```python
# marine_kb/trace.py
@dataclass
class CallRecord:
    stage: str            # normalize|crag_eval|crag_eval_corrective|generate|coverage_review|claim_grounding|completeness|citation_repair|guidance
    attempt: int
    system_id: str        # 例如 'STRUCTURED_ANSWER_PROMPT@sha8'
    prompt_sha256: str
    response_sha256: str | None
    latency_ms: int
    prompt_tokens: int | None
    completion_tokens: int | None
    error_code: str | None
```

**验收**

- 关闭开关时，现有 1031 项测试全部通过，回答字节不变（用 mock caller 比对）。
- 开启 trace 时，G3 类重跑（需授权的收费调用）能输出：每轮 `coverage_review.issues`、completeness 错误码、各阶段段落哈希。
- 新增 `tests/test_trace_replay.py`：录制一次 mock 交互后重放，回答哈希一致。

**风险 / 兼容 / 回滚**：cassette 含资料原文，按 `DATA_ASSETS.md` 规定只存本机，加入 `.gitignore`；`answer_meta` 新字段只增不改；回滚即关闭 `MARINE_TRACE`。

**需要用户授权的唯一收费步骤**：在 P0 完成后，用录制模式重跑 12 题专项 + G3/A2/C3/M1 各 3 次（约 15×3 次问答），用来确认 H1–H7，并作为 P3/P4 的回放基线。

---

### P1 统一子问契约（Facet Contract）

**目标**：检索、CRAG、生成、审查、恢复、标签都共用一套带类型的子问；纠错时其他子问不退化。

**新文件** `marine_kb/facet_contract.py`

```python
FACET_KINDS = ('definition','distinction','basis_document','procedure','eligibility',
               'method_steps','channel','risk_relation','numeric_value','status_at_time',
               'historical_fact','calculation','explanation')
GAP_TYPES = ('evidence_not_found','jurisdiction_rule_not_found','live_status_not_available',
             'individual_determination_required','business_data_missing','calculation_input_missing')

@dataclass(frozen=True)
class Facet:
    fid: str                 # 'f1'...；另存 key=sha1(normalized text)[:8]，跨轮次和补检保持稳定
    text: str
    kind: str
    objects: tuple[str, ...] # 过渡期生活救助、冬春救助、巨灾保险赔付…
    places: tuple[str, ...]
    place_role: str          # 'none'|'context'|'jurisdiction_required'
    time: dict | None        # {'start','end','as_of'}，沿用 forecast_runtime.service.period/as_of_cutoff
    required_outputs: tuple[str, ...]   # 'verdict','per_object_basis','steps','value','channel_name','relation'
    allowed_gaps: frozenset[str]
    parent: str | None

def build_contract(question, planner_parts, history_state=None) -> list[Facet]
```

**规则（确定性优先，模型只给候选）**

- `kind`：规则词表，比如“是不是一回事|区别|能否替代 → distinction”“依据什么|按什么文件 → basis_document”“检查哪些|步骤|怎么加固 → method_steps”“会不会传染|是否说明 → risk_relation”“多少|多大|最高 → numeric_value”“当前|今天|截至某日的状态 → status_at_time”。规则不命中时用 normalizer 的建议，并限制在 `FACET_KINDS` 内。
- `place_role = jurisdiction_required` 的条件：facet 含“标准|金额|补助|申请窗口|当地规定|本地预案|按.*预案|签发|由谁|现行|开放|停运|回港|名单”，或 kind 属于 `eligibility / status_at_time / numeric_value`；`channel` 题中的地名也属于此类（渠道本身是地方性的）。否则地名为 `context`。
- `allowed_gaps` 按 kind 与 place_role 查表，例如：
  - `method_steps + context` → `{evidence_not_found}`
  - `basis_document` → `{evidence_not_found, jurisdiction_rule_not_found 仅当 jurisdiction_required}`
  - `status_at_time` → `{live_status_not_available, evidence_not_found}`
  - `eligibility` → `{individual_determination_required, jurisdiction_rule_not_found}`
  - `risk_relation` → `{evidence_not_found}`（不允许 `live_status_not_available`，除非问“当地是否已暴发”）

**接入点**

| 文件 | 改动 |
|---|---|
| `qa.py:_normalize` / `prepare_question` | 产出 `plan['facets']`，`subquestions` 保留（向后兼容：`[{id,question}]` 由 facets 派生） |
| `crag.py:retrieve` | `parts` 带 `fid/kind`；`_plan` 键用 `fid` |
| `qa.py:_organized_answer` | payload 增加 `facet_contract`；`question_contract` 保留 |
| `coverage_review` / `answer_completeness` | 输入加 `facet_contract`，输出用 `fid` |
| `chat_api` 会话 | `Conversation` 保存 `TurnState{storm_sid, places, time, objects}`；显式新实体覆盖，否则继承（见 P1.1） |

**P1.1 追问继承与缓存隔离**

```python
@dataclass
class TurnState:
    storm_sid: str | None; places: tuple; time: dict | None; objects: tuple; as_of: str

def resolve_turn(question, prev: TurnState | None) -> TurnState:
    explicit = extract_state(question)            # 复用 leaf_places / period / STORM_ALIASES / named_objects
    if explicit.corrects(prev):                   # “不是宁波，是舟山”“改成10月20日”
        return prev.replace(**explicit.nonempty())
    return prev.merge(explicit) if followup_question(question) else explicit
```

- 回答 meta 写 `turn_state` 和 `inherited_fields`；渲染层可显示一行“本轮按：舟山、10月18–21日”。
- 网页与预报缓存键加入 `turn_state.fingerprint()`（地点、时间窗、as_of、批次），防止跨地点或跨日期串用。

**验收样例**

- G3 → f1 distinction(objects=3)，f2/f3/f4 basis_document，place_role=none。
- A2 → f1–f3 method_steps，place=温州，role=context。
- C3 → f1 distinction，f2/f3 channel，place=浙江，role=jurisdiction_required，`live_status_not_available` 不在允许集合中。
- G1“假设宁波鄞州受灾能申请什么救助” → eligibility，role=jurisdiction_required，允许 `jurisdiction_rule_not_found` 和 `individual_determination_required`。
- 追问“那舟山呢”继承对象、替换地点；“不是蔷琵，是 202629”替换台风且清除旧时间窗。

**风险**：规则误判 kind。缓解：未命中规则时用 `explanation` 并沿用旧行为；`tests/test_facet_contract.py` 覆盖 160 题全部原问 + 第 10 节的改写题，人工核对一次快照。
**回滚**：`MARINE_FACET_CONTRACT=false` 时 facets 只用于记录，不参与判定。

---

### P2 证据对象、文档卡与一致的证据视图

**目标**：正文段落与标题、文号、机关、施行日等元数据分开封装，并且可以核验；所有阶段看同一份证据视图。

**新文件** `marine_kb/evidence_units.py`

```python
@dataclass(frozen=True)
class DocCard:
    citation: int; source_id: str; title: str
    issuer: str | None; doc_no: str | None
    published_at: str | None; issued_at: str | None
    effective_from: str | None; effective_to: str | None
    jurisdiction: str | None          # 'national'|'浙江'|'宁波'…（来自 _scope）
    version_status: str               # 'current_verified'|'version_unverified'|'expired'|...
    url: str | None; retrieved_at: str | None; body_sha256: str
    derivation: dict                  # 每个字段的来源：'title'|'body_line:<offset>'|'url'|'meta'

@dataclass(frozen=True)
class Passage:
    citation: int; passage_id: int    # 0 保留给 DocCard 渲染
    text: str; start: int; end: int   # 原文偏移
    section_path: tuple[str, ...]     # 例如 ('五、灾后救助','5.2 过渡期生活救助')
    sha256: str

def build_doc_card(hit) -> DocCard               # 只从 title/正文首尾 1500 字/URL 正则抽取，不调用模型
def build_passages(hit, selected_ids=None) -> list[Passage]
def evidence_view(hits, crag_plan) -> list[dict]  # 唯一入口：生成、CRAG、校验、审查都调用
```

**抽取规则（确定性）**

- `issuer`：标题中“X关于印发”“X办公厅”前缀；正文末尾落款。
- `doc_no`：`[一-鿿]{1,6}〔20\d{2}〕\d+号`。
- `effective_from`：正文中“自YYYY年M月D日起施行|实施”。
- `title` 中书名号内的名称单独列为 `document_names`。

**改动**

| 文件 | 改动 |
|---|---|
| `web_supplement.focused_public_text` | 返回 `[(start,end,section_path)]` 结构；按原文顺序输出；施行/适用句**不再前置拼入正文**，改写入 DocCard；保留原函数供旧路径使用 |
| `web_supplement.fetch` L706–721 | hit 增加 `doc_card`、`passages`；`text` 不变（兼容） |
| `qa.py:_organized_answer` L1087–1099 | 改为 `evidence_view(...)`；生成器 evidence 中每个资料加 `passage 0 = DocCard.render()`（如“文件：国务院办公厅关于印发《国家自然灾害救助应急预案》的通知；机关：国务院办公厅；施行：未在正文节选中出现；现行状态：未核实”） |
| `qa.py` L1214 覆盖审查 | `cited_passages` 改为 `evidence_view` 的同一批段落 |
| `crag.py:_evaluate` L105–109 | 用 `evidence_view` 的段落 |
| 截断 | 3000 字上限改为按 facet 分配：每个 facet 至少 1 个完整章节（≤900 字），每源总计 ≤4800 字；policy 文件允许 6000 字。用开关比较 token 成本 |

**校验规则**

- `document_identity` 只能引用 passage 0，比对 `DocCard` 字段（书名号名称要求精确包含）。
- passage 0 **不得**支持任何数量、程序、主体职责类陈述（确定性检查：含 `QUANTITY_PATTERN` 或动词“下达/审核/发放/申请”的 claim 若引用 passage 0 则拒绝）。

**验收样例**

- G3 资料3：DocCard.title 含“国家自然灾害救助应急预案”，claim“过渡期依据《国家自然灾害救助应急预案》办理”可拆为 identity（passage 0）+ content（正文段），两部分都通过。
- 负例：“该预案规定救助标准为每人每天 20 元[资料3:0]” → 拒绝（数字不能由 DocCard 支持）。
- 负例：标题为宁波方案，回答写“全国巨灾保险依据该方案” → `jurisdiction=宁波` 与陈述范围冲突，由 P4 拒绝。

**风险**：抽取错误把附件名当成文件名。缓解：`derivation` 记录来源；`tests/test_doc_card.py` 用 `data/public_references/` 的宁波 PDF 及导出片段做 fixture。
**迁移**：旧 cassette 没有 passage 0；重放时以 `evidence_view(version=1)` 生成旧视图。
**回滚**：`MARINE_EVIDENCE_UNITS=false`。

---

### P3 分型逐句校验与局部恢复

**目标**：修复 C1–C5，统一主路径与恢复路径的标准；恢复以“最小受支持语义单元”为目标，保留已通过的兄弟子问，恢复后重新判定覆盖。

#### P3.1 陈述切分（确定性）

`claim_grounding.draft_claims` 改为 `split_claims(raw, contract)`：

- 标题行或编号标签（如“1. 是否一回事：不是。”）拆成 `label`（“1. 是否一回事”）+ 结论（“不是”）。结论部分标为 `synthesis` 候选，并绑定该行所属 facet。
- 复合句在“；”和“[资料N]；”处、以及“而/则/但”引出的并列分句处拆成 atoms。**每个 atom 必须是原句的连续子串**（去掉引用标记后），不改写任何文字。
- 每个 claim 带 `facet_ids`：按所在段落标题或编号与 facet 对齐；对不上的由判定模型给出，再校验其 `fid` 是否存在。

#### P3.2 判定模型输出扩展（沿用一次批量调用）

```json
{"claims":[{"id":3,"atoms":[
   {"aid":"3a","text":"过渡期生活救助资金由财政部会同应急管理部按相关政策规定下达","status":"supported","type":"direct_fact","refs":[{"citation":3,"passage_id":2}]},
   {"aid":"3b","text":"冬春救助是受灾群众安全温暖过冬的制度性安排","status":"supported","type":"direct_fact","refs":[{"citation":1,"passage_id":1}]}],
  "facet_ids":["f1","f2","f3"]},
 {"id":2,"atoms":[{"aid":"2a","text":"不是","status":"synthesis","premises":["3a","3b","3c","9a"]}]}]}
```

**确定性复核**（`apply_attribution_v2`）：

1. `atom.text` 是 claim 文本的子串，否则 `rejected_atom_not_substring`。
2. supported → 沿用 `grounding_quote_not_in_passage` 和 `decode(candidate)`（数字、单位、表格）。
3. `document_identity` → 只与 DocCard 比对。
4. `synthesis` → 每个 premise 都必须是已通过的 atom；atom 中的命名实体和数字必须出现在 premises 或问题里（`named_objects`、`leaf_places`、`QUANTITY_PATTERN`）；并且所属 facet 的 kind 必须在 `{distinction, risk_relation, basis_document, explanation}` 中。
5. limitation / scope_note → 用 P1 的 `gap_type` 校验，取代 L192 的正则；允许引用其所限定的资料。
6. 同一 passage 在同一调用中被判“有”和“无”的冲突（G3 第 3 与第 5 句）→ 标记 `judge_inconsistent`，对冲突 atom 单独复判一次（≤1 次额外调用），仍冲突则按 unsupported 处理，但保留在 trace。

#### P3.3 交付判定（确定性）

```python
def facet_delivery(contract, accepted):
    rows = {}
    for f in contract:
        own = [a for a in accepted if f.fid in a.facet_ids]
        substantive = [a for a in own if a.type in {'direct_fact','synthesis','calculation','document_identity'}]
        gaps = [a for a in own if a.type == 'gap' and a.gap_type in f.allowed_gaps]
        covers = covered_outputs(f, substantive)   # 规则：distinction 需要 verdict(synthesis)；basis_document 需要 identity+content；steps 需要 ≥2 步
        rows[f.fid] = ('full' if covers == set(f.required_outputs)
                       else 'partial' if substantive or gaps
                       else 'missing')
    return rows

def publishable(contract, rows):
    core = [f for f in contract if f.kind in {'distinction','numeric_value','status_at_time'}] or contract[:1]
    return (all(rows[f.fid] != 'missing' for f in core)
            and sum(r != 'missing' for r in rows.values()) * 2 >= len(rows))
```

#### P3.4 局部恢复（有预算上限）

```python
def recover(raw, evidence, contract, budget):
    accepted, decisions = ground(raw, evidence, contract)               # 1 次调用（+≤1 次复判）
    rows = facet_delivery(contract, accepted)
    if publishable(contract, rows):
        return render(accepted, rows)
    failed = [f for f in contract if rows[f.fid] == 'missing']
    absent = [f for f in failed if reason_class(decisions, f) == 'evidence_absent']
    if absent and budget.take('facet_search'):                          # ≤1 轮、≤2 条查询/facet
        evidence = evidence + targeted_retrieve(absent, contract)       # 查询词来自 facet.objects + kind 词表，不含私有资料
    if budget.take('facet_regen'):                                      # ≤1 次调用
        raw2 = regenerate(failed, frozen=accepted, evidence)            # payload 带 frozen_claims（逐字）、rejected_claims+理由
        more, _ = ground(raw2, evidence, contract, only=failed)         # ≤1 次调用
        accepted = merge(accepted, more)                                # 不得删除或改写 frozen
        rows = facet_delivery(contract, accepted)
    return render_with_gaps(accepted, rows, acquired=doc_cards(evidence), contract=contract)
```

- **预算**：每题模型调用 ≤10 次，墙钟 ≤60 秒（含检索）；超出就直接走 `render_with_gaps`。
- **`render_with_gaps`**：每个 missing facet 输出“已取得什么（DocCard 的文件名、机关、URL，确定性）、还缺什么（gap_type 对应的模板 + 具体对象）、补齐路径（第 9 节）”。不再输出孤立残片。
- **来源列表**：用户可见来源 = 被保留陈述引用的来源 + `render_with_gaps` 提到的 DocCard 来源；未采用的候选只进 trace。

#### P3.5 主路径统一

- 对 `families(question)` 非空的问题，以及 `basis_document / procedure / eligibility` 类 facet：**首次生成通过后就运行 P3 判定**（影子模式先记录不拦截）。
- 其他题保持原逻辑，只在失败时进入。
- 两周影子数据显示误删率下降、且误保留率（独立评测）不上升后，再开启拦截。

**修改文件**：`marine_kb/claim_grounding.py`（新增 v2 函数，保留 v1）、`marine_kb/qa.py` L1334–1406（改为调用 `recover`）、`marine_kb/answer_completeness.py`（completeness 改为可选的模型复核，确定性的 `facet_delivery` 作为主判定）、新文件 `marine_kb/recovery.py`。

**单元测试（重放 G3 结构，不调用模型）**

- 用 G3 的 11 条陈述 + 构造的判定 JSON：第 3 句拆出 3 个 atom，保留 2 个；第 2 句以 synthesis 保留（premises 为 3a、3b、9a）；第 4 句 identity 通过；第 6 句以 scope_note 保留；第 11 句因 `jurisdiction_rule_not_found` 不在 f4 允许集合中而转为 scope_note（或删除）。
- 负例：synthesis 引入新实体（“三者都由民政部门办理”）→ 拒绝；premise 未通过 → 拒绝。
- 负例：atom 不是原句子串（模型改写）→ 拒绝。
- 负例：H4 情形（冬春救助“资金管理系统”实际出自资料2却引资料1）→ 仍拒绝，不复活。
- 最少交付：只有“该方案自2024年4月30日起施行”时，`publishable=False`，输出 `render_with_gaps`，不输出残片。

**风险**：synthesis 被滥用成洗稿。缓解：确定性实体/数字约束 + facet 类型限制 + 独立评测中单列“synthesis 误判率”。
**成本**：典型多 1 次判定调用（主路径影子）；恢复路径从“3 次生成 + 3 次审查 + 1 次判定 + 1 次补全”改为“≤2 次生成 + ≤2 次审查 + ≤2 次判定 + ≤1 次局部生成”，用 P0 的 usage 实测比较。
**回滚**：`MARINE_TYPED_GROUNDING=off|shadow|on`。

---

### P4 范围闸门（A2/C3/M3/G1/G2 类）

**目标**：只有契约允许的缺项才算缺项；审查发现的 `unasked_gap` 以确定性方式处理，而不是靠重写；地方精确规定、个体赔付、当日状态等真正必要的缺项照样保留。

**新文件** `marine_kb/scope_gate.py`

```python
def classify_gap(text, facet, evidence) -> str:     # 规则 + 关键词：返回 GAP_TYPES 之一或 'unclassified'
def gate(missing, contract, review_issues):
    kept, notes = [], []
    for item in missing:
        f = facet_of(item, contract)                  # 由生成器 subquestion_coverage 的 id / fid 映射
        g = classify_gap(item, f, ...)
        flagged = any(i['kind']=='unasked_gap' and i['text'] in item for i in review_issues)
        if g in f.allowed_gaps and not (flagged and g not in HARD_GAPS[f.kind]):
            kept.append((f.fid, g, item))
        else:
            notes.append(scope_note_for(f, g))        # 固定模板，一句话，不计入缺项
    return kept, dedupe(notes)
```

- `HARD_GAPS`：例如 `eligibility → individual_determination_required`、`status_at_time → live_status_not_available`、`numeric_value → business_data_missing`。审查即使判为 `unasked_gap` 也**不能移除**这些缺项（防止模型审查删掉真实缺项）。

**适用性理由过滤**

- `_safe_metadata` 输出给生成器的 `applicability.reasons` 按 facet 过滤：`place_role=context` 且 `kind ∈ {method_steps, definition, distinction, risk_relation}` 时去掉“来源未明确覆盖所问地点”，改为 DocCard 的 `jurisdiction='national'` 字段，由渲染层统一生成一句范围提示。

**`document_kind` 修正**（`evidence_applicability.py` L179）

- 标题含“预报”但同时含“防御|知识|科普|是什么|怎么|与”或 `primary_scope ∈ {national_guidance, service_directory}` → `knowledge`。
- 测试：C3 资料2 → knowledge；“浙江省海浪预报（10月18日）” → 仍为 forecast。

**policy 版本未核的处理**（`evidence_applicability.py` L345–346 与 `crag.py` L129）

- 对 `basis_document / procedure` facet：`version_unverified` 的 policy 保持 `use=direct`，同时加 `scope_note=version_unverified`。
- 对 `status_at_time` 或“现行是否有效”类 facet：仍为 reference。
- 这样 G3 facet② 会是 supported，不再触发无效补检。

**标签**

- `answerability` 只由 `facet_delivery` 和 `kept` 缺项决定，`scope_note` 不降级。
- 这样会让部分题从 partial 变为 full，这是范围修正的预期结果，**但必须经过第 10 节的独立验收确认**；凡是 full 的题，独立评测器若判出“缺了真正必要的项”，就计入 `false_full`，作为发布阻断指标。

**`coverage_review` 的用途收窄**：只保留 `unsupported_claim / contradiction` 两类用于 P3 复判；`unasked_gap` 作为 `gate` 的输入。不再因为 `unasked_gap` 触发整篇重写（每题少 1–2 次生成和审查调用）。

**验收样例**

- A2：缺项为空；末尾一句“以上为国家海水鱼产业技术体系手册的通用做法，温州当地如另有要求以当地为准”；facet 全部 full。
- C3：不出现“当前实时发布状态”缺项；查询入口保留。
- G1（eligibility + 宁波）：保留“鄞州区补助标准、申请窗口”缺项，因为它在 `allowed_gaps` 中且属于 HARD_GAPS 关联类型。
- 负例：问“宁波巨灾保险今年赔了多少”（numeric_value） → 缺数据时保留 `business_data_missing`，不得被审查移除。

**回滚**：`MARINE_SCOPE_GATE=false`。

---

### P5 关系与主语范围守卫（M1 类，含医学/法律）

**目标**：识别陈述的主体、关系、条件和例外，避免把同段共现的实体拼成错误对应。

**新文件** `marine_kb/relation_guard.py`，数据文件 `data/domain/relation_lexicon.json`

```python
# relation_lexicon.json（每条附权威来源 URL、取得时间、核对人；未核对的条目不启用）
{"vectors": {"蚊": ["蚊","蚊虫","蚊子","伊蚊","库蚊","按蚊"], "鼠": ["鼠","鼠类","老鼠"], "蝇": ["蝇","苍蝇"]},
 "diseases": {"登革热": {"vector": "蚊", "src": "..."}, "基孔肯雅热": {"vector": "蚊", "src": "..."},
              "流行性乙型脑炎": {"vector": "蚊", "aliases": ["乙脑"], "src": "..."},
              "肾综合征出血热": {"vector": "鼠", "aliases": ["出血热"], "src": "..."},
              "钩端螺旋体病": {"vector": "鼠/污染水接触", "aliases": ["钩体病"], "src": "..."}}}

JOINT = r'(?P<subj>(?:{V})(?:[、和与及/]+(?:{V}))+)[^。；]{0,24}(?:传播|带来|引起|导致|传染)(?P<obj>[^。；]+)'

def joint_relations(passage_text) -> list[Relation]           # subjects>1 的关系
def check(answer_claims, facets, evidence) -> list[Issue]:
    # 1) facet.objects 只含一种媒介（如“蚊”），claim 列出的宾语来自某个联合关系，
    #    claim 及其所在段落未保留其他主语 → 'joint_subject_collapsed'
    # 2) claim 写“蚊…传播 X”，而词表中 X 的媒介≠蚊，且没有任何引用原段直接写“蚊…X” → 'relation_mismatch'
```

- 处理方式：命中 `joint_subject_collapsed` 时，按确定性模板改写（**只增加主语限定，不改原文事实**），例如“原文指出，洪水后**鼠类和蚊虫**活动增加可能传播……；其中与蚊虫相关的是登革热、基孔肯雅热（需有原文支持），出血热、钩体病主要与鼠类和污水接触相关（需有原文支持）”。没有原文支持的部分不写，只保留联合表述并加一句“上列疾病并非都由蚊虫传播”。
- 词表**只用于检测，不作为回答证据**。回答中的每个对应关系仍须有原段支持；如果没有，就保留原文的联合表述。
- 推广到法律和程序类：沿用 `facet_support` 的“主体—动作”要求（L412–421），增加“提出建议 ≠ 批准”“降级 ≠ 终止”的关系检查，已有提示词规则改成同样的检测器。
- `GENERATION_GUIDANCE` L114 的“不提供配药”改为确定性检测：`risk_relation / method_steps` 中出现药剂名称、浓度或剂量时提示删除，除非问题明确问到用药。

**专业来源要求**：`risk_relation` facet 若涉及医学（命中疾病词表），correct 来源必须属于卫生健康部门或疾控机构（`nhc.gov.cn`、`chinacdc.cn`、`*.wsjkw.*.gov.cn`、`gov.cn` 转载的卫健委文章），否则 CRAG 只能给 ambiguous。

**验收**：M1 原答 → 检出 `joint_subject_collapsed`；修复后答案中出血热、钩体病不再挂在蚊媒风险下。负例：原文本来就写“蚊虫可传播登革热” → 不告警。
**回滚**：`MARINE_RELATION_GUARD=off|shadow|on`。

---

### P6 连接器与正文提取

| 问题 | 方案 | 文件 |
|---|---|---|
| nhc.gov.cn 14 次失败 | ① P0 记录 HTTP 码；② 不绕过访问限制；③ 增加**官方等效来源**：同文在 gov.cn 的转载、chinacdc.cn、浙江省及市卫健委；④ 对少量高价值页面采用**人工采集参考件**（沿用 `data/public_references/manifest.json` 模式：URL、取得时间、sha256、采集人、版本），由 `PublicReferenceReader` 优先读取；⑤ 采集件标 `retrieved_at`，超过 180 天在 DocCard 中提示复核 | `public_cache.py`、`public_sources.py`、`data/public_references/` |
| 无关 JS 路径页被读取 | 候选按 family 过滤：非“路径/预警/当前台风”类问题不读取 `typhoon.*` 页面；记录 `skipped_irrelevant` | `web_supplement.fetch` L579–592 |
| 正文顺序与标题链 | 见 P2 | `focused_public_text` |
| 附件发现只限预案/标准 | `document_links` 的标题匹配扩到“方案、细则、指南、手册、规范、通知附件”；附件 DocCard 继承父通知的机关、文号、日期 | `fetch` L606–627 |
| 缓存可重复性 | 网页读取结果按 `(url, sha256, retrieved_at)` 存档；评测用 ReaderCassette 固定网页快照，另做实时运行测时效 | `public_cache.py`、`tools/replay/` |

验收：12 题专项中 `web_body_read_failures` 不再出现无关 JS 页；M 族至少 1 个卫健/疾控来源被读取成功（实时或采集件），并在 DocCard 中注明来源方式。

---

### P7（可选）数据型路由的工具调度

只在 P0–P4 完成、并且评测显示数据题的主要失败在“工具选择与参数”时实施，见第 8 节。

---

## 8. 在现有 DeepSeek + CRAG 体系内修复，还是引入工具调度 / skill

| 方案 | 收益 | 成本 | 边界 |
|---|---|---|---|
| **A. 体系内修复（P0–P6，推荐先做）** | 直接对应已确证的根因（C1–C8）；所有新检查都是确定性的或带审计的；不改变模型和接口 | 典型每题 +0~1 次调用（影子判定），恢复路径调用数下降；工程量集中在 `qa.py` 恢复段和 4 个新模块 | 不能解决缺业务数据；判定仍依赖 DeepSeek，需要独立评测兜底 |
| **B. 工具调度（函数调用）** | 数据题可以多步组合（预报格网 → 天文潮 → 岸段阈值 → 预警归档），参数可校验，失败原因可结构化返回 | 规划调用次数不确定、延迟上升；网页内容可能注入工具参数；需要权限层与审计；回归测试更难 | 只适合数据和计算类路由（`forecast_runtime` 已是确定性路由，增量收益要用数据证明）；公开服务类问题的根因不在工具选择 |
| **C. 添加 SKILL.md** | 对开发者（Codex）的工作流说明有用 | 几乎为零 | **对运行时没有任何作用**：DeepSeek 不会读取仓库里的 Markdown，`_call_model` 只发送代码中的 system 提示 |

**如果做 B，必须满足以下接入条件，否则不算集成**：

1. **注册**：新增 `marine_kb/tools/registry.py`，每个工具是服务器端 Python 函数，带 JSON Schema。例如 `query_coastal_forecast(region, start, end)`、`astronomical_tide(station, start, end)`、`list_official_warnings(region, as_of)`、`read_official_page(url)`、`search_official(query, site)`。
2. **参数约束**：地点必须在已注册岸段/站点表中；时间窗不超出 NC 覆盖范围且不晚于 as_of；URL 必须通过 `trusted_url`。不合规直接返回结构化错误，不执行。
3. **权限**：工具执行层沿用 `_can_send` 与外发授权；模型拿不到私有资料原文作为搜索参数（保持 `crag.py` L173–174 的原则）。
4. **执行链**：`ToolLoop`（≤4 轮，≤8 次调用，≤45 秒）→ 工具结果成为 `business_forecast` 或 `program_calculation` 证据 → 仍走 P3 判定和 `validate_business_delivery`。
5. **日志**：每次工具调用的参数、结果哈希、耗时、错误码写入 `AnswerTrace`。
6. **失败恢复**：工具报错时返回 `calculation_input_missing` 或 `business_data_missing` 类缺项，不回退到模型记忆。
7. **核实**：DeepSeek API 文档列有 Function Calling，但需按当前账号与模型版本（`deepseek-chat`、thinking disabled）实测 JSON 参数稳定性后再决定。

**“领域技能”的正确落点**：本项目已有运行时生效的领域包，即 `public_service_intents.py` 的查询镜头、`focus_groups`、`supports_subject` 和 `GENERATION_GUIDANCE`。建议把它们规范化为 `marine_kb/domains/<family>.py`，统一提供 `queries / focus_groups / facet_rules / relation_rules / allowed_sources / guidance`，由注册表加载，并且每个领域包带测试。这才是能生效的“skill”；新增文件而不接入 `fetch`、`crag`、`_organized_answer`、`scope_gate`，就不算完成。

**参考项目的使用边界**

- CRAG（`docs/CRAG_REFERENCE.json`，commit `de7c296`）：继续只借鉴“评估—纠错”控制流，不引入作者代码。
- Adaptive-RAG：可借鉴按复杂度分配检索流程，例如对 `definition` 类单 facet 题跳过补检和覆盖审查。需要在第 10 节评测中量化延迟收益，不宣称复现论文结果。
- FloodOps、RapidResponseAgent、RescueMind、AWS 示例：只作工具接口形态参考；许可证和代码可用性逐一核对后才能引入任何代码；不得用其 mock 数据冒充业务数据。

---

## 9. 需要获取的数据、来源、授权与核对方法

### 9.1 可通过公开资料或工程补齐

| 业务类型 | 需要的数据 | 来源 | 获取方式 | 核对方法 |
|---|---|---|---|---|
| 救助制度 | 《国家自然灾害救助应急预案》现行版本正文、冬春救助通知、倒损住房规范、浙江省和宁波市自然灾害救助实施办法 | gov.cn、mem.gov.cn、zj.gov.cn、ningbo.gov.cn | 官方页面 + 附件；DocCard 抽取文号和施行日 | 查“有效性”标签、废止通知、后续修订；`version_status` 只有在有明确现行标识时才写 `current_verified` |
| 巨灾保险 | 宁波、温州等地公共巨灾保险实施方案及年度续保通知 | 地方政府、应急管理局网站 | 同上；已有宁波 PDF 参考件 | 方案有效期（如 2024—2026）对照查询日；过期须标注 |
| 防蚊与传染病 | 卫健委、疾控的灾后防病指南 | nhc.gov.cn（当前读取失败）、chinacdc.cn、浙江省卫健委 | 实时读取或人工采集件 | 发布机关、日期、疾病—媒介对应逐条核对 |
| 养殖设施 | 农业农村部、浙江省海洋与渔业部门技术手册 | moa.gov.cn、nynct.zj.gov.cn | 附件 PDF | 手册版本、发布机关 |
| 预警渠道 | 气象、海洋预警发布平台目录 | cma.gov.cn、zj.weather.com.cn（目前返回验证页）、zjocean.org.cn | 服务目录页 + 人工采集 | 只证明入口存在，不证明当前有预警 |
| 指定日期地方公告 | 渔船回港、停运复航、响应启动、灾情通报 | 县市政府、海事局、交通运输局、防指栏目 | **定时归档爬取**指定栏目（尊重 robots 与访问规则），保存原文、哈希、取得时间、发布、更新、解除链 | 按“截至时刻”取当时有效版本；发布时间晚于 as_of 的排除（已有 `after_cutoff`） |
| 历史灾情 | 海洋灾害公报（国家、浙江） | mnr.gov.cn、zj.gov.cn | 原表恢复、结构化入库 | 区分增水、最高潮位、经济损失；事件—站点记录 ≠ 独立站点 |

### 9.2 需要授权或业务核定，不能靠公开资料补齐

| 缺项 | 为什么不能公开补齐 | 补齐路径 | 系统应有的输出 |
|---|---|---|---|
| 浙江省级预警完整清单（历史，含更新和解除） | 省级接口需要访问凭据；国家源不保证完整省级清单 | 业务单位提供接口凭据或按日归档文件 | “已取得国家海洋预报台记录 N 条；浙江省级清单未接入，不能判断为零预警” |
| SWL/SSW 语义、高程基准、MWD 角度定义、谱峰周期变量 | 产品口径需要数据提供方书面说明 | 产品说明书或数据单位确认函 | 数值照报，标注假设编号，比如 Q-022 中“SWL 按纯增水、天文潮按 1985 基准”这些假设 |
| 个人救助资格、补助金额、保单是否赔付 | 取决于个人核定和合同条款 | 当地应急局、村居、保单 | `individual_determination_required`，给出办理入口 |
| 淹没范围、转移人数与名单 | 需要裸地高程、海塘闸泵状态、校准模型和人员台账 | 水利、应急部门数据；模型校准 | 只报地形人口栅格统计并注明“不是淹没模型或名册” |
| 海堤达标率、隐患点清单 | 工程台账多为内部资料 | 水利部门 | `business_data_missing` + 申请路径 |
| 预警实际平均提前量 | 需要预警与实况配对样本 | 业务检验数据 | 只解释指标定义和计算方法 |
| 浴场开放、水质、救生、船舶资质的当日状态 | 现场管理记录 | 景区、海事、文旅部门公告 | `live_status_not_available` |

### 9.3 版本、时间、地区、单位核对方法（写入 P2 DocCard 与 `forecast_runtime`）

- **版本**：DocCard `version_status` 的取值规则——正文或页面有“有效性：有效/现行有效”且有发布日期 → `current_verified`；有“废止/失效”或后续替代通知 → `expired`；否则 `version_unverified`。有效期区间（如 2024—2026）与 as_of 比较。
- **时间**：三个时间分开记录——`published_at`（发布）、`valid_from/valid_to`（有效）、`retrieved_at`（读取）。查询“截至某日”同时约束发布时间 ≤ cutoff，且有效区间覆盖目标时段。NC 记录起报批次、有效时刻、时区（UTC 或 UTC+8 显式标注）、产品过期判断。
- **地区**：DocCard `jurisdiction` 与 facet `places` 比较；岸段代表点 ≠ 验潮站；跨站峰值不能作为同站差值（现有 `business_validation` 已有规则，保留）。
- **单位**：只认原文或产品声明的单位（`declared_column_unit`）；风速 kt→m/s 只在原单位明确时换算；潮位必须带基准；缺测不按零处理。
- **数值复核**：数据题的独立核对脚本直接读 NC/数据库，不经过问答系统，重算 3 个抽样点，与回答比对（见第 10 节）。

---

## 10. 评测设计

### 10.1 分层

| 层 | 内容 | 是否收费 | 频率 |
|---|---|---|---|
| 单元 | `facet_contract`、`evidence_units`、`apply_attribution_v2`、`facet_delivery`、`scope_gate`、`relation_guard`、`classify_gap`、DocCard 抽取 | 否 | 每次提交 |
| 回放接口 | Flask 测试客户端 + `ReplayCaller` + `ReaderCassette`，重放 P0 录制的真实模型输出与网页快照 | 否 | 每次提交 |
| 真实模型 | 固定题集实时调用 DeepSeek，保存原始响应 | 是，须用户授权 | 每阶段开启前、发布前 |
| 同族未见改写 | 封存题集，开发期间不可见，只在阶段验收时运行 | 是 | 每阶段验收 |

### 10.2 题集

- **金标准集**：从 160 题每类抽取 4–6 题，加 12 题专项，约 60 题。每题由业务人员写出：必答点（对应 facet）、允许缺项（gap_type）、禁止陈述（如“出血热由蚊传播”）、数据题的真值（由独立脚本从原始数据计算）。
- **同族未见改写**（示例，Codex 不得据此调提示词）：
  - 救助：“领了冬春救助还能拿保险公司的赔款吗？各找谁办？”“过渡期生活救助和应急生活救助差在哪里？”
  - 养殖：“舟山深水抗风浪网箱台风前锚泊系统怎么检查？”“温岭对虾池塘台风前进排水闸要检查什么？”
  - 蚊媒：“水退后家里蚊子多，会得乙脑吗？”“灾后老鼠多了会传什么病，怎么防？”（检验反向的主语塌缩）
  - 渠道：“手机上看到的台风路径图能当预警用吗？在哪看正式预警？”
  - 比较：“风暴潮蓝色警报和台风蓝色预警是一回事吗？分别谁发布？”
  - 数据：同一预报批次，换一个日期段或地区，要求增水峰值、时刻和基准。
- **负例集（自动构造）**：源错配（引用别的资料段号）、日期越界（发布晚于截至时刻）、单位错误（cm 写成 m）、未问缺项（方法题加“当地规程未核”）、恢复丢答案（模拟 3 句里 2 句被判 unsupported）、标题冒充正文、synthesis 引入新实体、联合主语塌缩。

### 10.3 指标（分开报告，不合成单一分数）

| 指标 | 定义 | 判定方 |
|---|---|---|
| 覆盖率 | 已交付 facet / 应交付 facet（排除有允许缺项的） | 独立评审 + 人工抽检 |
| 正确性 | 必答点中与金标准一致的比例；禁止陈述出现率 | 人工（金标准集全部），模型评审只做初筛 |
| 事实支持 | 抽样陈述中能被所引原段蕴含的比例；按陈述分型分别统计（direct / identity / synthesis） | 确定性核对 + 不同模型或人工 |
| 数值准确 | 数值、单位、时刻、地点、基准五元组与独立脚本一致的比例 | 确定性 |
| 时效 | 发布时间晚于截至时刻的资料泄漏数；过期文件当现行的次数 | 确定性 |
| 诚实缺项 | 缺项精确率（报出的缺项中属于允许缺项的比例）、召回率（真实缺项被报出的比例）、未问缺项率、**false_full 率** | 人工 + 确定性 |
| 退化率 | 发布了 `publishable=False` 的答案的比例（目标 0）；恢复路径中 facet 丢失数 | 确定性 |
| 延迟 | 端到端与各阶段 p50/p95 | trace |
| 成本 | 每题模型调用次数、prompt/completion tokens | trace（P0 usage） |

### 10.4 独立性要求

- 生成模型不得给自己的答案评标签。系统自报的 `answerability` 只作为被测量，与独立评审对比后报告一致率。
- 独立评审用不同提供方的模型或人工；盲评；输入为问题、答案和原始引文段，不看系统标签。
- 金标准集由至少 2 名业务人员标注，报告一致性（Cohen's κ）；分歧由第三人裁决。
- 每次真实运行保存：原始响应（本机）、答案哈希、源码哈希（沿用 `runtime_source_sha256`）、网页快照哈希、trace。

### 10.5 对比方法

- 修改前后用**同一份网页快照和同一题集**重放，消除网页变化的影响。
- 每题重复 3 次，报告方差（DeepSeek 在 temperature 0.1 下仍有波动）。
- 与外部产品（如豆包）比较时，只用固定输入、同一时间、同一独立评分，不推测对方实现。

---

## 11. 交付标准（逐项可验收）

**P0**
- [ ] 关闭所有新开关时，`pytest tests -q` 结果不少于 1031 passed，且 mock 回放的回答字节不变。
- [ ] 每题 `answer_meta.trace` 含每次模型调用的阶段、耗时、tokens、错误码；恢复分支含 `coverage_review_history`、completeness 错误码、逐句 `retained/drop_rule`。
- [ ] 用授权的录制运行确认或否定 H1–H7，并写进 `docs/` 下的诊断记录。

**P1–P2**
- [ ] 160 题原问及第 10 节改写题的 facet kind、place_role 快照经人工核对，错误率 ≤5%。
- [ ] 追问测试：地点替换、日期替换、台风替换、纠正指令各 ≥3 例全部通过；缓存键包含状态指纹。
- [ ] 生成、CRAG、校验、审查使用同一 `evidence_view`，跨阶段段落哈希断言全部通过。
- [ ] DocCard 对 `data/public_references/` 的宁波方案正确抽出名称、机关、施行日；附件继承父通知元数据。

**P3**
- [ ] G3 结构重放：第 3 句保留至少 2 个 atom；“不是一回事”以 synthesis 保留且前提齐全；文件名经 DocCard 核对；版本说明保留；输出不再是单句残片。
- [ ] 所有负例（atom 改写、新实体 synthesis、premise 未过、passage 0 支持数字、H4 错归因）均被拒绝。
- [ ] 回放和真实运行中，`publishable=False` 却被发布的答案数为 0。
- [ ] 真实运行中，每题模型调用 ≤10 次、p95 墙钟 ≤60 秒；超出时输出 `render_with_gaps`。
- [ ] 影子期独立评测显示：误删（有支持却被删）下降，误保留（无支持却保留）不上升，才开启拦截。

**P4**
- [ ] A2、C3、M3 在重放中不再输出未问缺项；G1、G2 中真正必要的地方缺项仍然保留。
- [ ] `false_full` 率在独立评测中不高于修改前；HARD_GAPS 不能被审查移除（单测覆盖）。
- [ ] C3 资料2 被判为 knowledge；带日期的预报页仍为 forecast。

**P5**
- [ ] M1 原答检出 `joint_subject_collapsed`；修复后答案中出血热、钩体病不再挂在蚊媒风险之下；反向题（“老鼠多会传什么病”）不把登革热归给鼠。
- [ ] 词表每一条都有权威来源 URL、取得时间、核对人；没有来源的条目不启用。

**P6**
- [ ] 非路径类问题不再读取 `typhoon.*` JS 页面。
- [ ] 蚊媒族至少 1 个卫健或疾控来源成功读取（实时或人工采集件），DocCard 标明来源方式。
- [ ] 读取失败记录含 HTTP 码与最终 URL。

**缺数据问题的输出（全阶段适用）**
- [ ] 对第 9.2 节列出的缺项，回答都包含三部分：已取得的数据或文件（带出处）、具体缺项（对象、地点、时间、指标）、补齐路径（机构或数据名）。不输出“整体无法回答”，也不用通用知识冒充具体数据。示例（R-016 类）：

> 截至 2026-10-17，本系统未取得宁波市针对该台风的转移指令或统计通报，因此不能给出转移人数和村社名单。已取得：××（来源）。缺：宁波市及各区县防指转移统计（按村社）。补齐：防指通报或业务台账接入。地形人口栅格只能估算受影响格网人口，不是转移名单。

**评测**
- [ ] 金标准集与封存改写集建立完成，标注一致性 κ ≥0.6。
- [ ] 每次阶段验收报告分别给出 10.3 节全部指标、修改前后对比和 3 次重复的方差；原始响应与独立评审记录完整留存。
- [ ] 报告中不出现“160 题全部正确”之类的表述；按业务类型写明“在什么输入条件下可以完整回答、什么条件下输出缺项”。

---

## 附录 A 关键代码位置索引

| 主题 | 位置 |
|---|---|
| 生成提示与 JSON 格式 | `marine_kb/qa.py` L42–91 |
| 证据截断与段切分 | `qa.py` L109–130、L1089–1096 |
| 主校验只核对含数字的行 | `qa.py` L387–421（L406–417） |
| 覆盖审查触发重写或报错 | `qa.py` L1208–1232 |
| 有缺项即降级 | `qa.py` L171–172 |
| 恢复入口与返回 | `qa.py` L1336–1406（返回时不带 coverage_review） |
| 逐句判定提示 | `marine_kb/claim_grounding.py` L37–51 |
| 按句切分 | `claim_grounding.py` L77 |
| 限制语过滤 | `claim_grounding.py` L189–193 |
| 残片也返回 | `claim_grounding.py` L209–213 |
| 补全审查约束 | `marine_kb/answer_completeness.py` L26–53 |
| CRAG：reference 强制 ambiguous | `marine_kb/crag.py` L129 |
| CRAG 重编号 | `crag.py` L218–221 |
| 施行句前置 | `marine_kb/web_supplement.py` L233–240 |
| 网页文本截断 | `web_supplement.py` L707 |
| 无地名资料加地点理由 | `marine_kb/evidence_applicability.py` L198–204 |
| 标题含“预报”判 forecast | `evidence_applicability.py` L179–180 |
| policy 未核现行 → reference | `evidence_applicability.py` L345–346 |
| 公共服务生成指引（仅提示词） | `marine_kb/public_service_intents.py` L104–116 |
| 整题单一契约 | `marine_kb/question_contract.py` L5–23 |
| 模型调用参数 | `qa.py` L642–650；`model_layer.py:call_model` |
