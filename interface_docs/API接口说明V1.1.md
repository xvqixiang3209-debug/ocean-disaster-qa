# 浙江台风历史相似案例智能体 API（V1.2）

## 1. 核心接口

```http
POST /api/typhoon/similar
Content-Type: application/json
```

联调地址由部署服务器确定，例如：

```text
http://<服务器内网IP>:8766/api/typhoon/similar
```

正式环境应由浙江大学或中心 API 网关提供 HTTPS 域名、身份认证、来源 IP 白名单、限流和审计。应用本身目前未内置登录或 API Key 校验，因此不能把 8766 端口直接暴露到公网。

## 2. 重要的数据约定

相似路径采用 DTW 等路径特征计算，所以一次有效请求必须包含：

- 调用方的台风唯一编号 `sid`；
- 至少 2 个带时间和经纬度的当前/预报路径点，生产建议不少于 4 个；
- 所有路径点中至少有一个风速或中心气压值。

只提供“1909”或“第 9 号台风”之类的普通编号，目前不能直接计算相似路径。若对方只传编号，需要对方的台风数据服务先按编号补齐路径，或者双方另行约定“编号查询路径”的上游接口。`GET /api/typhoon/{sid}`使用的是 IBTrACS SID，仅用于查询已入库的历史台风，也不等同于普通业务编号。

## 3. 请求字段

| 字段 | 类型 | 必填 | 说明 |
|---|---|---:|---|
| `sid` | string | 是 | 本次台风唯一编号 |
| `name` | string | 否 | 中英文名称 |
| `source` | string | 否 | 数据来源，如 `CMA` |
| `issued_at` | ISO 8601 string | 否 | 资料发布时间，建议带时区 |
| `top_k` | integer | 否 | 返回相似案例数，默认 10 |
| `request_id` | string | 否 | 调用方追踪号 |
| `compact` | boolean | 否 | `false`返回完整资料；`true`返回紧凑候选 |
| `forecast_track` | array | 是 | 当前或预报路径 |
| `forecast_track[].time` | ISO 8601 string | 是 | 路径时刻，建议带时区 |
| `forecast_track[].lat` | number | 是 | 纬度，范围 -90 至 90 |
| `forecast_track[].lon` | number | 是 | 经度，范围 -180 至 180 |
| `forecast_track[].wind_kt` | number | 条件必填 | 最大风速，单位 kt |
| `forecast_track[].pressure_hpa` | number | 条件必填 | 中心气压，单位 hPa |

完整请求见 [request_example.json](./request_example.json)。

## 4. 返回字段

成功时返回 HTTP 200，主要内容包括：

| 字段 | 说明 |
|---|---|
| `request_id` | 本次分析追踪号，后续会话继续使用 |
| `current` | 归一化后的当前台风及路径 |
| `analogues` | 按路径相似度排序的历史台风数组 |
| `analogues[].similarity_score` | 相似度评分 |
| `analogues[].track_dtw_km` | 路径 DTW 距离 |
| `analogues[].track` | 历史完整路径（紧凑模式不返回） |
| `analogues[].storm_surge` | 最大增水、最高潮位、对应站点及数据性质 |
| `analogues[].historical_report` | 已关联的历史影响总结（存在时） |
| `analogues[].analysis` | 该案例的文本解释 |
| `report` | 全部相似案例的综合报告 |
| `report_meta` | 大模型/模板模式、回退原因、事实校验状态 |
| `processing_ms` | 服务端处理耗时，毫秒 |

`report_meta.used_mode="llm"`且`fallback=false`表示 DeepSeek 成功生成文本；若模型不可用，算法和数据库事实仍正常返回，但文本会自动回退到模板，此时`used_mode="template"`、`fallback=true`。

## 5. 调用示例

Linux：

```bash
curl --request POST 'http://127.0.0.1:8766/api/typhoon/similar' \
  --header 'Content-Type: application/json' \
  --data-binary '@interface_docs/request_example.json'
```

Windows PowerShell：

```powershell
$body = Get-Content -Raw -Encoding UTF8 .\interface_docs\request_example.json
Invoke-RestMethod -Method Post `
  -Uri http://127.0.0.1:8766/api/typhoon/similar `
  -ContentType 'application/json; charset=utf-8' `
  -Body $body `
  -TimeoutSec 120
```

## 6. 后续交互接口

先从`analogues`中取得某个历史台风的`sid`，再选择并提问：

```http
POST /api/typhoon/conversation/select
Content-Type: application/json

{"request_id":"上一步返回值","sid":"历史台风IBTrACS SID"}
```

```http
POST /api/typhoon/conversation/ask
Content-Type: application/json

{"request_id":"上一步返回值","sid":"历史台风IBTrACS SID","question":"该台风在浙江造成了什么影响？"}
```

问答接口会先执行选中 SID 范围内的本地 RAG 检索，再把结构化事实与召回证据交给 DeepSeek。返回的 `answer_meta.rag_search_executed`、`rag_raw_candidate_count`、`rag_evidence_count` 和 `rag_selected_sid` 可用于核验检索是否执行；`fact_reconciliation` 保留多来源原始数值，并说明是完全一致、微小误差、冲突还是口径不可比。

会话存放在当前服务进程的内存中；容器重启后原`request_id`失效。当前版本只支持单实例，不应让多个容器共享同一个 SQLite 数据库做负载均衡。

## 7. 健康检查与错误码

```http
GET /api/typhoon/status
```

常见错误：

| HTTP | `code` | 含义 |
|---:|---|---|
| 400 | `INVALID_CURRENT_TYPHOON` | 请求字段、坐标、时刻或路径不合法 |
| 400 | `INVALID_REQUEST` | 控制参数不合法 |
| 404 | `NOT_FOUND` / `SESSION_NOT_FOUND` | 台风或会话不存在 |
| 422 | `MATCH_FAILED` | 无法完成匹配 |
| 503 | `DATA_NOT_READY` | 历史库或默认当前台风资料未就绪 |
| 500 | `OUTPUT_VALIDATION_FAILED` | 输出事实一致性校验失败 |
| 500 | `INTERNAL_ERROR` | 未预期错误 |

调用方同步超时建议设置为 60 至 120 秒。若中心网关只能等待 3 秒，可先用`compact=true`获得候选，再调用详情/会话接口；正式异步任务接口需要后续单独设计。

OpenAPI 文件见 [openapi.yaml](./openapi.yaml)。

知识库管理接口（导入、检索验证和 SID 重建）主要供系统管理页面使用，详见 `docs/RAG知识库与交互使用说明.md`；对外业务系统通常只需调用相似台风与后续交互接口。
