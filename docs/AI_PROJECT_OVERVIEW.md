# 项目现状总览

本项目是基于 RuoYi-Vue 的工业设备健康管理平台，保留设备、测点、振动/温度历史数据、附件、诊断任务、诊断结果、报表和 WebSocket 展示能力。下位机文件接入已统一为 Spring 管理的 `CWRU_MAT_V2` TCP 服务。

## 当前数据链路

```text
采集仪 --TCP 8888/CWRU_MAT_V2--> Spring MAT 接收服务
  --> 协议/大小/SHA/MAT 签名校验
  --> 设备-测点-物理通道匹配
  --> 安全附件存储与 sensor_ingest_file 台账
  --> 测点唯一主模型绑定
  --> 诊断任务队列 --> Python /internal/infer
  --> 诊断结果、告警、WebSocket 推送
```

成功回执只表示附件和诊断任务已经入库并可靠入队，不表示 Python 推理已经完成。

## MAT V2 协议

发送顺序：

1. `CWRU_MAT_V2\n`
2. 4 字节大端 JSON 头长度
3. UTF-8 JSON 头
4. 服务端返回 `READY\n`
5. 原始 MAT 文件数据
6. 服务端返回单行 JSON 结果

JSON 头必须包含 `filename`、`filesize`、`sha256`、`deviceCode`、`pointCode`、`channelId` 和带时区的 `acquisitionTime`。文件大小为 1～128 MB，文件名只能是安全的 `.mat` 文件名，文件必须通过 MAT v5/HDF5 签名和 SHA-256 校验。平台不接受 `CWRU_MAT_V1`，也不接受旧 HTTP、8890、8891 或目录扫描接入。

最终状态：

- `ACCEPTED`：返回 `ingestId`、`attachmentId`、`taskId`；
- `DUPLICATE`：相同设备/测点/SHA 已接收，不重复诊断；
- `QUARANTINED`：设备、测点、物理通道或主模型绑定失败，文件进入隔离目录；
- `ERROR`：协议、大小、签名、连接或 SHA 校验失败。

测试发送器为 [cwru_mat_sender.py](../ruoyi-sensor/mock/cwru_mat_sender.py)。它会等待 `READY` 后再发送文件，并生成 V2 所需的完整 JSON 头。

## 关键代码

| 功能 | 代码 |
|---|---|
| 8888 TCP 接收、并发限制、优雅关闭、隔离、幂等 | `ruoyi-sensor/.../service/MatFileReceiverService.java` |
| 协议头校验 | `ruoyi-sensor/.../domain/dto/MatFileProtocolHeader.java` |
| 安全附件存储 | `ruoyi-sensor/.../service/PhmAttachmentStorageService.java` |
| 接收台账及人工关联/重试 | `ruoyi-sensor/.../service/SensorIngestFileService.java` |
| 公共诊断任务创建与执行 | `ruoyi-sensor/.../web/VibrationDiagnosisController.java` |
| 设备-测点-物理通道维护 | `ruoyi-sensor/.../service/PhmAcquisitionChannelService.java` |
| 主诊断模型绑定 | `ruoyi-sensor/.../domain/entity/PhmDiagnosisBindingEntity.java` |
| IoTDB 时序存储 | `ruoyi-sensor/.../service/timeseries/IoTdbTimeSeriesStore.java` |
| WebSocket 推送 | `ruoyi-sensor/.../websocket/SensorWebSocketHandler.java` |
| 生产配置校验 | `ruoyi-admin/.../config/ProductionConfigurationValidator.java` |
| MAT 接入配置页 | `ruoyi-ui/src/views/sensor/access/points.vue` |
| 接收台账页 | `ruoyi-ui/src/views/sensor/ingest/files.vue` |

## 数据与迁移

`V2026081602__MatOnlyIngressCleanup` 在删除旧结构前检查重复的设备/物理通道和重复启用主模型；发现冲突会抛出 Flyway 异常并阻断迁移，不静默丢数据。迁移会删除采集器凭据、旧实时策略表、旧任务关联列，并把 `phm_acquisition_channel` 简化为设备、测点、物理通道、采样率、单位、标定参数和启用状态。

历史振动、温度、诊断结果和附件仍可查询；手工诊断上传仍由诊断页面提供。自动 MAT 任务的 `source_type` 为 `MAT_TCP`，任务和结果使用协议头的采集时间，同时保留服务端接收时间。

Flyway 迁移配置（prod）：
- `baseline-on-migrate: true`，`baseline-version: 2026041700`
- `validate-on-migrate: true`，`clean-disabled: true`
- 迁移文件为 Java 类（非 SQL），位于 `ruoyi-admin/src/main/java/db/migration/`

