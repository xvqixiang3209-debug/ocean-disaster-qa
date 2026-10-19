# Windows部署说明

本方案适用于Windows 10/11或Windows Server试点部署。应用使用Waitress运行Flask服务，公网访问由IIS、政务云API网关或其他受信任的HTTPS反向代理提供。

## 1. 部署结构

```text
用户浏览器/调用系统
        │ HTTPS 443
        ▼
政务云网关或IIS（证书、鉴权、限流、访问日志）
        │ http://127.0.0.1:8766
        ▼
Waitress + Flask应用
        ├─ SQLite历史台风与风暴潮知识库
        ├─ 当前台风输入接口
        └─ 大模型API或后续本地模型服务
```

不要将Flask自带开发服务器或8766端口直接暴露到互联网。

## 2. 4090显卡说明

当前版本通过OpenAI兼容API调用DeepSeek，台风DTW检索在CPU上运行，因此4090显卡暂时不会被使用，也不影响现有功能。若正式环境不允许访问公网模型，可在4090电脑上另行部署本地大模型服务，再将`DEEPSEEK_BASE_URL`和`DEEPSEEK_MODEL`改成本地服务提供的OpenAI兼容地址及模型编码。是否采用本地模型需先确认模型、显存需求和政务云安全要求。

## 3. 服务器需要准备

- 64位Python 3.11；
- 建议4核CPU、8GB内存、5GB以上可用磁盘；
- 固定内网地址；
- IIS/政务云网关或经批准的反向代理；
- 公网域名、HTTPS证书和访问控制策略；
- 若使用外部DeepSeek，允许服务器访问`api.deepseek.com:443`；
- 若服务器不能访问PyPI，准备与服务器Python版本和CPU架构一致的离线wheelhouse。

## 4. 推荐目录

为了后续增加灾害统计、台风总结和海浪数据，建议将代码、配置和数据分开：

```text
D:\ocean_qa\
├─ app\                  当前版本代码
├─ shared\
│  ├─ data\              历史台风及后续扩展数据
│  ├─ current\           当前台风输入文件
│  └─ backup\            数据库和配置备份
└─ logs\                 由运维平台或服务管理器采集
```

首版可以直接使用项目内的SQLite文件。正式更新前，建议把数据库移到`shared\data`，并在`.env`中使用绝对路径。后续替换`app`目录时不覆盖`shared`目录。

## 5. 安装

将项目复制到服务器，例如：

```text
D:\ocean_qa\app
```

管理员或部署账号打开PowerShell：

```powershell
cd D:\ocean_qa\app
Set-ExecutionPolicy -Scope Process Bypass
.\deploy\windows\install.ps1
```

安装脚本会创建`.venv`、安装依赖，并在缺少`.env`时从示例创建。`.env`已被`.gitignore`排除，禁止将其加入代码包或版本库。

如果政务云不能连接PyPI，先在一台与服务器环境一致且可联网的Windows电脑上下载：

```powershell
py -3.11 -m pip download -r requirements.txt -d wheelhouse
```

将`wheelhouse`复制到服务器后执行：

```powershell
.\deploy\windows\install.ps1 -Offline -Wheelhouse .\wheelhouse
```

## 6. 配置

编辑项目根目录下的`.env`。至少核对：

```dotenv
APP_HOST=127.0.0.1
APP_PORT=8766
APP_THREADS=8
TIANDITU_API_KEY=天地图浏览器端Key

TYPHOON_DB_TYPE=sqlite
TYPHOON_SQLITE_PATH=D:/ocean_qa/shared/data/typhoon_history.sqlite3
TYPHOON_STORM_SURGE_PATH=D:/ocean_qa/shared/data/typhoon_history.sqlite3
TYPHOON_CURRENT_FILE=D:/ocean_qa/shared/current/current_typhoon.json

TYPHOON_REPORT_MODE=llm
TYPHOON_LLM_TIMEOUT_SECONDS=20
DEEPSEEK_API_KEY=由政务云密钥管理或运维人员注入
DEEPSEEK_BASE_URL=https://api.deepseek.com
DEEPSEEK_MODEL=deepseek-v4-flash
```

天地图 Key 请在 `https://console.tianditu.gov.cn/` 申请浏览器端/Web应用类型。若配置来源白名单，请加入服务器实际内网IP或正式域名。修改 Key 后需要重启服务。

