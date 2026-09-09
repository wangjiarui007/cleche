package db.migration;

import static org.junit.jupiter.api.Assertions.assertEquals;
import static org.junit.jupiter.api.Assertions.assertTrue;

import java.nio.file.Files;
import java.nio.file.Path;
import java.sql.Connection;
import java.sql.DriverManager;
import java.sql.ResultSet;
import java.sql.Statement;
import org.flywaydb.core.Flyway;
import org.junit.jupiter.api.Test;
import org.testcontainers.containers.MySQLContainer;
import org.testcontainers.junit.jupiter.Container;
import org.testcontainers.junit.jupiter.Testcontainers;

@Testcontainers(disabledWithoutDocker = true)
class IndustrialPipelineMenuMigrationTest
{
    @Container
    static final MySQLContainer<?> MYSQL = new MySQLContainer<>("mysql:8.4")
        .withDatabaseName("ry_permissions").withUsername("test").withPassword("test");

    @Test
    void createsDeterministicRoutesWithoutChangingExistingRoleGrants() throws Exception
    {
        try (Connection connection = connection(); Statement statement = connection.createStatement())
        {
            statement.execute("""
                CREATE TABLE sys_menu(
                  menu_id BIGINT AUTO_INCREMENT PRIMARY KEY, menu_name VARCHAR(50), parent_id BIGINT,
                  order_num INT, path VARCHAR(200), component VARCHAR(255), query VARCHAR(255),
                  route_name VARCHAR(50), is_frame INT, is_cache INT, menu_type CHAR(1), visible CHAR(1),
                  status CHAR(1), perms VARCHAR(100), icon VARCHAR(100), create_by VARCHAR(64),
                  create_time DATETIME, update_by VARCHAR(64), update_time DATETIME, remark VARCHAR(500))
                """);
            statement.execute("CREATE TABLE sys_role_menu(role_id BIGINT,menu_id BIGINT,UNIQUE KEY(role_id,menu_id))");
            statement.execute("INSERT INTO sys_menu(menu_id,menu_name,parent_id,order_num,path,menu_type) VALUES(10,'系统工具',0,1,'tool','M'),(12,'监测与数据',0,4,'monitoring-center','M'),(13,'PHM中心',0,6,'phm','M')");
            statement.execute("INSERT INTO sys_menu(menu_id,menu_name,parent_id,order_num,path,component,route_name,menu_type,perms) VALUES(11,'低代码',10,1,'lowcode','tool/lowcode/index','LowCode','C','tool:lowcode:list')");
            statement.execute("INSERT INTO sys_role_menu VALUES(2,11)");
        }

        Flyway flyway = Flyway.configure()
            .dataSource(MYSQL.getJdbcUrl(), MYSQL.getUsername(), MYSQL.getPassword())
            .locations("classpath:db/migration-targeted")
            .failOnMissingLocations(false)
            .baselineOnMigrate(true).baselineVersion("2026081501")
            .javaMigrations(new V2026081502__IndustrialPipelineMenus()).load();
        assertEquals(1, flyway.migrate().migrationsExecuted);
        assertEquals(0, flyway.migrate().migrationsExecuted);

        try (Connection connection = connection(); Statement statement = connection.createStatement())
        {
            assertEquals(0, count(statement, "SELECT COUNT(*) FROM sys_menu WHERE parent_id=0 AND path='sensor-access' AND menu_type='M'"));
            assertEquals(1, count(statement, "SELECT COUNT(*) FROM sys_menu child JOIN sys_menu parent ON parent.menu_id=child.parent_id WHERE parent.path='phm' AND child.path='points' AND child.component='sensor/access/points' AND child.perms='sensor:channel:list'"));
            assertEquals(1, count(statement, "SELECT COUNT(*) FROM sys_menu child JOIN sys_menu parent ON parent.menu_id=child.parent_id WHERE parent.path='monitoring-center' AND child.path='files' AND child.component='sensor/ingest/files' AND child.perms='sensor:ingest:list'"));
            assertEquals(1, count(statement, "SELECT COUNT(*) FROM sys_menu WHERE perms='tool:lowcode:test'"));
            assertEquals(1, count(statement, "SELECT COUNT(*) FROM sys_menu WHERE perms='tool:lowcode:activate'"));
            assertEquals(0, count(statement, "SELECT COUNT(*) FROM sys_menu child LEFT JOIN sys_menu parent ON parent.menu_id=child.parent_id WHERE child.parent_id<>0 AND parent.menu_id IS NULL"));
            assertEquals(1, count(statement, "SELECT COUNT(*) FROM sys_role_menu WHERE role_id=2"));
            assertEquals(0, count(statement, "SELECT COUNT(*) FROM sys_role_menu rm LEFT JOIN sys_menu m ON m.menu_id=rm.menu_id WHERE m.menu_id IS NULL"));
            assertEquals(0, count(statement, "SELECT COUNT(*) FROM sys_menu WHERE path='sensor-analysis'"));
        }

        Path uiRoot = Path.of("ruoyi-ui", "src", "views");
        if (!Files.isDirectory(uiRoot)) uiRoot = Path.of("..", "ruoyi-ui", "src", "views");
        assertTrue(Files.isRegularFile(uiRoot.resolve("sensor/access/points.vue")));
        assertTrue(Files.isRegularFile(uiRoot.resolve("system/vibration/index.vue")));
    }

    private int count(Statement statement, String sql) throws Exception
    {
        try (ResultSet result = statement.executeQuery(sql)) { result.next(); return result.getInt(1); }
    }

    private Connection connection() throws Exception
    {
        return DriverManager.getConnection(MYSQL.getJdbcUrl(), MYSQL.getUsername(), MYSQL.getPassword());
    }
}
