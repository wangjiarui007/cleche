# RuoYi-Vue PHM 工业设备健康管理平台

## 项目简介

本项目基于 **若依（RuoYi-Vue）前后端分离框架 3.9.2** 深度改造，面向 **工业设备健康管理（PHM）** 场景，提供设备/测点管理、振动与温度历史查询、齿轮/轴承智能诊断、告警事件报表、历史数据下载、IoTDB 时序存储与低代码工作台。下位机文件接入统一为 8888 端口的 CWRU_MAT_V2。

技术体系：**Spring Boot 3 + MyBatis-Plus + Spring WebSocket + Redis Stream + IoTDB + Vue 2 + FastAPI/PyTorch**。

系统以 **Java（Spring Boot）** 为统一边界：负责用户认证（Redis 会话 Cookie + CSRF）、权限、数据范围、MAT 文件接收、诊断编排与持久化；**Python（FastAPI）** 只做模型推理，仅对 Java 暴露内部接口，浏览器不可直接访问。

## 技术栈

### 后端

| 组件 | 版本 | 说明 |
|---|---|---|
| Java（JDK） | 25（编译目标 17） | CI 用 JDK 25 Temurin |
| Spring Boot | 3.5.16 | 应用框架 |
| MyBatis / MyBatis-Plus | 3.0.4 / 3.5.9 | ORM |
| Spring Security | — | 会话 Cookie + CSRF |
| Spring WebSocket | — | `/ws/sensor`、`/ws/monitoring` 实时推送 |
| Netty | 4.1.137.Final | 依赖声明（MAT 接收器实际使用 `java.net.ServerSocket`） |
| IoTDB Session | 2.0.10 | 时序数据库客户端 |
| Redis Streams | — | 实时数据流、去重、DLQ |
| Flyway | — | Java 类迁移（37 个迁移文件） |
| Actuator | — | health / prometheus 端点（prod: 8081） |
| Lombok | 1.18.46 | 编译期代码生成 |
| JTransforms | 3.1 | FFT 频谱分析 |
| commons-math3 | 3.6.1 | 数学计算 |
| LZ4 | 1.8.0 | 数据压缩 |
| Jackson | 2.21.4 | JSON 序列化 |
| Embedded Tomcat | 10.1.57 | Web 容器 |

### 前端（Vue 2）

| 组件 | 版本 | 说明 |
|---|---|---|
| Vue | 2.7.16 | UI 框架 |
| Vue Router | 3.6.5 | 路由 |
| Vuex | 3.6.0 | 状态管理 |
| Element UI | 2.15.14 | UI 组件库 |
| ECharts | 6.1.0 | 图表可视化 |
| Three.js | 0.185.1 | 3D 渲染 |
| Axios | 1.18.1 | HTTP 客户端 |
| Vue CLI | 5.0.9（Webpack 5） | 构建工具 |
| Playwright | ^1.61.0 | E2E 测试 |

### 推理服务（Python）

| 组件 | 版本 | 说明 |
|---|---|---|
| Python | 3.11（CI/生产） | 本地开发可用 3.14 |
| FastAPI | 0.141.1 | HTTP API 框架 |
| Uvicorn[standard] | 0.34.2 | ASGI 服务器 |
| PyTorch | 2.13.0 | 模型推理（GPU 可选） |
| NumPy | 2.2.5 | 数值计算 |
| SciPy | 1.15.2 | .mat 文件读取、信号处理 |
| pandas | 2.2.3 | 批量诊断数据处理 |
| prometheus-client | 0.22.1 | `/internal/metrics` 端点 |
| pytest | 9.1.1 | 单元测试 |

## 系统架构

```text
前端页面（Vue 2，端口 80）
   │
   ├── REST API（/dev-api 或 /prod-api 代理）──► Spring Boot（8080）
   ├── WebSocket（/ws/sensor、/ws/monitoring，一次性票据）──► 实时推送
   │
   ▼
Spring Boot 应用（ruoyi-admin，入口：RuoYiApplication）
   ├── ruoyi-common      公共工具、统一返回、异常、低代码 SPI
   ├── ruoyi-system      用户、角色、菜单、部门、字典
   ├── ruoyi-framework   安全/会话/CSRF/拦截器/线程池/配置
   ├── ruoyi-sensor      MAT TCP 接收、PHM 诊断、IoTDB、WebSocket
   ├── ruoyi-lowcode     低代码工作台（独立数据源）
   └── ruoyi-admin       启动入口、REST 聚合、Flyway 迁移
   │
   ├── MySQL（ry-yue，主库）＋ 低代码独立库（ry-lowcode）
   ├── Redis（会话、验证码、限流、配置缓存）
   ├── IoTDB（时序投影，端口 6667）
   ├── 附件目录 / 模型目录（.local-models，SHA-256 校验）
   ├── FastAPI 统一推理服务（dev: 127.0.0.1:5000，/internal/*）
   ├── 生产齿轮推理 worker（127.0.0.1:5001）
   └── 生产轴承推理 worker（127.0.0.1:5002）
```

