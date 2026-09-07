# RuoYi-Vue PHM 平台 — 系统配置与硬件资源需求分析

> 基于代码库静态分析生成，数据来源：`pom.xml`、`application*.yml`、`requirements.txt`、`inference_service.py`、`logback.xml`、`ThreadPoolConfig.java` 等。

---

## 1. 基础运行环境 (Runtime Environment)

### 1.1 编程语言版本

| 层 | 语言 | 版本要求 | 说明 |
|---|---|---|---|
| 后端 | Java | **JDK 25**（编译目标 17） | CI 用 JDK 25 Temurin，`pom.xml:20` 设 `java.version=17` |
| 推理服务 | Python | **3.11**（CI/生产）/ 3.14（本地开发） | `requirements.txt` 针对 3.11 锁定；本机 3.14 亦可运行 |
| 前端 | Node.js | **≥ 24.0** | `ruoyi-ui/package.json:72` |
| 前端 | npm | **≥ 11** | `ruoyi-ui/package.json:73` |

### 1.2 核心依赖库

#### Java 后端（`pom.xml`）

| 库 | 版本 | 用途 |
|---|---|---|
| Spring Boot | 3.5.16 | 应用框架 |
| MyBatis-Plus | 3.5.9 | ORM |
| Netty | 4.1.137.Final | 声明但 MAT 接收器未使用 |
| IoTDB Session | 2.0.10 | 时序数据库客户端 |
| Jackson | 2.21.4 | JSON 序列化 |
| Embedded Tomcat | 10.1.57 | Web 容器 |
| libthrift | 0.24.0 | IoTDB RPC 通信 |
| Flyway | （Spring Boot 管理） | 数据库迁移（Java 类，非 SQL） |
| Micrometer + Prometheus | （Spring Boot 管理） | 监控指标导出 |

#### Python 推理服务（`ruoyi-sensor/inference/requirements.txt`）

| 库 | 版本 | 用途 |
|---|---|---|
| PyTorch | 2.13.0 | 模型推理（GPU 可选） |
| FastAPI | 0.141.1 | HTTP API 框架 |
| uvicorn[standard] | 0.34.2 | ASGI 服务器 |
| numpy | 2.2.5 | 数值计算 |
| scipy | 1.15.2 | .mat 文件读取、信号处理 |
| pandas | 2.2.3 | 批量诊断数据处理 |
| prometheus-client | 0.22.1 | `/internal/metrics` 端点 |
| tqdm | 4.67.1 | 进度条（批量诊断） |

#### 前端（`ruoyi-ui/package.json`）

| 库 | 版本 | 用途 |
|---|---|---|
| Vue | 2.7.16 | UI 框架 |
| Element UI | 2.15.14 | 组件库 |
| ECharts | 6.1.0 | 图表 |
| Three.js | 0.185.1 | 3D 渲染 |
| Playwright | ^1.61.0 | E2E 测试 |

### 1.3 数据库与中间件

| 组件 | 版本要求 | 用途 | 配置来源 |
|---|---|---|---|
| **MySQL** | 5.7+ / 8.x | 主业务库（两套 schema） | `application.yml:127-137` |
| **Redis** | **≥ 5.0**（需 Redis Streams） | Session、验证码、限流、缓存 | `application.yml:139-150` |
| **Apache IoTDB** | 1.x / 3.x（Session API 2.0.10） | 时序数据存储（振动波形、诊断结果） | `application.yml:98-122` |

> **MySQL 双 schema 架构**：主库 `ry-yue`（连接池 max=20）+ 低代码独立库 `ry-lowcode`（连接池 max=10），prod 校验器强制要求两库分离。

**无 Kafka、RabbitMQ 或其他消息中间件。**

---

## 2. 硬件资源配置建议 (Hardware Resource Requirements)

### 2.1 CPU 与内存

基于代码中的线程池、连接池和 Tomcat 配置：

