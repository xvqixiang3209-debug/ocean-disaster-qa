# P0 本机录制与离线回放

本工具保存依赖调用的完整请求与响应，供同一请求的确定性回归使用。默认不开启录制，也不加载 `.env`、调用 DeepSeek 或访问网页。这里的测试全部使用模拟输入，不构成 G3 或其他真实问题的重测记录。

## 私有文件

录制文件包含用户问题、提示词、模型输出和网页原文，只存本机 `tmp/replay/`，此目录加入 `.gitignore`。不要提交、公开分享或把原始 cassette 添加到公开 trace。程序同时要求 `mode="record"` 和 `allow_sensitive_recording=True`；路径必须位于 `private_root` 内，默认根目录为应用下的 `tmp/replay`。测试使用显式临时根目录。

录制不改变现有 `_can_send` 或资料外发授权：包装器放在现有授权检查之后，不提供跳过授权的能力。真实模型录制仍需已获授权的收费调用；本工具不自动执行。

## 接口

```python
from marine_kb.replay import ModelCassette, RecordingCaller, ReplayCaller, ReaderCassette, SearchCassette

# 由本地评测程序显式装配。live_caller/live_reader/live_search 为既有依赖。
with ModelCassette("tmp/replay/local-run.jsonl", mode="record",
                   allow_sensitive_recording=True) as journal:
    caller = RecordingCaller(live_caller, journal)
    reader = ReaderCassette(live_reader, journal)
    search = SearchCassette(live_search, journal)
    # 注入 MarineQuestionService/OfficialWebSupplement 后执行获准的测试。

with ModelCassette("tmp/replay/local-run.jsonl", ordering="per_key") as journal:
    caller = ReplayCaller(journal)
    reader = ReaderCassette(None, journal)
    search = SearchCassette(None, journal)
    # 再次注入后，执行与录制时完全相同的请求。
    # 正常退出会验证全部交互已消费；也可提前 journal.assert_consumed()。
```

模型包装器只接受 `stream=False`。返回文本和 `(text, usage)` 元组均保持原值，不裁剪空白、不重排模型 JSON。调用参数、系统提示、问题、模型名称、temperature、return_usage 等全部参与指纹；任何差异都报错，不会调用真实服务补齐。调用参数中禁止传入凭据。

`RecordingCaller` 保留原调用器的签名，便于 trace 判断是否支持 `return_usage`；`ReplayCaller` 仅在录制请求实际使用该选项时声明支持。回放时 trace 开关/请求选项也须与录制一致，不能用旧记录模拟不存在的 usage。

网页包装器记录 `enrich(rows)` 的完整输入与返回（包括正文、读取失败、时间和 hash）。建议包在 `PublicReferenceReader` 外层，以冻结本次缓存/人工参考件选择。搜索包装器记录 `SearchOutcome`，保留 provider、attempts、错误和结果。

`journal.invoke(kind, request, live_function)` 支持其他纯 JSON/元组依赖；回放时省略 `live_function`。全链离线测试还必须冻结本地检索、预报工具和时钟等输入，并拦截所有网络出口。仅替换模型或网页读取器不能保证整个服务离线，不能直接对生产 HTTP 接口声称离线回放。

## 匹配与完成状态

- `ordered`：按全局调用顺序比对；错序、未知请求或多余调用立即失败。
- `per_key`：允许不同请求因线程调度调整顺序；同一指纹的重复调用仍按录制顺序消费，不复用最后一条响应。
- 每次完成的交互立即写入并 flush/fsync。文件以独占创建方式写入，已有文件不能覆盖。
- 正常关闭才写完成 footer；异常退出、中断、缺调用、截断和 hash 不一致均不能当完整录制回放。
- 已录制的依赖异常按安全类型/机器码恢复；不保存 SDK 异常的原始消息，不动态导入异常类，不用 pickle。
- `assert_consumed()` 检查没有未使用交互。context manager 正常退出时自动执行此检查。

严格指纹使代码改动后的提示词差异直接可见。若改动了提示词，就需要新的授权录制或独立的新模拟夹具；不能放宽匹配后把旧模型输出当作新提示词的真实结果。现有 G3 最终答案和部分 trace 缺少原始生成/审查请求，不能反推成完整真实 cassette。

## 本机验证

在应用目录执行：

```powershell
& '..\.venv\Scripts\python.exe' -m pytest tests/test_cassette_replay.py -q
& '..\.venv\Scripts\python.exe' -m tools.replay inspect --cassette .\tmp\replay\local-run.jsonl
```

`inspect` 只输出文件完整性及调用数量，不打印原文、URL 或私有路径。完整性通过只证明录制没有损坏，不证明答案正确、请求已成功回放或来源为现行版本。回放评测仍需分别报告业务正确性、子问覆盖与缺项。
