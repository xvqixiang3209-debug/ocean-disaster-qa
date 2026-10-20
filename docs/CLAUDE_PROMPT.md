请只读评审这个项目当前 main 分支，先记录实际提交 SHA：

https://github.com/xvqixiang3209-debug/ocean-disaster-qa

先读 `docs/CLAUDE_FOLLOWUP_20261020.md`，再读原 `docs/NEXT_PHASE_PLAN_FOR_CODEX.md`、`docs/SNAPSHOT_VALIDATION.md` 和 `evaluations/p1_p6_followup_20261020/README.md`。

这是对你基于 a6113ee 制定的 P0–P6 方案继续实现后的复审。当前版本 phase1.8.4-grounded-recovery，完整本机测试1485项、102项子测试通过；公开快照1433项通过、52项因无私有录制跳过、102项子测试通过。本轮只有旧24次录制的离线阶段复核，没有新24/160问答。

请优先追踪 `answer_pipeline.py`、`recovery.py`、`typed_grounding.py`、`facet_delivery.py`、`scope_gate.py`，分析跨阶段审查分歧如何保住有据内容，有据归纳如何提供可验证证明，完整交付如何避免关键词误判。22个旧归纳缺证明、1个G1审查边界变化、17次正文哈希未匹配仍未解决。不要把它们笼统归为缺业务数据。

请按复审文档要求写 `NEXT_PHASE_REVIEW_FOR_CODEX.md`，给出最小修改文件、接口、正反例测试和成功判据；区分代码可确证、需原录制、需新模型输出和需数据方核定。目标是这些题代表的问题类型，不写题号特例。

本轮不要改代码，不调用DeepSeek、不访问生产服务、不获取凭据。能运行测试就记录实际命令和输出，不能运行则明确未重跑。公开仓库没有业务数据库、NC或原始cassette；不要用旧输出冒充新提示词的实际结果。