| 配置项 | 值 | 来源 |
|---|---|---|
| Tomcat max 线程 | **800** | `application.yml:196` |
| Tomcat min-spare | 100 | `application.yml:198` |
| Tomcat accept-count | 1000 | `application.yml:193` |
| 框架线程池 core/max | 50 / **200** | `ThreadPoolConfig.java` |
| 框架调度线程池 | 50（daemon） | `ThreadPoolConfig.java:52` |
| 诊断线程池 | core=4, max=4, queue=100 | `application.yml:66-68` |
| 振动线程池 | core=4, max=16, queue=500 | `VibrationAsyncConfig.java` |
| MAT 接收线程 | 4 worker, max 8 连接 | `application.yml:89-90` |

| 配置级别 | CPU | 内存 | 说明 |
|---|---|---|---|
| **最低配置** | 4 核 | **4 GB** | JVM 堆 1 GB（`bin/run.bat:11`），Tomcat 线程降配，单节点 IoTDB，dev 模式 |
| **推荐配置** | **8 核+** | **8–16 GB** | JVM 堆 4–8 GB，全部线程池满配，3 节点 IoTDB 集群 |
| **高吞吐配置** | 16 核+ | 16–32 GB | 500+ msg/s 持续采集，JVM 堆 8–16 GB |

> JVM 堆未在配置文件中硬编码。`bin/run.bat` 中的本地开发参数为 `-Xms256m -Xmx1024m -XX:MetaspaceSize=128m -XX:MaxMetaspaceSize=512m`。生产部署需通过 `JAVA_OPTS` 或容器参数显式设置。

### 2.2 GPU 算力需求

**GPU 是可选的，不是必须的。**

```python
# inference_service.py:294
DEVICE = torch.device("cuda" if torch.cuda.is_available() else "cpu")
```

| 场景 | GPU 需求 | 说明 |
|---|---|---|
| **推理部署** | **可选**（CPU 可运行） | 自动检测 CUDA，无 GPU 时回退 CPU；本地开发用 `torch+cpu` |
| **模型训练** | 推荐 NVIDIA GPU | 训练脚本 `04.4_diagnose_unlabeled_target.py` 支持 CUDA，但不在本平台运行 |

**模型规模（推理侧）：**

| 模型 | 架构 | 文件大小 | 参数量 | 说明 |
|---|---|---|---|---|
| 齿轮箱 (Gear) | WDCNNMechDG（多分支 + GRL） | ~7.25 MB | 中等 | 含 FFT、Hilbert 变换、域分类器 |
| 轴承 (Bearing) | ResNet1D-18 | ~14.74 MB | ~11.2M | 标准 1D ResNet |

两个模型同时加载约需 **200–500 MB GPU 显存**（视 batch size）。CPU 推理时占用约 **1–2 GB 内存**。

若使用 GPU：
- 最低：NVIDIA GPU，≥ 2 GB 显存，CUDA ≥ 11.8
- 推荐：NVIDIA GPU，≥ 4 GB 显存（支持更大 batch size 加速并发推理）

### 2.3 磁盘存储与 I/O

| 用途 | 空间需求 | 说明 |
|---|---|---|
| **应用日志** | **~40 GB 上限** | 3 个滚动日志文件各 100MB/个、60 天保留、总上限 info 20G + error 10G + user 10G（`logback.xml`） |
| **附件存储** | 视业务量 | `sensor.attachment.root`，单文件最大 128 MB（诊断输入）、20 MB（报告） |
| **MAT 接收临时** | ≤ 128 MB/文件 | `sensor.mat-receiver.root`，处理后归档至附件 |
| **IoTDB 时序数据** | **100 GB+**（推荐） | 遥测数据保留 3 年、振动帧 90 天、诊断结果 10 年；振动帧为 BLOB（波形+频谱） |
| **模型文件** | ~22 MB | 两个 `.pth` 文件 |
| **上传文件** | 视业务量 | `ruoyi.profile`，最大 32 MB/请求 |

