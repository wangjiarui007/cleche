package db.migration;

import static org.junit.jupiter.api.Assertions.assertEquals;

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
class IndustrialMenuReorganizationMigrationTest
{
    @Container
    static final MySQLContainer<?> MYSQL = new MySQLContainer<>("mysql:8.4")
        .withDatabaseName("ry_menu_reorganization").withUsername("test").withPassword("test");

    @Test
    void movesLegacyIntakeMenusAndPreservesRoleAccess() throws Exception
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
            statement.execute("""
                INSERT INTO sys_menu(menu_id,menu_name,parent_id,order_num,path,component,route_name,menu_type,perms)
                VALUES
                  (10,'监测与数据',0,4,'monitoring-center',NULL,'MonitoringCenter','M',''),
                  (11,'PHM中心',0,6,'phm',NULL,'PhmCenter','M',''),
                  (20,'数据接入',0,20,'sensor-access',NULL,'SensorAccess','M',''),
                  (21,'测点接入',20,1,'points','sensor/access/points','SensorAccessPoints','C','sensor:channel:list'),
                  (22,'振动文件接收',20,2,'files','sensor/ingest/files','SensorIngestFiles','C','sensor:ingest:list'),
                  (23,'通道新增',21,1,'#','','','F','sensor:channel:add'),
                  (24,'失败重试',22,1,'#','','','F','sensor:ingest:retry')
                """);
            statement.execute("INSERT INTO sys_role_menu VALUES(7,20),(7,21),(7,22),(7,23),(7,24)");
        }

        Flyway flyway = Flyway.configure()
            .dataSource(MYSQL.getJdbcUrl(), MYSQL.getUsername(), MYSQL.getPassword())
            .locations("classpath:db/migration-targeted")
            .failOnMissingLocations(false)
            .baselineOnMigrate(true).baselineVersion("2026081700")
            .javaMigrations(new V2026081701__ReorganizeIndustrialMenus()).load();
        assertEquals(1, flyway.migrate().migrationsExecuted);
        assertEquals(0, flyway.migrate().migrationsExecuted);

        try (Connection connection = connection(); Statement statement = connection.createStatement())
        {
            assertEquals(0, count(statement, "SELECT COUNT(*) FROM sys_menu WHERE parent_id=0 AND path='sensor-access'"));
            assertEquals(1, count(statement, "SELECT COUNT(*) FROM sys_menu child JOIN sys_menu parent ON parent.menu_id=child.parent_id WHERE child.menu_id=21 AND parent.path='phm'"));
            assertEquals(1, count(statement, "SELECT COUNT(*) FROM sys_menu child JOIN sys_menu parent ON parent.menu_id=child.parent_id WHERE child.menu_id=22 AND parent.path='monitoring-center'"));
            assertEquals(1, count(statement, "SELECT COUNT(*) FROM sys_menu WHERE menu_id=21 AND component='sensor/access/points' AND perms='sensor:channel:list'"));
            assertEquals(1, count(statement, "SELECT COUNT(*) FROM sys_menu WHERE menu_id=22 AND component='sensor/ingest/files' AND perms='sensor:ingest:list'"));
            assertEquals(6, count(statement, "SELECT COUNT(*) FROM sys_role_menu WHERE role_id=7"));
            assertEquals(0, count(statement, "SELECT COUNT(*) FROM sys_role_menu rm LEFT JOIN sys_menu m ON m.menu_id=rm.menu_id WHERE m.menu_id IS NULL"));
        }
    }

    private int count(Statement statement, String sql) throws Exception
    {
        try (ResultSet result = statement.executeQuery(sql))
        {
            result.next();
            return result.getInt(1);
        }
    }

    private Connection connection() throws Exception
    {
        return DriverManager.getConnection(MYSQL.getJdbcUrl(), MYSQL.getUsername(), MYSQL.getPassword());
    }
}