### 模块依赖链

```
ruoyi-common → ruoyi-system → ruoyi-framework → ruoyi-admin
                                         ↑              ↑
                               ruoyi-sensor ────────────┘
                               ruoyi-lowcode ───────────┘
```

### 关键设计

- **统一边界**：Java 是唯一对外认证与数据边界，Python 只做推理
- **异步管道**：遥测/帧消息先入 Redis Stream，消费者落库、写 IoTDB、推送 WS，失败进 DLQ
- **双写一致**：诊断记录 MySQL 为准，IoTDB 为投影，outbox + 租约同步，读时可回退
- **安全默认**：会话 Cookie、CSRF、数据范围、附件所有权、低代码白名单、模型哈希
- **实时可靠性**：采集持久化链路与诊断链路隔离；Redis AOF、ACK、`XPENDING/XCLAIM`、截止时间和有限重试保证实时新鲜度

## 核心功能模块

### 1. 基础后台管理（RuoYi 原生能力）
- 用户、角色、菜单、部门、字典、参数等统一管理
- 登录认证（会话 Cookie + CSRF 双提交）、权限校验、动态菜单
- 操作日志、登录日志、在线用户、低代码工作台

### 2. 工业监测与采集
- 设备、测点、振动数据、温度数据管理
- Spring 内置 MAT 文件接收（8888/CWRU_MAT_V2），文件校验、隔离、台账和幂等
- WebSocket（`/ws/sensor`、`/ws/monitoring`）一次性票据握手与实时推送
- 八通道实时监控页面、实时监测工作台（资产树、KPI 条、趋势图）

### 3. PHM 健康管理与智能诊断
- 设备集群、健康总览、设备大脑（`/phm/*`）
- 告警规则、告警处理、设备事件、实时/历史报表与 CSV 导出
- 齿轮、轴承模型推理（FastAPI + PyTorch，模型清单 + SHA-256 校验）
- 单点诊断、批量诊断、诊断任务历史、历史数据下载
- 自动 MAT 诊断任务：按测点唯一主模型绑定，异步推理、结果持久化和 WebSocket 联动
- MySQL→IoTDB 诊断结果同步（outbox + 重试 + 租约），IoTDB 主读、MySQL 回退

### 4. 时序存储（IoTDB Table 模型）
- `telemetry_metric`、`vibration_frame`、`diagnosis_result` 三张时序表（默认 TTL 1095/90/3650 天）
- `sensor.store-type` 支持 `iotdb` / `noop` 切换，带健康指示器与降级语义

### 5. 附件安全存储
- `/attachments` 独立 CRUD：随机存储键、所有者鉴权、类型/大小限制
- 可选 Windows Defender 病毒扫描（`MpCmdRun.exe`）
- 主动内容（html/htm/svg/swf）已移出上传白名单

### 6. 低代码工作台（V2）
- 独立数据库 schema（`LOWCODE_DB_URL`），与主库最小权限隔离
- 版本化元数据（草稿/校验/发布/回滚）、数据库预览与 DDL 预览
- 业务资源白名单（`lc_resource_allowlist`）、服务端表策略
- 连接器出站强制代理 + 地址/路径校验，SSRF 防护
- 运行时写操作默认关闭（`LOWCODE_RUNTIME_WRITE_ENABLED=false`）

### 7. 安全加固
- 认证由 JWT 迁移为 Redis 不透明会话 Cookie + CSRF 双提交
- 首次登录强制改密门禁（428），服务端密码策略 12–64 位
- 上传内容按类型与大小校验并隔离存储
- NumPy 推理加载 `allow_pickle=False`，拒绝恶意 object-array
- PyTorch `weights_only=True`；移除全信任 TLS 工具
- 生产独立管理端口（127.0.0.1:8081）仅暴露 health/prometheus

## 主要模块说明

### `ruoyi-admin`
项目启动入口（`RuoYiApplication`）、Web 接口聚合层、Flyway 迁移（37 个 Java 类迁移，`src/main/java/db/migration`）。prod 校验器 `ProductionConfigurationValidator` 强制检查所有秘密和路径配置。