> I/O 要求：IoTDB 写入密集（持续采集振动数据），推荐 SSD。日志和附件可使用 HDD。

---

## 3. 环境部署与运行验证 (Deployment & Verification)

### 3.1 项目中的配置文件

| 文件 | 用途 |
|---|---|
| `pom.xml`（根 + 各模块） | Java 构建配置、依赖版本、Maven profiles |
| `ruoyi-admin/src/main/resources/application.yml` | 默认配置（含 dev 默认值） |
| `ruoyi-admin/src/main/resources/application-dev.yml` | 开发环境覆盖 |
| `ruoyi-admin/src/main/resources/application-prod.yml` | 生产环境（全环境变量驱动） |
| `.env.example`（94 行） | 所有环境变量模板 |
| `ruoyi-sensor/inference/requirements.txt` | Python 依赖锁定 |
| `ruoyi-sensor/inference/models-manifest.json` | 模型 SHA256 校验清单 |
| `ruoyi-ui/package.json` | 前端依赖与脚本 |
| `ruoyi-ui/vue.config.js` | 前端 dev server 代理、构建配置 |
| `ruoyi-ui/playwright.config.js` | E2E 测试配置 |
| `config/spotbugs-exclude.xml` | SpotBugs 排除规则 |
| `.github/workflows/production-ci.yml` | CI 流水线 |
| `sql/`（17 个文件） | 数据库基线与升级脚本（开发用） |
| `ruoyi-admin/src/main/java/db/migration/`（37 个 Java 类） | Flyway 生产迁移 |

### 3.2 启动运行步骤

#### 开发环境一键启动（Windows）

```batch
# 1. 编译打包
bin\package.bat          # 等价于 mvn clean package -Dmaven.test.skip=true

# 2. 启动后端（自动设环境变量，--spring.profiles.active=dev）
bin\run.bat              # JVM: -Xms256m -Xmx1024m

# 3. 启动前端
cd ruoyi-ui && npm run dev   # 端口 80，代理 → localhost:8080

# 4. 手动启动推理服务
cd ruoyi-sensor/inference && python inference_service.py  # 端口 5000
```

#### Linux / 生产部署

```bash
# 1. 前置依赖
#    - JDK 25+ (compile target 17)
#    - Maven 3.8+
#    - MySQL 5.7+ / 8.x（创建 ry-yue 和 ry-lowcode 两个 schema）
#    - Redis ≥ 5.0
#    - IoTDB 1.x/3.x（推荐 3 节点集群）
#    - Python 3.11 + pip install -r ruoyi-sensor/inference/requirements.txt

# 2. 配置环境变量（参考 .env.example，至少需要以下项）
#    MYSQL_URL, MYSQL_USERNAME, MYSQL_PASSWORD
#    REDIS_HOST, REDIS_PASSWORD
#    IOTDB_NODE_URLS, IOTDB_USERNAME, IOTDB_PASSWORD
#    LOWCODE_DB_URL, LOWCODE_DB_USERNAME, LOWCODE_DB_PASSWORD
#    SENSOR_ATTACHMENT_ROOT（绝对路径）
#    RUOYI_PROFILE（绝对路径）
#    LOG_PATH（绝对路径）
#    SENSOR_INFERENCE_INTERNAL_TOKEN（≥ 32 字节）
#    CORS_ALLOWED_ORIGINS（不得含 localhost）
#    SENSOR_WS_ALLOWED_ORIGINS（不得含 localhost）

# 3. 构建
export JAVA_HOME=~/.local/opt/java-current  # 或系统 JDK 25
mvn clean test package

# 4. 启动后端
java -Xms2g -Xmx4g -XX:MetaspaceSize=256m -XX:MaxMetaspaceSize=512m \
     -jar ruoyi-admin/target/ruoyi-admin.jar

# 5. 启动推理服务（prod 需启动两个实例）
INFERENCE_INTERNAL_TOKEN=<token> \
python ruoyi-sensor/inference/inference_service.py  # 实例 1: 齿轮箱 :5001

INFERENCE_INTERNAL_TOKEN=<token> PORT=5002 \
python ruoyi-sensor/inference/inference_service.py  # 实例 2: 轴承 :5002

# 6. 前端构建（产出 dist/ 供 nginx 等静态服务）
cd ruoyi-ui && npm ci && npm run build:prod
```

