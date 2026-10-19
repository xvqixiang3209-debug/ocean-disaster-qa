# NC快速解析

固定入口为 `tools/parse_nc.py`；Windows快捷入口为 `parse_nc.ps1`。脚本复用问答系统的 `inspect_grid`、`GridReader` 和 `ParsedGridStore`，不再按问题临时编写读取代码。工具只读取本地文件，不下载NC、不调用DeepSeek、不修改原始文件。

## 查看文件包含什么

在应用目录运行：

```powershell
.\parse_nc.ps1 inspect --level L5 --batch 202610162000 --output exports/nc_quick/catalog_202610162000_L5.json
```

未指定 `--input` 时，递归读取本应用 `data/forecast_runtime/grids`。也可指定单个NC或另一个目录。输出包含文件SHA256、产品、批次、变量原始描述和单位、经纬度范围、有效时间、时区假设及物理含义缺项；相同内容的文件只登记一次。

## 提取指定海点、指定时间的数值

以下为朱家尖区域参考海点示例，不能作为浴场精确定位：

```powershell
.\parse_nc.ps1 point --level L5 --batch 202610162000 --lon 122.4 --lat 29.9 --start 2026-10-17T00:00:00+08:00 --end 2026-10-17T23:00:00+08:00 --output exports/nc_quick/zhujiajian_20261017.json
```

输出两份文件：JSON保存逐文件来源、解释限制、最近有效格点及距离、时序、极值、有效样本数量和缓存状态；同名CSV保存可用数值及来源，方便Excel读取。CSV中的时间和数值可以交给DeepSeek组织回答，但需要同时携带JSON内的warnings。

同一输入文件、变量、坐标、时段和解析版本再次查询时，复用系统SQLite中已提取的结果，不重新读取整个数值网格。文件内容变化或查询条件变化会使用不同缓存。默认共享应用 `data/forecast_runtime/forecast.sqlite3`；`--cache-dir` 可指定独立缓存目录。查看目录的 `inspect` 命令不初始化缓存。

目录中存在多个批次时，`point` 要求用 `--batch` 明确选择；同一产品同时有L2和L5时用 `--level` 选择，不拼接批次或混合两个版本。全批次检查可省略这两项运行 `inspect`。单文件可以省略二者。

无时区输入按北京时间处理，带时区输入统一换算为北京时间。`--start`、`--end` 包含边界；仅填写日期时，起始为当天00:00，结束为当天23:59:59。返回的partial标记仍按实际文件有效时间判断。数值缺测、位置越界、超出有效时段会明确标记，不能补零或补造极值。默认最近有效格点上限20公里，可用 `--max-distance` 调整；实际取样距离保留在结果中。

## 支持范围与数据含义

当前支持已接入的L2/L5产品文件命名、NetCDF4/HDF5、一维lat/lon和time×lat×lon变量；不声称支持所有NC格式或所有模式。`--variable` 可以读取文件内指定变量，默认读取对应产品；SSW由u10/v10、OCU由u/v计算矢量模。SWL不在本工具中叠加天文潮或计算超警。

- SWL纯增水/总潮位和高程基准尚未核定。
- SSW阵风/平均风及平均时段尚未核定。
- L5 WAV最大有效波高统计口径尚未核定；异常MWD不解释成波向。
- OCU的水层未说明，不能确认浴场离岸流。
- SST原单位Celsis D保留，不伪装成已核定单位。
- 文件未写时区或仅写initial time时，继承系统明确标注的时间假设。

这些是文件口径问题，DeepSeek不能通过语言推理补成事实。固定解析器负责读取、计算和缓存；DeepSeek负责理解问题、调用业务查询、结合来源解释结果。网页问答已有同一解析器的调用链，不需要用户逐次运行命令。

运行 `python tools/parse_nc.py --help` 或 `python tools/parse_nc.py point --help` 查看参数。PowerShell入口使用应用虚拟环境，普通Python入口需要安装本项目requirements中的h5py和numpy。

退出码：0表示完整成功；3表示部分结果或没有覆盖数据，需查看JSON；2表示参数、文件解析或写入失败。JSON中的每个文件状态和错误均保留，批量处理中坏文件不会冒充成功。
