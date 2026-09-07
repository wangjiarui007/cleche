@echo off
setlocal
chcp 65001 >nul

set "PROJECT_ROOT=%~dp0.."
set "ADMIN_JAR=%PROJECT_ROOT%\ruoyi-admin\target\ruoyi-admin.jar"
set "SENSOR_ATTACHMENT_ROOT=%PROJECT_ROOT%\.local-data\attachments"
set "INFERENCE_ALLOWED_INPUT_ROOTS=%PROJECT_ROOT%\.local-data\attachments\objects"
set "RUOYI_PROFILE=%PROJECT_ROOT%\.local-data\uploadPath"
set "INFERENCE_MODEL_ROOT=%PROJECT_ROOT%\.local-models"
set "JAVA_OPTS=-Xms256m -Xmx1024m -XX:MetaspaceSize=128m -XX:MaxMetaspaceSize=512m"

if not exist "%ADMIN_JAR%" (
  echo [错误] 本地后端包不存在：%ADMIN_JAR%
  echo 请先在项目根目录运行 mvn clean install -DskipTests
  exit /b 1
)

cd /d "%PROJECT_ROOT%"
echo [信息] 正在从本地项目启动后端：%ADMIN_JAR%
java %JAVA_OPTS% -jar "%ADMIN_JAR%" --spring.profiles.active=dev
exit /b %ERRORLEVEL%
