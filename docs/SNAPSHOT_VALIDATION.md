# 2026-10-21 当前公开快照验证

审查目标为本文所在提交。上次公开基线`333d45dc0a2d69100c97cae8688551fc48590e3c`；旧验证记录保留在[归档](archive/20261020_333d45d_SNAPSHOT_VALIDATION.md)。

| 验证对象 | 通过 | 跳过 | 失败 | 子测试通过 | 时间 |
|---|---:|---:|---:|---:|---:|
| 完整本机应用最近已有记录 | 2289 | 0 | 0 | 134 | 37.40秒 |
| 本次公开代码快照重新运行 | 2231 | 58 | 0 | 134 | 33.82秒 |
| 本次Node地图/连接测试文件 | 3 | 0 | 0 | — | 见统计摘要 |

Python3.13.9，使用本次已在干净虚拟环境中离线安装的依赖。运行命令`python -m pytest tests -q --junitxml=<output.xml>`。关闭后台worker、模型和网络检索；测试中的模型/网络依赖使用现有mock，没有真实API回退。

公开仓库不含私有数据。58项跳过来自54项私有cassette/真实录制依赖及4项本地台风数据库依赖；保留原断言，不为测试通过复制私有数据库或伪造录制。初次公开同步曾遗漏运行必需的`marine_kb/data/warning_concepts.json`，补回经过审查的官方概念卡后按原命令重跑，结果如表；未改变运行代码或降低断言。

Node命令：`node --test tests/test_objective_panel.cjs tests/test_objective_map_actions.cjs tests/test_knowledge_connection.cjs`。覆盖地图动作、精确原生时次、范围/要素切换、旧图清除、异步取消及管理页连接处理。

本轮上传前已有8次自然语言事务（7ready、1no_data）、8组真实产品检查和48项响应检查。6个报告事务共观察到48次模型调用，用户实际展示以程序数据为主。4×4格网、73时次样例的逐格最大值、时间/坐标、有效样本和阈值过程已独立读NC核对。以上是此前实测，本次上传新增付费问答0次，没有新版160题和专家验收。

新发现的未知地名、首帧海点掩膜语义、多要素显示边界来自静态审查，尚未作为新失败复现；请见[Claude当前任务](CLAUDE_CURRENT_HANDOFF_20261021.md)与[绘图指南](OBJECTIVE_MAP_RENDERING_GUIDE.md)。[公开统计](../evaluations/objective_map_20261021/summary.json)不含私有原始记录或实时预报数值。
