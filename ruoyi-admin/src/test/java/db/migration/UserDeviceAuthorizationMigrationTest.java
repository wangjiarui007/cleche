package db.migration;

import static org.junit.jupiter.api.Assertions.assertEquals;
import static org.junit.jupiter.api.Assertions.assertTrue;

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
class UserDeviceAuthorizationMigrationTest
{
    @Container
    static final MySQLContainer<?> MYSQL = new MySQLContainer<>("mysql:8.4")
        .withDatabaseName("ry_user_device_auth").withUsername("test").withPassword("test");

    @Test
    void createsUserDeviceTableAndRemovesDepartmentMenusIdempotently() throws Exception
    {
        try (Connection connection = connection(); Statement statement = connection.createStatement())
        {
            statement.execute("CREATE TABLE sys_user(user_id BIGINT PRIMARY KEY)");
            statement.execute("CREATE TABLE sys_menu(menu_id BIGINT PRIMARY KEY, parent_id BIGINT, path VARCHAR(100), menu_name VARCHAR(100))");
            statement.execute("CREATE TABLE sys_role_menu(role_id BIGINT, menu_id BIGINT)");
            statement.execute("INSERT INTO sys_menu VALUES (103,1,'dept','部门管理'),(1016,103,'#','部门查询')");
            statement.execute("INSERT INTO sys_role_menu VALUES (2,103),(2,1016)");
        }

        Flyway flyway = Flyway.configure()
            .dataSource(MYSQL.getJdbcUrl(), MYSQL.getUsername(), MYSQL.getPassword())
            .locations("classpath:db/migration-targeted")
            .failOnMissingLocations(false)
            .baselineOnMigrate(true).baselineVersion("2026081703")
            .javaMigrations(new V2026081704__UserDeviceAuthorization()).load();
        assertEquals(1, flyway.migrate().migrationsExecuted);
        assertEquals(0, flyway.migrate().migrationsExecuted);

        try (Connection connection = connection(); Statement statement = connection.createStatement())
        {
            assertTrue(exists(statement, "SELECT 1 FROM information_schema.tables WHERE table_schema=DATABASE() AND table_name='sys_user_device'"));
            assertEquals(0, count(statement, "SELECT COUNT(*) FROM sys_menu WHERE path='dept'"));
            assertEquals(0, count(statement, "SELECT COUNT(*) FROM sys_menu WHERE parent_id=103"));
            assertEquals(0, count(statement, "SELECT COUNT(*) FROM sys_role_menu"));
        }
    }

    private boolean exists(Statement statement, String sql) throws Exception
    {
        try (ResultSet result = statement.executeQuery(sql))
        {
            return result.next();
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