## 配置和启动

默认配置在 `ruoyi-admin/src/main/resources/application.yml`，开发/生产覆盖在 `application-dev.yml` 和 `application-prod.yml`：

- `sensor.mat-receiver.enabled=true`
- `sensor.mat-receiver.bind-address=0.0.0.0`
- `sensor.mat-receiver.port=8888`
- 最大文件 128 MB
- 默认只启动 Spring 内置 MAT 接收服务，不启动旧接收器或旧采集认证链路

`start-all.ps1` 已从仓库删除。本地开发请使用 `bin/run.bat`（后端）和 `ruoyi-ui/bin/run-web.bat`（前端），或手动执行 Maven / npm 命令。

生产部署应通过防火墙限制 8888 来源；SHA-256 只保证文件完整性，不代表发送端身份认证。

## 环境变量

生产部署需配置的环境变量（参考 `.env.example`）：

```bash
# === 数据库（必须）===
MYSQL_URL=jdbc:mysql://localhost:3306/ry-yue?...
MYSQL_USERNAME=root
MYSQL_PASSWORD=<min 8 bytes>

LOWCODE_DB_URL=jdbc:mysql://localhost:3306/ry-lowcode?...
LOWCODE_DB_USERNAME=lowcode_runtime
LOWCODE_DB_PASSWORD=<min 16 bytes>

REDIS_HOST=localhost
REDIS_PASSWORD=<min 8 bytes>

IOTDB_NODE_URLS=iotdb-dn-1:6667,iotdb-dn-2:6667,iotdb-dn-3:6667
IOTDB_USERNAME=root
IOTDB_PASSWORD=<min 8 bytes>

# === 推理服务（必须）===
SENSOR_GEAR_INFER_URL=http://127.0.0.1:5001/internal/infer
SENSOR_BEARING_INFER_URL=http://127.0.0.1:5002/internal/infer
SENSOR_INFERENCE_INTERNAL_TOKEN=<min 32 bytes>

# === 文件路径（生产必须绝对路径）===
SENSOR_ATTACHMENT_ROOT=/data/phm/attachments
RUOYI_PROFILE=/data/phm/upload
LOG_PATH=/data/phm/logs

# === 安全（生产必须）===
CORS_ALLOWED_ORIGINS=https://your-domain.com
SENSOR_WS_ALLOWED_ORIGINS=https://your-domain.com
```

## 生产启动校验

`ProductionConfigurationValidator` 在 `prod` profile 启动时强制校验：

| 校验项 | 最低要求 |
|---|---|
| `sensor.inference.internal-token` | ≥ 32 字节 |
| `spring.datasource.password` | ≥ 8 字节 |
| `spring.data.redis.password` | ≥ 8 字节 |
| `sensor.iotdb.password` | ≥ 8 字节 |
| `lowcode.datasource.password` | ≥ 16 字节 |
| `lowcode.datasource.url` | **不得**等于 `spring.datasource.url`（独立 schema） |
| CORS origins / WS origins | **不得**含 `localhost`、`127.0.0.1`、`*` |
| `ruoyi.profile`、`logging.file.path`、`sensor.attachment.root` | 必须为绝对可写路径 |
| 所有 secret 值 | 不得含 `change-me`、`admin123`、`123456`、`root` 等开发默认值 |

## 验证

```bash
# Java 全量构建
mvn clean test package

# 单模块测试
mvn -pl ruoyi-sensor -am test \
    -Dtest=MatFileProtocolHeaderTest \
    -Dsurefire.failIfNoSpecifiedTests=false

# 质量门禁
mvn -DskipTests verify -Psecurity-gates

# 容器集成测试（需 Docker）
mvn -pl ruoyi-admin -am test \
    -Dtest=LowCodeV2MigrationTest,ProductionHardeningMigrationTest \
    -Dsurefire.failIfNoSpecifiedTests=false

# Python 推理
cd ruoyi-sensor/inference && python -m pytest

# Mock 数据采集
python ruoyi-sensor/mock/cwru_mat_sender.py --file <reference.mat> --once \
    --device-code DEV-001 --point-code CH1 --channel-id 1
```

协议测试应覆盖 V2 正常传输、V1 拒绝、非法头长度、超大文件、路径穿越、错误扩展名、伪造 MAT、SHA 不一致和连接中断；业务测试应覆盖正确映射、模型未绑定、通道不一致、隔离后人工修正、重复幂等和并发上传。
