package db.migration;

import static org.junit.jupiter.api.Assertions.assertEquals;
import static org.junit.jupiter.api.Assertions.assertFalse;

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
class LegacyPlatformCleanupMigrationTest
{
    @Container
    static final MySQLContainer<?> MYSQL = new MySQLContainer<>("mysql:8.4")
        .withDatabaseName("ry_cleanup").withUsername("test").withPassword("test");

    @Test
    void removesLegacyTablesMenusAndRelationsButKeepsRetainedMenu() throws Exception
    {
        try (Connection connection = connection(); Statement statement = connection.createStatement())
        {
            statement.execute("CREATE TABLE sys_menu(menu_id BIGINT PRIMARY KEY,menu_name VARCHAR(50),parent_id BIGINT,path VARCHAR(200),component VARCHAR(255),perms VARCHAR(100))");
            statement.execute("CREATE TABLE sys_role_menu(role_id BIGINT,menu_id BIGINT)");
            statement.execute("INSERT INTO sys_menu VALUES (1,'系统工具',0,'tool','',''),(2,'代码生成',1,'gen','tool/gen/index','tool:gen:list'),(3,'低代码工作台',1,'lowcode','tool/lowcode/index','tool:lowcode:list'),(4,'生成查询',2,'','','tool:gen:query')");
            statement.execute("INSERT INTO sys_role_menu VALUES (7,2),(7,3),(7,4)");
            for (String table : new String[] { "sys_job", "sys_job_log", "gen_table", "gen_table_column", "sys_user_post", "sys_post", "sys_notice_read", "sys_notice" })
            {
                statement.execute("CREATE TABLE " + table + "(id BIGINT)");
            }
        }

        Flyway.configure().dataSource(MYSQL.getJdbcUrl(), MYSQL.getUsername(), MYSQL.getPassword())
            .locations("classpath:db/migration-targeted")
            .failOnMissingLocations(false)
            .baselineOnMigrate(true).baselineVersion("2026081500")
            .javaMigrations(new V2026081601__RemoveLegacyPlatformComponents()).load().migrate();

        try (Connection connection = connection(); Statement statement = connection.createStatement())
        {
            assertFalse(exists(statement, "sys_job"));
            assertFalse(exists(statement, "gen_table"));
            assertFalse(exists(statement, "sys_post"));
            assertFalse(exists(statement, "sys_notice"));
            try (ResultSet result = statement.executeQuery("SELECT COUNT(*) FROM sys_menu WHERE menu_id=3"))
            {
                result.next();
                assertEquals(1, result.getInt(1));
            }
            try (ResultSet result = statement.executeQuery("SELECT COUNT(*) FROM sys_role_menu WHERE menu_id=3"))
            {
                result.next();
                assertEquals(1, result.getInt(1));
            }
            try (ResultSet result = statement.executeQuery("SELECT COUNT(*) FROM sys_role_menu WHERE menu_id IN (2,4)"))
            {
                result.next();
                assertEquals(0, result.getInt(1));
            }
        }
    }

    private Connection connection() throws Exception
    {
        return DriverManager.getConnection(MYSQL.getJdbcUrl(), MYSQL.getUsername(), MYSQL.getPassword());
    }

    private boolean exists(Statement statement, String table) throws Exception
    {
        try (ResultSet result = statement.executeQuery("SELECT COUNT(*) FROM information_schema.tables WHERE table_schema=DATABASE() AND table_name='" + table + "'"))
        {
            result.next();
            return result.getInt(1) > 0;
        }
    }
}
