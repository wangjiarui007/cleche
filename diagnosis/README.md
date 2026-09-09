# 轴承在线诊断服务

本目录提供 FastAPI 在线接口、质量检查、共用模型推理与拒识、报警状态机和 SQLite 持久化。
Python **3.10 或更高版本**。推荐单进程部署（`workers=1`），默认仅监听 `127.0.0.1:8000`。

## 安装与启动

在本目录执行 PowerShell 命令：

```powershell
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements-dev.txt
.\.venv\Scripts\python.exe main.py
```

本次验证环境已创建 `.venv`，复用了本机的 NumPy、SciPy 和 PyTorch，并补齐 Web 和客户端依赖；可直接执行上述启动命令。
正式部署应在目标机器重新建立环境，并根据目标 GPU 安装匹配的 PyTorch。

也可以从本目录使用：

```powershell
.\.venv\Scripts\python.exe -m uvicorn api.diagnosis_api:app --workers 1
```

或者在上级 `diagnosis_service` 目录执行 `python -m diagnosis.main`，或
`python -m uvicorn diagnosis.api.diagnosis_api:app --workers 1`。
请使用已安装依赖的 Python。默认模型和数据库路径均相对于 `main.py`，不依赖工作目录。

服务在应用 lifespan 启动阶段加载模型，加载失败则启动失败；关闭时释放数据库。
`model/model_sevice.py` 保留为旧拼写的兼容入口，正式入口是 `model/model_service.py`。

## 请求与返回

- `GET /health`：模型初始化和数据库可用后返回 200；未就绪返回 503。
- `POST /diagnose`：在线诊断。
- `GET /device/{device_id}`：当前诊断状态与报警级别；设备不存在返回 404。
- `GET /history/{device_id}?limit=20`：完整历史结果；`limit` 为 1–1000。

诊断 JSON 示例（`signal` 需替换为实际的一维数据）：

```json
{
  "device_id": "bearing_01",
  "batch_id": "acquisition-000001",
  "rpm": 1600,
  "sampling_rate": 16000,
  "collected_at": "2026-09-08T09:00:00+08:00",
  "signal": [0.12, -0.03, 0.08]
}
```

示例的三个采样点会得到 `DATA_INVALID`；当前权重窗口为 4096 点，至少需要 4 个窗口才能通过最少窗口数要求。
当前模型支持转速为 **600–3000 rpm**，采样率为 **16000 Hz**。在线接口明确拒绝采样率不匹配，不自动重采样。

`collected_at` 可省略以兼容旧客户端；现场采集端应提供带时区的真实采集时间。
提供后会校验过期数据、未来时间（允许 30 秒时钟偏差）以及同一设备的严格时间递增。
省略时按接收顺序处理，无法判断采集端是否乱序。样本时间应在上传时保持不变。
不同通道应使用不同的 `device_id`，避免混合报警状态。

批次规则：

- `(device_id, batch_id)` 为幂等键，同批次同内容返回第一次保存的完整结果，不再推理或累计报警。
- 同批次不同信号、转速、采样率或采集时间返回 **409**。
- **503** 表示未就绪、繁忙或存储失败；客户端应使用原批次和原内容重试。
- **422** 表示请求格式或参数错误，**413** 表示请求体过大，**408** 表示上传超时。
- 模型执行异常返回 **500**，事务回滚，重试不会留下半条记录。
- 正常处理的质量异常、采样率错误、超工况或拒识以 200 返回业务状态，并记录到设备历史。

业务状态包括 `HEALTHY`、`FAULT`、`UNCERTAIN`、`DATA_INVALID`、`FS_ERROR`、`OUT_OF_RANGE`。
拒识时 `fault_type=null`，候选类别放在 `candidate_fault_type`；`reason` 保存拒识原因。
`probability` 和 `confidence` 使用融合决策概率；`direct_probability` 单独保留直接分类头的概率。
每条新结果保存模型 SHA-256、质量指标、拒识原因、采集时间和接收时间。

## 报警、并发与恢复

连续同类故障 3 次进入 WARNING、5 次进入 ALARM；已有报警只在连续 10 次健康后解除。
再次出现故障不会把 ALARM 降为 NORMAL。未知、无效数据或超过连续性间隔会清零连续计数，但保留已有报警。
更换故障类别会重新累计该类别的连续次数，已有报警继续保留。
超过状态时效后，查询返回 `STALE`，同时保留上一次诊断状态和报警级别。

