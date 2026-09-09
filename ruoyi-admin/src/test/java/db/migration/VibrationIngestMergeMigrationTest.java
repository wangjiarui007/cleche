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
class VibrationIngestMergeMigrationTest
{
    @Container
    static final MySQLContainer<?> MYSQL = new MySQLContainer<>("mysql:8.4")
        .withDatabaseName("ry_vibration_merge").withUsername("test").withPassword("test");

    @Test
    void removesLegacyPageAndPreservesRoleCapabilitiesIdempotently() throws Exception
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
                VALUES (10,'监测与数据',0,4,'monitoring-center','','MonitoringCenter','M',''),
                       (11,'振动分析',10,3,'vibration','system/vibration/index','VibrationData','C','sensor:vibration:list'),
                       (12,'振动文件接收',10,6,'files','sensor/ingest/files','SensorIngestFiles','C','sensor:ingest:list'),
                       (13,'文件关联',12,1,'#','','','F','sensor:ingest:associate'),
                       (14,'失败重试',12,2,'#','','','F','sensor:ingest:retry')
                """);
            statement.execute("INSERT INTO sys_role_menu VALUES(7,12),(7,13),(7,14)");
        }

        Flyway flyway = Flyway.configure()
            .dataSource(MYSQL.getJdbcUrl(), MYSQL.getUsername(), MYSQL.getPassword())
            .locations("classpath:db/migration-targeted")
            .failOnMissingLocations(false)
            .baselineOnMigrate(true).baselineVersion("2026081705")
            .javaMigrations(new V2026081706__MergeVibrationIngestIntoAnalysis()).load();
        assertEquals(1, flyway.migrate().migrationsExecuted);
        assertEquals(0, flyway.migrate().migrationsExecuted);

        try (Connection connection = connection(); Statement statement = connection.createStatement())
        {
            assertEquals(0, count(statement, "SELECT COUNT(*) FROM sys_menu WHERE path='files'"));
            assertEquals(1, count(statement, "SELECT COUNT(*) FROM sys_menu WHERE parent_id=11 AND perms='sensor:ingest:list'"));
            assertEquals(1, count(statement, "SELECT COUNT(*) FROM sys_menu WHERE parent_id=11 AND perms='sensor:ingest:associate'"));
            assertEquals(1, count(statement, "SELECT COUNT(*) FROM sys_menu WHERE parent_id=11 AND perms='sensor:ingest:retry'"));
            assertEquals(4, count(statement, "SELECT COUNT(*) FROM sys_role_menu WHERE role_id=7"));
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
