# 客观预报海点与缺测契约（P2）

## 默认算法

`sea_mask=wave_valid` 现在表示：同数据集的波浪源，在**整个源文件原生时间轴**中至少出现过一次有限、非负、通过缺测值及单位解析的波高，则该网格为静态海点。查询时段或显示分辨率不改变这个域。

各时次、各要素是否有值另行判断：

| 位置与时次 | 统计及显示 |
| --- | --- |
| 静态海点且当前要素有值 | 进入该要素统计和绘图 |
| 静态海点且当前要素缺测 | 计入海点缺测样本，显示透明，不按零补齐 |
| 波浪源全时间轴从未有效 | 不计入海点或海点缺测，显示透明 |

波浪某时次缺测不会遮掉同一静态海点上已有的风数据。此为当前实现的业务假定，后续可根据产品说明调整。

## API 与结果

单帧、原生过程统计、报告峰值快照和逐格时段最大值共用上述静态域。其他要素采用已有最近邻映射及波浪轴覆盖裁剪，不对覆盖外网格外推。

`sea_mask.method` 为 `static_any_valid_wave_native_sample_in_source_axis`，同时提供 `source_sha256`、`axis_sha256`、请求网格内的 `never_valid_count`。`stats72.fields.<field>` 新增：

- `never_valid_count`：请求网格中不属于静态海域的格点数。
- `intermittent_cell_count`：静态海点中，在预期原生时次上有效样本不足的格点数。包括所问窗口内始终缺测、但在窗口外有效的海点。
- `data_partial`：有海点缺测或无可用样本时为真。

`stats72` 的 `missing_count` 单位为海上格点-时次样本；缺少整条原生时次另在 `temporal_coverage` 中说明。逐格最大值的 `stats.missing_count` 是聚合后仍无值的海点数，`valid_sample_counts` 和 `valid_sample_ratios` 提供逐格窗口有效样本情况。

源波浪掩膜按源 SHA、算法版本及经纬度轴哈希缓存至客观索引目录的 `index/masks/`，逐帧、每块最多 256 行读取；缓存写入采用临时文件和原子替换，并校验布尔数组形状与校验和。内存最多保留 3 个源的掩膜。

报告算法版本为 `objective-report-scope-process-cache-v4`，缓存身份包含实际掩膜方法，旧报告不复用为新版结果。

## 回退

设置 `OBJECTIVE_SEA_MASK=first_frame` 可临时恢复首帧域用于对照。它会产生单独的报告缓存。默认配置不设置此变量。即使回退，单帧、过程与最大值也保持同一首帧域，不再保留原有的海浪最大值特例。

## 已执行验证

`tests/test_objective_static_sea_mask.py` 覆盖 HDF5 和 scipy classic NC：首帧缺测后恢复的 9 m 峰值、各出口及峰值时刻一致、独立风峰值、阈值 `>`/`>=`、永久缺测透明、窗口外有效海点、整个窗口缺测、原生统计不依赖显示分辨率、缓存复用与损坏重建、最近邻覆盖裁剪、缩放与缺测解析一致。

真实已登记的 2026-10-21 20:00 批次核查：L2 静态海点比首帧多 263 个，L5 多 299 个。朱家尖 72 小时 L2 波浪有 1 个海点整窗缺测，保留为 73 个缺测样本；L2/L5 两套数据的风浪四组原生峰值、逐格最大值峰值及峰值单帧的值、地点和时刻一致。该核查只读已登记 NC，未调用模型、下载新数据或访问预览服务。

本地证据：`tmp/claude_review_20261022/p2_baseline_actual_dependencies.json`、`p2_fixed_actual_dependencies.json`、`p2_real_nc_mask_audit.json`。合成复现使用真实 h5py/scipy，没有 h5py 替身。