### `ruoyi-framework`
安全认证与会话（`SecurityConfig`）、CSRF 过滤器、改密门禁、线程池配置（核心 50/最大 200）、Redis 配置、数据源配置。

### `ruoyi-system`
用户、角色、部门、菜单、字典、系统配置等基础系统管理服务。

### `ruoyi-common`
通用工具、统一响应、异常体系、低代码 SPI（`LowCodeActionHandler` 等）、注解（`@RateLimiter`、`@RepeatSubmit`、`@Log`）。

### `ruoyi-lowcode`
低代码工作台（项目/版本/连接器/资源白名单，独立数据源），含运行时 CRUD、管道执行、规则引擎。

### `ruoyi-sensor`
工业业务核心（30 个 Service、13 个 Controller、23 个 Entity）：
- **MAT TCP 接入**：`MatFileReceiverService`（纯 `java.net.ServerSocket`，端口 8888，非 Netty）
- **PHM 健康管理**：设备/测点/告警/事件/报表全生命周期
- **诊断编排**：单点/批量诊断、模型发布与影子验证、自动 MAT 任务
- **振动分析**：时域/频域分析、批量分析、波形预览
- **IoTDB 时序存储**：`IoTdbTimeSeriesStore`、时序分析、帧编解码
- **WebSocket 推送**：`SensorWebSocketHandler`（票据认证、5 种订阅频道）
- **附件安全存储**：`PhmAttachmentStorageService`（病毒扫描、SHA-256 校验）

### `ruoyi-sensor/mock`
MAT V2 Python 测试发送器（`cwru_mat_sender.py`），负责生成/发送带完整协议头的 `.mat` 文件。

### `ruoyi-sensor/inference`
独立 FastAPI/PyTorch 推理服务（`inference_service.py`，1452 行）：
- 齿轮模型（WDCNNMechDG，~7.25 MB）+ 轴承模型（ResNet1D-18，~14.74 MB）
- `/internal/infer`、`/internal/infer/batch`、`/internal/preview` 内部接口
- 模型清单与 SHA-256 校验（`models-manifest.json`）
- GPU 可选：`torch.cuda.is_available()` 自动检测，无 GPU 回退 CPU
- 本地开发统一运行在 5000，prod 拆分 5001/5002

## 端口分配

| 服务 | 端口 | 绑定地址 | 说明 |
|---|---|---|---|
| Java HTTP (Tomcat) | 8080 | 0.0.0.0 | 主 API 服务 |
| Actuator / Prometheus | 8081 | 127.0.0.1 | 仅本机访问（prod） |
| MAT V2 TCP 接收 | 8888 | 0.0.0.0 | 设备数据采集 |
| Python 推理 (dev) | 5000 | 127.0.0.1 | 齿轮+轴承合一 |
| Python 推理 (prod 齿轮箱) | 5001 | 127.0.0.1 | 独立进程 |
| Python 推理 (prod 轴承) | 5002 | 127.0.0.1 | 独立进程 |
| MySQL | 3306 | — | 外部服务 |
| Redis | 6379 | — | 外部服务 |
| IoTDB | 6667 | — | 外部集群 |
| Vue dev server | 80 | 0.0.0.0 | 开发模式 |

## 硬件资源配置

| 配置级别 | CPU | 内存 | GPU | 磁盘 |
|---|---|---|---|---|
| **最低** | 4 核 | 4 GB | 无（CPU 推理） | 50 GB |
| **推荐** | 8 核+ | 8–16 GB | 可选（≥ 2 GB 显存） | 200 GB+（含 IoTDB） |
| **高吞吐** | 16 核+ | 16–32 GB | 推荐（≥ 4 GB 显存） | 500 GB+ |

> GPU 可选：推理服务自动检测 CUDA，无 GPU 时回退 CPU。模型同时加载约 200–500 MB 显存。IoTDB 时序数据推荐 SSD，日志和附件可使用 HDD。

## 快速启动

### 前置依赖
- JDK 25+（编译目标 17）、Maven 3.8+、Node.js 24+、npm 11+
- Python 3.11 + `pip install -r ruoyi-sensor/inference/requirements.txt`
- MySQL 5.7+/8.x（创建 `ry-yue` 和 `ry-lowcode` 两个 schema）
- Redis ≥ 5.0（需 Redis Streams）
- Apache IoTDB（推荐 3 节点集群）
- 模型文件放于 `ruoyi-sensor/inference/get/`（齿轮 `best_model_classwise_maha.pth`、轴承 `best_model.pth`，SHA 与 `models-manifest.json` 一致）