如果使用省平台或本地模型，将后三项替换为平台提供的密钥、OpenAI兼容地址和模型编码。不要把真实密钥发到群聊、文档、截图或部署压缩包中。

## 7. 启动和本机验收

启动生产服务：

```powershell
cd D:\ocean_qa\app
.\deploy\windows\start.ps1
```

另开一个PowerShell窗口运行：

```powershell
cd D:\ocean_qa\app
.\deploy\windows\test.ps1
```

验收结果应包括：状态接口成功、返回10个历史案例、`fact_validation=pass`。`used_mode=llm`且`fallback=False`表示模型调用成功；发生模型超时或网络故障时会返回模板报告。

## 8. 让局域网其他电脑访问

仅用于受控内网测试时，可以将`.env`改为：

```dotenv
APP_HOST=0.0.0.0
```

然后由管理员在Windows防火墙中仅允许指定内网网段访问8766端口。其他电脑通过`http://服务器内网IP:8766`访问。测试完成后恢复`APP_HOST=127.0.0.1`并关闭该防火墙规则。

## 9. 配置正式公网地址

公网地址不能仅靠修改Python代码获得，需要网络或政务云管理员完成：

1. 分配固定公网IP或政务云网关入口；
2. 将正式域名解析到公网入口；
3. 在网关或IIS绑定HTTPS证书和443端口；
4. 将请求反向代理至`http://127.0.0.1:8766`；
5. 配置统一身份认证、IP白名单或API令牌、限流、访问日志和请求体大小；
6. Windows防火墙不对公网开放8766，只允许反向代理访问；
7. 对外发布业务接口`POST /api/typhoon/similar`，健康检查使用`GET /api/typhoon/status`。

当前应用自身没有用户登录和公网限流能力，因此在网关认证完成前，不得面向不受信任的互联网公开，否则可能导致数据暴露和模型额度被滥用。

## 10. 设置开机自启

由服务器运维人员使用单位批准的Windows服务管理方式运行：

- 程序：`D:\ocean_qa\app\.venv\Scripts\python.exe`
- 参数：`production_server.py`
- 工作目录：`D:\ocean_qa\app`
- 运行账号：低权限专用服务账号；
- 恢复策略：失败后自动重启；
- 日志：接入政务云或Windows日志采集系统。

不建议使用个人管理员账号长期运行服务。

## 11. 后续增加数据和更新代码

灾害统计、台风总结和海浪数据尚未准备好时，不应阻塞首版部署。后续按独立数据源接入：

1. 确认数据表、字段、单位、时间、空间范围和台风编号映射；
2. 在测试环境新增对应仓储/Provider，不直接修改现有DTW排名事实；
3. 增加接口契约和自动化测试；
4. 备份`.env`和数据库；
5. 停止服务并替换代码版本；
6. 安装新增依赖、执行数据迁移；
7. 运行完整测试和样例接口；
8. 验证通过后重新启动服务，失败则回退上一版本。

建议采用`1.0.0`、`1.1.0`等版本号保存发布包和数据库结构版本，避免直接在生产目录临时改代码。

## 12. 上线前清单

- [ ] 真实密钥未写入代码、压缩包或日志；
- [ ] SQLite/MySQL数据已备份；
- [ ] 当前台风输入方式已确认；
- [ ] 外部模型或省平台模型网络已连通；
- [ ] HTTPS、身份认证、IP白名单和限流已配置；
- [ ] 8766端口未直接暴露公网；
- [ ] 状态接口和相似台风接口通过验收；
- [ ] 模型失败时模板回退正常；
- [ ] Windows服务账号和自动重启已配置；
- [ ] 日志不记录密钥和完整敏感请求。
# v1.2 RAG与单台风连续问答

本版本新增本地 RAG 知识库、网页知识导入、单台风选择和连续问答，并将地图调整为主区域约三分之二宽度。

- 启动后打开首页，单击右上角“知识库管理”即可上传文档，系统自动解析、切片和建立 SQLite FTS5/BM25 索引。
- 初次类比只返回 10 个紧凑候选；单击一个候选后才读取完整路径和详情。
- 选中台风后，右侧对话框自动切换到该台风的 RAG 连续问答。
- 部署和备份时必须同时保留 `typhoon\data\typhoon_history.sqlite3` 与 `typhoon\data\typhoon_rag.sqlite3`。
- 详细操作见 `docs\RAG知识库与交互使用说明.md`。