### 3.3 端口汇总

| 服务 | 端口 | 绑定地址 | 说明 |
|---|---|---|---|
| Java HTTP (Tomcat) | **8080** | 0.0.0.0 | 主 API 服务 |
| Actuator / Prometheus | **8081** | 127.0.0.1 | 仅本机访问 |
| MAT V2 TCP 接收 | **8888** | 0.0.0.0 | 设备数据采集 |
| Python 推理 (dev) | **5000** | 127.0.0.1 | 齿轮箱+轴承合一 |
| Python 推理 (prod 齿轮箱) | **5001** | 127.0.0.1 | 独立进程 |
| Python 推理 (prod 轴承) | **5002** | 127.0.0.1 | 独立进程 |
| MySQL | **3306** | — | 外部服务 |
| Redis | **6379** | — | 外部服务 |
| IoTDB | **6667** | — | 外部集群 |
| Vue dev server | **80** | 0.0.0.0 | 开发模式 |

### 3.4 构建与验证命令

```bash
# 全量构建（CI 主流程）
mvn clean test package

# 单模块测试（必须带 -am 和 -Dsurefire.failIfNoSpecifiedTests=false）
mvn -pl ruoyi-sensor -am test \
    -Dtest=MatFileProtocolHeaderTest \
    -Dsurefire.failIfNoSpecifiedTests=false

# 质量门禁（JaCoCo + SpotBugs）
mvn -DskipTests verify -Psecurity-gates

# 容器集成测试（需 Docker）
mvn -pl ruoyi-admin -am test \
    -Dtest=LowCodeV2MigrationTest,ProductionHardeningMigrationTest \
    -Dsurefire.failIfNoSpecifiedTests=false

# 前端
cd ruoyi-ui && npm run dev          # 开发
cd ruoyi-ui && npm run build:prod   # 生产构建
cd ruoyi-ui && npm run test:e2e     # Playwright E2E

# Python 推理测试
cd ruoyi-sensor/inference && python -m pytest

# Mock 数据采集验证
python ruoyi-sensor/mock/cwru_mat_sender.py \
    --file <xxx.mat> --once --device-code DEV-001 --point-code CH1 --channel-id 1
```

### 3.5 生产环境启动校验

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

### 3.6 关键环境变量速查（`.env.example`）

```bash
# === 数据库 ===
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

# === 推理服务 ===
SENSOR_GEAR_INFER_URL=http://127.0.0.1:5001/internal/infer
SENSOR_BEARING_INFER_URL=http://127.0.0.1:5002/internal/infer
SENSOR_INFERENCE_INTERNAL_TOKEN=<min 32 bytes>

# === 文件路径（生产必须绝对路径）===
SENSOR_ATTACHMENT_ROOT=/data/phm/attachments
RUOYI_PROFILE=/data/phm/upload
LOG_PATH=/data/phm/logs

# === 安全 ===
CORS_ALLOWED_ORIGINS=https://your-domain.com
SENSOR_WS_ALLOWED_ORIGINS=https://your-domain.com
```

---

## 附录：不存在的组件

本仓库中 **不存在** 以下基础设施文件：Dockerfile、docker-compose.yml、Kubernetes manifests、Helm charts、nginx 配置、systemd 服务文件、Terraform/Ansible playbook。部署为裸 JVM 进程模式，前端 `dist/` 产物需外部 Web 服务器（如 nginx）托管。