服务限制待处理请求数，并串行执行推理。数据库使用 WAL、FULL 同步、线程锁和 `BEGIN IMMEDIATE`。
诊断结果、报警历史、设备状态、报警计数和幂等回执在一个事务内提交；失败全部回滚。
多连接访问同一 SQLite 文件仍由事务保护，但推理在事务期间占用写锁，因此推荐单 worker；这不是高吞吐多 GPU 调度实现。

升级时保留原来的三张表和历史数据，补充 `result_json` 字段、查询索引、报警状态表及批次回执表。
旧数据库的活动报警会保守保留；旧版本未保存的正常计数、完整质量原因和批次内容无法追溯恢复。
幂等保障从升级后首次入库的批次开始。旧数据库上线前请备份；本次测试仅使用临时数据库。

## 配置

通过环境变量设置；模型结构、类别顺序、决策与拒识阈值从 checkpoint 恢复。

| 变量 | 默认值 | 用途 |
|---|---|---|
| `DIAGNOSIS_MODEL_PATH` | `model/best_model.pth`（相对代码目录） | 权重 |
| `DIAGNOSIS_DATABASE_PATH` | `database/diagnosis.db`（相对代码目录） | 数据库 |
| `DIAGNOSIS_DEVICE` | `cuda` | 无 CUDA 自动回退 CPU |
| `DIAGNOSIS_BATCH_SIZE` | `32` | 单次模型推理的窗口数 |
| `DIAGNOSIS_MAX_SAMPLES` | `1600000` | 单条信号最大点数 |
| `DIAGNOSIS_MAX_BODY_BYTES` | `33554432` | JSON 请求体上限，含分块上传 |
| `DIAGNOSIS_MAX_PENDING` | `4` | 同时上传或等待诊断的请求数 |
| `DIAGNOSIS_STALE_SECONDS` | `60` | 设备状态时效及报警连续性间隔 |
| `DIAGNOSIS_MAX_DATA_AGE_SECONDS` | `300` | 带采集时间的新批次最大年龄 |
| `DIAGNOSIS_CLIP_THRESHOLD` | 未设置 | 传感器/ADC 饱和阈值，单位须与信号一致 |
| `DIAGNOSIS_HOST` | `127.0.0.1` | `main.py` 监听地址 |
| `DIAGNOSIS_PORT` | `8000` | `main.py` 监听端口 |

服务使用 Python logging 记录设备、批次、业务状态和耗时，不记录原始波形。
默认没有自动删除历史或幂等回执；磁盘容量、备份归档与日志轮转需要按现场保存周期配置。
该服务未实现用户认证；需要远程访问时，应接入现场已有的认证网关与访问控制。

## 模拟采集

```powershell
.\.venv\Scripts\python.exe simulate_online_client.py --limit 3 --interval 10
```

递归扫描 MAT 文件，明确读取 `DE_time`，优先读取文件内转速和采样率，缺失时解析文件名。
不再固定使用 2400 rpm，不会把时间轴误选为振动信号。采集溢出记录拒绝上传。
模拟器将历史波形视为新采集批次并赋予当前时间，失败重试保持原批次内容；这不是离线准确率评估工具。

## 验证

```powershell
.\.venv\Scripts\python.exe -B -m unittest discover -s tests -v
.\.venv\Scripts\python.exe -B tests/smoke_server.py
.\.venv\Scripts\python.exe -B tests/smoke_server.py --package
```

本次修复验证：**30 项回归测试通过**，包括真实 checkpoint 加载、校准拒识、报警迟滞、重启恢复、重复批次、并发连接、事务回滚、异常记录、API 校验和客户端真实元数据。
实际启动服务并经 HTTP 上传一个 800 rpm 正常 MAT 文件：处理 39 个窗口，返回 HEALTHY；重复上传后历史仍为 1 条。
直接入口和包入口均验证通过，测试服务退出后关闭；单次 CPU 服务耗时约 56–62 ms，仅为本机冒烟样本结果，不代表现场时延保证。

尚未进行 GPU 压测、长时间稳定性试验或完整数据集准确率验收。正式现场验收还需覆盖实际通道数、上传周期、工况和传感器量程。

Python 3.10 兼容验证：已在 `paper2_mghcmn`（Python 3.10.20）运行全部 30 项回归测试及包启动 HTTP 冒烟测试，均通过。模型摘要使用分块 SHA-256，上传总超时使用 `asyncio.wait_for`，无需升级 Python。
