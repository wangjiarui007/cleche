# 接口与 API 总览

> 本文档基于代码库静态分析生成，涵盖所有 REST 端点、WebSocket 协议及 Python 推理接口。

---

## 目录

- [1. 认证与基础接口](#1-认证与基础接口)
- [2. PHM 健康管理接口](#2-phm-健康管理接口)
- [3. 诊断与推理接口](#3-诊断与推理接口)
- [4. 振动与温度数据接口](#4-振动与温度数据接口)
- [5. 实时监控接口](#5-实时监控接口)
- [6. 数据接入与模型管理接口](#6-数据接入与模型管理接口)
- [7. 系统管理接口](#7-系统管理接口)
- [8. 低代码工作台接口](#8-低代码工作台接口)
- [9. WebSocket 实时协议](#9-websocket-实时协议)
- [10. Python 推理服务内部接口](#10-python-推理服务内部接口)
- [11. MAT V2 TCP 协议](#11-mat-v2-tcp-协议)

---

## 1. 认证与基础接口

基础路径：无（`ruoyi-admin`）

| 方法 | 路径 | 说明 |
|---|---|---|
| POST | `/login` | 用户登录（返回 Redis 会话 Cookie） |
| GET | `/csrf` | 获取 CSRF Token |
| GET | `/getInfo` | 获取当前用户信息、角色、权限列表 |
| GET | `/getRouters` | 获取当前用户菜单路由树 |
| GET | `/captchaImage` | 生成验证码图片（Base64） |

---

## 2. PHM 健康管理接口

控制器：`PhmController`（`ruoyi-sensor`）
基础路径：`/phm`

### 2.1 设备管理

| 方法 | 路径 | 说明 |
|---|---|---|
| GET | `/phm/devices` | 设备列表（支持 `keyword` 过滤） |
| GET | `/phm/devices/cluster` | 设备集群分组（按组织/状态） |
| GET | `/phm/devices/{deviceId}/brain` | 设备"大脑"（诊断摘要） |
| POST | `/phm/devices/{deviceId}/favorite` | 切换设备收藏状态 |
| POST | `/phm/devices` | 创建设备 |
| PUT | `/phm/devices` | 更新设备 |
| DELETE | `/phm/devices/{deviceId}` | 删除设备 |

### 2.2 测点管理

| 方法 | 路径 | 说明 |
|---|---|---|
| GET | `/phm/points` | 测点列表（支持 `deviceId` 过滤） |
| GET | `/phm/points/{pointId}/features/trend` | 测点特征趋势（`featureCode`） |
| POST | `/phm/points` | 创建测点 |
| PUT | `/phm/points` | 更新测点 |
| DELETE | `/phm/points/{pointId}` | 删除测点 |

### 2.3 特征配置

| 方法 | 路径 | 说明 |
|---|---|---|
| GET | `/phm/features` | 特征配置列表 |
| POST | `/phm/features` | 创建特征配置 |
| PUT | `/phm/features` | 更新特征配置 |
| DELETE | `/phm/features/{featureId}` | 删除特征配置 |

### 2.4 告警管理

| 方法 | 路径 | 说明 |
|---|---|---|
| GET | `/phm/alarms` | 告警列表（`deviceCode`、`status`、`alarmLevel`、`alarmSource`） |
| GET | `/phm/alarms/{alarmId}` | 告警详情 |
| GET | `/phm/alarms/{alarmId}/timeline` | 告警时间线 |
| POST | `/phm/alarms/{alarmId}/handle` | 处理告警 |
| POST | `/phm/alarms/{alarmId}/ignore` | 忽略告警 |
| POST | `/phm/alarms/{alarmId}/acknowledge` | 确认告警 |
| POST | `/phm/alarms/{alarmId}/assign` | 指派告警 |
| POST | `/phm/alarms/{alarmId}/close` | 关闭告警 |

### 2.5 告警规则

| 方法 | 路径 | 说明 |
|---|---|---|
| GET | `/phm/alarm-rules` | 告警规则列表 |
| POST | `/phm/alarm-rules` | 创建告警规则 |
| PUT | `/phm/alarm-rules` | 更新告警规则 |
| DELETE | `/phm/alarm-rules/{ruleId}` | 删除告警规则 |

### 2.6 设备事件与报表

| 方法 | 路径 | 说明 |
|---|---|---|
| GET | `/phm/device-events` | 设备事件列表 |
| POST | `/phm/device-events` | 创建设备事件 |
| PUT | `/phm/device-events` | 更新设备事件 |
| DELETE | `/phm/device-events/{eventId}` | 删除设备事件 |
| GET | `/phm/reports/realtime` | 实时报表 |
| POST | `/phm/reports/realtime/export` | 导出实时报表（CSV） |
| GET | `/phm/reports/history` | 历史报表 |
| POST | `/phm/reports/history/export` | 导出历史报表（CSV） |

### 2.7 附件与系统配置

| 方法 | 路径 | 说明 |
|---|---|---|
| GET | `/phm/attachments` | 附件列表 |
| POST | `/phm/attachments/upload` | 上传附件（multipart） |
| GET | `/phm/attachments/{attachmentId}/content` | 下载附件 |
| PUT | `/phm/attachments` | 更新附件元数据 |
| DELETE | `/phm/attachments/{attachmentId}` | 删除附件 |
| GET | `/phm/system-config` | 系统配置列表 |
| POST | `/phm/system-config` | 创建系统配置 |
| PUT | `/phm/system-config` | 更新系统配置 |

---

## 3. 诊断与推理接口

控制器：`VibrationDiagnosisController`（`ruoyi-sensor`）
基础路径：`/sensor/diagnosis`

### 3.1 诊断任务

| 方法 | 路径 | 说明 |
|---|---|---|
| GET | `/sensor/diagnosis/options` | 诊断 UI 选项（设备、测点、模型） |
| GET | `/sensor/diagnosis/overview` | 诊断概览（按部门/设备/测点） |
| POST | `/sensor/diagnosis/tasks` | 创建手动诊断任务 |
| GET | `/sensor/diagnosis/tasks/{id}` | 获取任务详情 |
| POST | `/sensor/diagnosis/receiver/analyze` | 提交 MAT 接收分析 |

### 3.2 批量诊断

| 方法 | 路径 | 说明 |
|---|---|---|
| POST | `/sensor/diagnosis/batches` | 创建多测点诊断批次 |
| GET | `/sensor/diagnosis/batches/{id}` | 获取批次状态/摘要 |
| POST | `/sensor/diagnosis/batches/{id}/retry` | 重试批次中失败项 |

### 3.3 推理接口

| 方法 | 路径 | 说明 |
|---|---|---|
| GET | `/sensor/diagnosis/inference/health` | Python 推理健康检查 |
| GET | `/sensor/diagnosis/inference/files` | 可用诊断输入文件列表 |
| GET | `/sensor/diagnosis/inference/analyze` | 运行诊断分析 |
| POST | `/sensor/diagnosis/inference/upload` | 上传诊断文件 |
| GET | `/sensor/diagnosis/inference/history` | 诊断推理历史 |

### 3.4 分析数据

| 方法 | 路径 | 说明 |
|---|---|---|
| GET | `/sensor/diagnosis/analysis/timeseries` | 时序分析数据（含 FFT） |
| GET | `/sensor/diagnosis/diagnosis/latest` | 最新诊断结果 |
| GET | `/sensor/diagnosis/diagnosis/trend` | 7 天健康指数趋势 |
| GET | `/sensor/diagnosis/device/list` | 诊断用设备列表 |

### 3.5 振动分析（VibrationAnalysisController）

| 方法 | 路径 | 说明 |
|---|---|---|
| POST | `/sensor/diagnosis/analysis/analyze` | 单信号振动分析 |
| POST | `/sensor/diagnosis/analysis/batchAnalyze` | 批量信号振动分析 |

### 3.6 分析批次（VibrationBatchController）

基础路径：`/sensor/diagnosis/batch`

| 方法 | 路径 | 说明 |
|---|---|---|
| GET | `/sensor/diagnosis/batch/list` | 批次列表 |
| GET | `/sensor/diagnosis/batch/page` | 分页批次列表 |
| GET | `/sensor/diagnosis/batch/detail/{batchId}` | 批次详情 |
| POST | `/sensor/diagnosis/batch` | 创建分析批次 |
| PUT | `/sensor/diagnosis/batch` | 更新分析批次 |
| DELETE | `/sensor/diagnosis/batch/{batchIds}` | 删除分析批次 |

---

## 4. 振动与温度数据接口

### 4.1 振动数据（DeviceVibrationDataController）

基础路径：`/sensor/vibration-data`

| 方法 | 路径 | 说明 |
|---|---|---|
| GET | `/sensor/vibration-data/list` | 分页振动数据列表 |
| GET | `/sensor/vibration-data/recent` | 最近振动数据 |
| GET | `/sensor/vibration-data/multi-channel/overview` | 八通道概览 |
| GET | `/sensor/vibration-data/multi-channel/{channelId}/analysis` | 单通道分析 |
| POST | `/sensor/vibration-data/export` | 导出（CSV） |
| GET | `/sensor/vibration-data/{dataId}` | 按 ID 查询 |
| POST | `/sensor/vibration-data` | 新增 |
| PUT | `/sensor/vibration-data` | 更新 |
| DELETE | `/sensor/vibration-data/{dataIds}` | 删除 |

### 4.2 温度数据（DeviceTemperatureDataController）

基础路径：`/sensor/temperature-data`

| 方法 | 路径 | 说明 |
|---|---|---|
| GET | `/sensor/temperature-data/list` | 分页温度数据列表 |
| GET | `/sensor/temperature-data/recent` | 最近温度数据 |
| POST | `/sensor/temperature-data/export` | 导出（CSV） |
| GET | `/sensor/temperature-data/{dataId}` | 按 ID 查询 |
| POST | `/sensor/temperature-data` | 新增 |
| PUT | `/sensor/temperature-data` | 更新 |
| DELETE | `/sensor/temperature-data/{dataIds}` | 删除 |

---

## 5. 实时监控接口

控制器：`IndustrialMonitoringController` + `MonitoringController`
基础路径：`/sensor/monitoring`

| 方法 | 路径 | 说明 |
|---|---|---|
| GET | `/sensor/monitoring/overview` | 监控仪表盘概览 |
| GET | `/sensor/monitoring/timeseries/health` | IoTDB 健康检查 |
| GET | `/sensor/monitoring/assets/tree` | 资产层级树 |
| GET | `/sensor/monitoring/workbench` | 工作台概览 |
| GET | `/sensor/monitoring/points/{pointId}/trend` | 测点趋势数据 |
| GET | `/sensor/monitoring/points/{pointId}/vibration-analysis` | 测点振动分析 |
| GET | `/sensor/monitoring/points/{pointId}/temperature-analysis` | 测点温度分析 |

---

## 6. 数据接入与模型管理接口

### 6.1 MAT 接收台账（SensorIngestFileController）

基础路径：`/sensor/ingest/files`

| 方法 | 路径 | 说明 |
|---|---|---|
| GET | `/sensor/ingest/files/list` | MAT 接收台账分页列表 |
| PUT | `/sensor/ingest/files/{id}/point` | 人工关联测点 |
| POST | `/sensor/ingest/files/{id}/retry` | 重试失败的 MAT 接收 |

### 6.2 采集通道（PhmAcquisitionChannelController）

基础路径：`/sensor/access/channels`

| 方法 | 路径 | 说明 |
|---|---|---|
| GET | `/sensor/access/channels/list` | 通道分页列表 |
| GET | `/sensor/access/channels/options` | 通道下拉选项 |
| POST | `/sensor/access/channels` | 创建采集通道 |
| PUT | `/sensor/access/channels` | 更新采集通道 |
| DELETE | `/sensor/access/channels/{ids}` | 删除采集通道 |

### 6.3 模型管理（ModelReleaseController）

基础路径：`/sensor/diagnosis/models`

| 方法 | 路径 | 说明 |
|---|---|---|
| GET | `/sensor/diagnosis/models` | 模型发布列表 |
| POST | `/sensor/diagnosis/models` | 注册新模型（DRAFT） |
| POST | `/sensor/diagnosis/models/{id}/activate` | 激活模型 |
| POST | `/sensor/diagnosis/models/{id}/shadow/start` | 启动影子验证 |
| POST | `/sensor/diagnosis/models/{id}/shadow/complete` | 完成影子验证 |

### 6.4 WebSocket 票据（WebSocketTicketController）

| 方法 | 路径 | 说明 |
|---|---|---|
| POST | `/sensor/ws-ticket` | 签发一次性 WebSocket 认证票据 |

---

## 7. 系统管理接口

### 7.1 用户管理（SysUserController）

基础路径：`/system/user`

| 方法 | 路径 | 说明 |
|---|---|---|
| GET | `/system/user/list` | 分页用户列表 |
| POST | `/system/user/export` | 导出（CSV） |
| POST | `/system/user/importData` | Excel 导入 |
| GET | `/system/user/{userId}` | 用户详情 |
| POST | `/system/user` | 创建用户 |
| PUT | `/system/user` | 更新用户 |
| DELETE | `/system/user/{userIds}` | 删除用户 |
| PUT | `/system/user/resetPwd` | 重置密码 |
| PUT | `/system/user/changeStatus` | 启用/禁用 |
| GET | `/system/user/authRole/{userId}` | 获取角色授权 |
| PUT | `/system/user/authRole` | 授予角色 |
| GET | `/system/user/deviceAuth/{userId}` | 获取设备访问权限 |
| PUT | `/system/user/deviceAuth` | 更新设备访问权限 |

### 7.2 角色管理（SysRoleController）

基础路径：`/system/role`

| 方法 | 路径 | 说明 |
|---|---|---|
| GET | `/system/role/list` | 角色列表 |
| GET | `/system/role/{roleId}` | 角色详情 |
| POST | `/system/role` | 创建角色 |
| PUT | `/system/role` | 更新角色 |
| PUT | `/system/role/changeStatus` | 启用/禁用 |
| DELETE | `/system/role/{roleIds}` | 删除角色 |
| GET | `/system/role/optionselect` | 角色下拉列表 |

### 7.3 菜单管理（SysMenuController）

基础路径：`/system/menu`

| 方法 | 路径 | 说明 |
|---|---|---|
| GET | `/system/menu/list` | 菜单列表 |
| GET | `/system/menu/treeselect` | 菜单树下拉 |
| POST | `/system/menu` | 创建菜单 |
| PUT | `/system/menu` | 更新菜单 |
| DELETE | `/system/menu/{menuId}` | 删除菜单 |

### 7.4 字典与配置

| 方法 | 路径 | 说明 |
|---|---|---|
| GET | `/system/dict/type/list` | 字典类型列表 |
| POST | `/system/dict/type` | 创建字典类型 |
| GET | `/system/dict/data/list` | 字典数据列表 |
| POST | `/system/dict/data` | 创建字典数据 |
| GET | `/system/config/list` | 配置列表 |
| GET | `/system/config/configKey/{configKey}` | 按 key 查询 |
| POST | `/system/config` | 创建配置 |

### 7.5 用户个人（SysProfileController）

基础路径：`/system/user/profile`

| 方法 | 路径 | 说明 |
|---|---|---|
| GET | `/system/user/profile` | 获取个人信息 |
| PUT | `/system/user/profile` | 更新个人信息 |
| PUT | `/system/user/profile/updatePwd` | 修改密码 |
| POST | `/system/user/profile/avatar` | 上传头像 |

### 7.6 监控管理

| 方法 | 路径 | 说明 |
|---|---|---|
| GET | `/monitor/logininfor/list` | 登录日志列表 |
| GET | `/monitor/online/list` | 在线用户列表 |
| DELETE | `/monitor/online/{tokenId}` | 强制下线 |
| GET | `/monitor/operlog/list` | 操作日志列表 |

---

## 8. 低代码工作台接口

### 8.1 项目管理（LowCodeProjectController）

基础路径：`/tool/lowcode/projects`

| 方法 | 路径 | 说明 |
|---|---|---|
| GET | `/tool/lowcode/projects` | 项目列表 |
| GET | `/tool/lowcode/projects/{id}` | 项目详情 |
| POST | `/tool/lowcode/projects` | 创建项目 |
| PUT | `/tool/lowcode/projects/{id}/draft` | 保存草稿 |
| POST | `/tool/lowcode/projects/{id}/validate` | 校验项目 |
| GET | `/tool/lowcode/projects/{id}/diff` | 差异对比 |
| POST | `/tool/lowcode/projects/{id}/publish` | 发布项目 |
| POST | `/tool/lowcode/projects/{id}/rollback/{versionId}` | 回滚版本 |
| GET | `/tool/lowcode/projects/{id}/database/inspect` | 数据库检查 |
| POST | `/tool/lowcode/projects/{id}/database/ddl-preview` | DDL 预览 |
| GET | `/tool/lowcode/projects/{id}/export` | 导出（ZIP） |
| GET | `/tool/lowcode/projects/resource-allowlist` | 资源白名单 |

### 8.2 运行时 CRUD（LowCodeRuntimeController）

基础路径：`/lowcode/runtime/{appCode}`

| 方法 | 路径 | 说明 |
|---|---|---|
| GET | `/lowcode/runtime/{appCode}/schema` | 应用 Schema |
| GET | `/lowcode/runtime/{appCode}/records` | 记录列表 |
| GET | `/lowcode/runtime/{appCode}/records/{id}` | 单条记录 |
| POST | `/lowcode/runtime/{appCode}/records` | 创建记录 |
| PUT | `/lowcode/runtime/{appCode}/records/{id}` | 更新记录 |
| DELETE | `/lowcode/runtime/{appCode}/records/{id}` | 删除记录 |
| POST | `/lowcode/runtime/{appCode}/actions/{actionCode}` | 自定义动作 |

### 8.3 连接器（LowCodeConnectorController）

基础路径：`/tool/lowcode/connectors`

| 方法 | 路径 | 说明 |
|---|---|---|
| GET | `/tool/lowcode/connectors` | 连接器列表 |
| PUT | `/tool/lowcode/connectors` | 保存连接器配置 |
| POST | `/tool/lowcode/connectors/{code}/test` | 测试连接器 |

---

## 9. WebSocket 实时协议

### 9.1 连接方式

- **端点**：`ws://{host}/ws/sensor` 或 `ws://{host}/ws/monitoring`
- **认证**：一次性票据，通过 `?ticket=<token>` 传递（票据由 `POST /sensor/ws-ticket` 签发）
- **格式**：文本帧，JSON 序列化的 `SensorWebSocketMessageVo`

### 9.2 客户端 → 服务端消息

| type | 说明 | 字段 |
|---|---|---|
| `subscribe` | 订阅频道 | `channel`（字符串） |
| `unsubscribe` | 取消订阅 | `channel`（字符串） |
| `ping` | 心跳 | 无 |

### 9.3 订阅频道

| 频道 | 所需权限 | 说明 |
|---|---|---|
| `overview` | `sensor:monitoring:view` 等 | 监控概览 + 增量数据推送 |
| `monitoring` | 同上 | 监控事件 |
| `device:{deviceCode}` | 同上 | 单设备更新 |
| `point:{pointId}` | 同上 | 单测点更新 |
| `phm_alarm` | `phm:alarm:list` 等 | PHM 告警事件 |

### 9.4 服务端 → 客户端推送消息

| type | event | 说明 |
|---|---|---|
| `realtime` | `feature` | 实时振动/温度特征数据 |
| `diagnosis` / `analysis` | `analysis` | 诊断推理结果 |
| `phm_alarm` | `created` | 新 PHM 告警 |
| `phm_alarm` | `changed` | 告警状态变更 |
| `overview` | `full` | 完整监控快照 |
| `overview` | `new_vibration` / `new_temperature` | 增量数据更新 |

### 9.5 消息体字段（SensorWebSocketMessageVo）

```json
{
  "type": "realtime",
  "event": "feature",
  "deviceCode": "DEV-001",
  "pointId": 1,
  "channelId": 1,
  "metricCode": "rms",
  "quality": 0.95,
  "confidence": 0.87,
  "healthIndex": 0.92,
  "riskLevel": "low",
  "vibrationValue": 2.35,
  "temperatureValue": 45.2,
  "rms": 2.35,
  "peak": 5.12,
  "sampleTime": "2026-08-20T10:30:00Z",
  "waveform": [0.1, 0.2, ...],
  "frequencyAxis": [0.0, 12.5, ...],
  "spectrum": [0.05, 0.12, ...],
  "message": "..."
}
```

---

## 10. Python 推理服务内部接口

服务地址：`127.0.0.1:5000`（dev）/ `5001`（prod 齿轮箱）/ `5002`（prod 轴承）
认证：`X-Internal-Token` 头（≥ 32 字节，环境变量 `INFERENCE_INTERNAL_TOKEN`）

| 方法 | 路径 | 说明 |
|---|---|---|
| POST | `/internal/infer` | 单文件推理 |
| POST | `/internal/infer/batch` | 批量推理（最多 8 项） |
| POST | `/internal/preview` | 信号预览（不加载模型） |
| GET | `/internal/health/live` | 存活探针 |
| GET | `/internal/health/ready` | 就绪探针（含模型状态） |
| GET | `/internal/metrics` | Prometheus 指标 |

### 推理请求格式（`/internal/infer`）

```json
{
  "modelType": "gear",
  "modelVersion": "v1.0",
  "deviceCode": "DEV-001",
  "channelId": 1,
  "pointId": 1,
  "rawSignal": [0.1, 0.2, ...],
  "sampleRate": 51200,
  "filename": "data.mat"
}
```

### 推理响应格式

```json
{
  "status": "success",
  "modelType": "gear",
  "modelVersion": "v1.0",
  "confidence": 0.87,
  "healthIndex": 0.92,
  "riskLevel": "low",
  "diagnosis": "正常",
  "evidence": [{"feature": "rms", "value": 2.35}],
  "latencyMs": 125
}
```

---

## 11. MAT V2 TCP 协议

端口：8888（`sensor.mat-receiver.port`）
绑定：`0.0.0.0`

### 通信流程

```
客户端                    服务端
  │                         │
  ├── CWRU_MAT_V2\n  ──────►│  1. 协议标识
  ├── 4B JSON头长度  ──────►│  2. 大端序
  ├── UTF-8 JSON 头  ──────►│  3. 文件元数据
  │                    ◄─────┤  READY\n
  ├── MAT 文件数据    ──────►│  5. 原始二进制
  │                    ◄─────┤  JSON 结果
```

### JSON 头字段

| 字段 | 类型 | 必填 | 说明 |
|---|---|---|---|
| `filename` | string | 是 | 安全文件名（仅 `.mat`） |
| `filesize` | long | 是 | 文件大小（1–128 MB） |
| `sha256` | string | 是 | 文件 SHA-256 哈希 |
| `deviceCode` | string | 是 | 设备编码 |
| `pointCode` | string | 是 | 测点编码 |
| `channelId` | int | 是 | 物理通道 ID |
| `acquisitionTime` | string | 是 | 采集时间（带时区 ISO-8601） |

### 响应状态

| 状态 | 说明 |
|---|---|
| `ACCEPTED` | 成功接收，返回 `ingestId`、`attachmentId`、`taskId` |
| `DUPLICATE` | 重复文件（相同设备/测点/SHA） |
| `QUARANTINED` | 设备/测点/通道匹配失败，文件进入隔离目录 |
| `ERROR` | 协议、大小、签名或校验失败 |

### Mock 测试工具

```bash
python ruoyi-sensor/mock/cwru_mat_sender.py \
    --file <xxx.mat> --once \
    --device-code DEV-001 --point-code CH1 --channel-id 1 \
    --host 127.0.0.1 --port 8888
```
