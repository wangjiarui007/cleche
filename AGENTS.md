# AGENTS.md

RuoYi-Vue PHM 工业设备健康监测平台：Java 25(编译目标 17) + Spring Boot 3.5.16 + MyBatis-Plus，Vue 2.7 前端，Python 3.11 FastAPI/PyTorch 推理服务。核心业务在 ruoyi-sensor（MAT V2 TCP 接入、PHM 诊断、IoTDB、WebSocket）。总览见 `docs/README.md`、`docs/AI_PROJECT_OVERVIEW.md`。

## 工具链与本机环境（WSL2，无 sudo）
- 构建需 JDK 17+、Maven 3.8+。本机工具装在用户目录：
  - JDK 21 Temurin：`~/.local/opt/java-current`（软链）
  - Maven 3.9.16：`~/.local/opt/apache-maven-3.9.16`
  - 命令在 `~/.local/bin`，但当前会话需手动 `export JAVA_HOME=~/.local/opt/java-current`
- Python 3.14 无 pip/venv 引导（ensurepip 缺失），依赖须用 `--user --break-system-packages` 安装；本机为 numpy 2.5.2 / scipy 1.18.1 / torch 2.13.0+cpu。CI 与生产按 Python 3.11 + `requirements.txt` 锁定版本（numpy 2.2.5 / scipy 1.15.2）。

## 构建与测试（命令顺序有讲究）
- 全量：`mvn clean test package`（CI 主流程）。后端包产物：`ruoyi-admin/target/ruoyi-admin.jar`。
- 单模块测试必须带 `-am`（模块 jar 未 install 进本地 .m2）：`mvn -pl ruoyi-sensor -am test -Dtest=MatFileProtocolHeaderTest -Dsurefire.failIfNoSpecifiedTests=false`
  - `-Dsurefire.failIfNoSpecifiedTests=false` 必带，否则 `-am` 构建的依赖模块因无匹配测试直接失败。
- 质量门禁：`mvn -DskipTests verify -Psecurity-gates`（JaCoCo + SpotBugs，effort=Max；黑名单见 `config/spotbugs-exclude.xml`）。
- 容器集成测试是 CI 必跑项（本地需 Docker），CI 显式拒绝跳过：`LowCodeV2MigrationTest`、`ProductionHardeningMigrationTest`、`RedisFrameStreamIntegrationTest`。
- 前端 Node 24：`npm run dev`（端口 80，代理→8080）、`npm run build:prod`、`npm run check:bundle`、`npm run test:e2e`（Playwright 自动起 9528，含 4 浏览器）。
- 推理：`cd ruoyi-sensor/inference && python -m pytest`。

## 架构注意点（易踩坑）
- 多模块边界：`ruoyi-common` / `ruoyi-system` / `ruoyi-framework` / `ruoyi-lowcode` / `ruoyi-sensor` / `ruoyi-admin`。依赖链 `common → system → framework → admin`，`sensor` 和 `lowcode` 从 `common` 分支。理解此链对 `-am` 构建行为至关重要。
- MAT V2 接收器是 `MatFileReceiverService`（`ruoyi-sensor`）：**纯 `java.net.ServerSocket`，不是 Netty**（pom 带 Netty 依赖但 unused），端口 8888，`sensor.mat-receiver.*`。docs 里"Netty 接收"是过时描述。
- **类型坑（已修但须警惕）**：`PhmDiagnosisBindingEntity.channelId` 为 `Long`；`MatFileProtocolHeader.channelId`、`PhmMeasurePointEntity.channelId` 为 `Integer`。比较必须 `Long.valueOf(integer).equals(binding.getChannelId())`，直接 `.equals` 恒 false。新增比较逻辑时务必注意此差异。
- Flyway 迁移是 **Java 类**而非 SQL：`ruoyi-admin/src/main/java/db/migration/`（37 个迁移文件）；迁移测试在 `ruoyi-admin/src/test/java/db/migration/`。prod 配置 `baseline-version: 2026041700`，`clean-disabled: true`。
- 附件根：`sensor.attachment.root` 默认 `./.local-data/attachments`（相对 JVM 工作目录，须从项目根启动），对象文件在 `objects/` 子目录下；`.local-data/`、`.local-models/` 已 gitignore。Python 推理服务的 `ALLOWED_INPUT_ROOTS` 自动对齐此路径。
- 管理端口：prod 下 `management.server.port: 8081`，仅暴露 health + prometheus。

## Python 推理服务（ruoyi-sensor/inference/）
- 必须在 inference 目录下运行（imports `models.*`、`utils_signal`，并动态加载 `04.4_diagnose_unlabeled_target.py`）。
- 只暴露 `/internal/infer`、`/internal/infer/batch`、`/internal/preview` 与 health/metrics，统一 `X-Internal-Token`（`INFERENCE_INTERNAL_TOKEN`）鉴权。
- Matlab 路径白名单 `ALLOWED_INPUT_ROOTS`：缺省自动含 `get/got` 与 `<项目根>/.local-data/attachments/objects`（与 Java 默认附件根自愈对齐）；生产必须让 `INFERENCE_ALLOWED_INPUT_ROOTS` 指向 `SENSOR_ATTACHMENT_ROOT/objects`，改动附件根时两处同步。
- dev 两个模型 URL 均指向 `127.0.0.1:5000`，prod 拆分 5001/5002；`sensor.startup.inference.enabled: false`，推理服务需手工启动（`python inference_service.py`）。

## 数据接入验证
- Mock TCP 发送端：`python ruoyi-sensor/mock/cwru_mat_sender.py --file <xxx.mat> --once --device-code DEV-001 --point-code CH1 --channel-id 1`

## CI 注意事项
- `build` job 跑在 `windows-latest`，`java-security-gates` 和 `testcontainers` 跑在 `ubuntu-latest`。
- Gitleaks `secret-scan` 也在 CI 中，别把密钥提交进仓库。

## 文档可信度
- docs 总览可靠，但含陈旧内容：文档引用的 `start-all.ps1` 在仓库已被删除；Netty 描述过时；docs/README.md 的 FastAPI/PyTorch/pytest 版本与 requirements.txt 不一致。以代码/配置为准。