### 首次准备
1. 复制 `.env.example` 为 `.env` 并填写密码、令牌等变量；
2. 安装 Python 依赖：

```bash
pip install -r ruoyi-sensor/inference/requirements.txt
```

3. 安装前端依赖：

```bash
cd ruoyi-ui && npm ci
```

### 启动步骤

```bash
# 1. 构建后端
mvn clean test package

# 2. 启动后端（dev profile）
java -Xms256m -Xmx1024m -jar ruoyi-admin/target/ruoyi-admin.jar --spring.profiles.active=dev

# 3. 启动推理服务
cd ruoyi-sensor/inference && python inference_service.py

# 4. 启动前端
cd ruoyi-ui && npm run dev
```

Windows 可使用 `bin/run.bat` 启动后端（自动设环境变量），`ruoyi-ui/bin/run-web.bat` 启动前端。

访问 `http://localhost:80`（测点总览：`/analysis-toolkit/bearing-diagnosis`）。

## 本地目录约定

```text
RuoYi-Vue-master
├── ruoyi-admin          启动入口、REST 聚合、Flyway 迁移
├── ruoyi-common         公共工具、统一返回、异常
├── ruoyi-framework      安全/会话/CSRF/配置
├── ruoyi-system         用户、角色、菜单、字典
├── ruoyi-lowcode        低代码工作台（独立数据源）
├── ruoyi-sensor         PHM 业务核心
│   ├── sensor/mock          MAT V2 测试发送器
│   └── sensor/inference     FastAPI 推理服务
├── ruoyi-ui             前端（Vue 2）
├── docs                 项目总览与说明文档
├── sql                  历史 SQL（空库安装/溯源，非生产迁移）
├── config               SpotBugs 排除等构建配置
├── bin                  Windows 启动脚本
├── .local-models        模型制品（不提交）
├── .local-data          附件、上传、日志（不提交）
├── pom.xml              根 Maven reactor
└── .env.example         环境变量模板
```

## 常用验证命令

```bash
# Java 全量构建
mvn clean test package

# Java 单模块测试（必须带 -am）
mvn -pl ruoyi-sensor -am test \
    -Dtest=MatFileProtocolHeaderTest \
    -Dsurefire.failIfNoSpecifiedTests=false

# Java 质量门禁（JaCoCo + SpotBugs）
mvn -DskipTests verify -Psecurity-gates

# 前端
cd ruoyi-ui && npm run build:prod
cd ruoyi-ui && npm run check:bundle
cd ruoyi-ui && npm run test:e2e

# Python 推理
cd ruoyi-sensor/inference && python -m pytest

# Mock 数据采集
python ruoyi-sensor/mock/cwru_mat_sender.py \
    --file <xxx.mat> --once --device-code DEV-001 --point-code CH1 --channel-id 1
```

## 生产部署要点

- 生产经 Nginx（HTTPS）对外，Java/Python 仅绑定内部地址；
- 生产推理拆分为齿轮（:5001）与轴承（:5002）两个独立进程；Java 不因单个推理 worker 不可用而停止采集和存储；
- Redis 实时任务流启用 AOF（`everysec`），任务以 10 秒截止时间和最多两次尝试保障新鲜度，过期任务不补发陈旧告警；
- 8888 仅允许可信采集内网，5001/5002、Java 管理端口、Redis、MySQL、IoTDB 不对用户开放；
- 使用 `.env` 注入真实秘密（`MYSQL_PASSWORD`、`SENSOR_INFERENCE_INTERNAL_TOKEN` 等），严禁沿用开发默认值；
- 低代码生产必须配置独立 `LOWCODE_DB_*` 账号与出站代理，写默认关闭；
- 无 Docker/Dockerfile/docker-compose，部署为裸 JVM 进程模式；前端 `dist/` 产物需外部 Web 服务器托管；
- MAT 接入部署、迁移、接口和验收矩阵见 `AI_PROJECT_OVERVIEW.md`；
- 接口文档见 `API_OVERVIEW.md`；
- 硬件与环境需求详见 `PHM_PLATFORM_REQUIREMENTS.md`。

## 适用场景

- 工业设备健康管理与状态监测
- 振动/温度实时监测与告警
- 齿轮、轴承智能诊断与历史追溯
- 设备状态可视化大屏、测点总览
- 校园/实验室/科研设备监控（毕业设计、课程设计、团队协作）
