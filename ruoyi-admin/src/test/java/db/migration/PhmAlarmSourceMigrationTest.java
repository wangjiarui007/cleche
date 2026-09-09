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
class PhmAlarmSourceMigrationTest
{
    @Container
    static final MySQLContainer<?> MYSQL = new MySQLContainer<>("mysql:8.4")
        .withDatabaseName("ry_phm_alarm_source").withUsername("test").withPassword("test");

    @Test
    void addsSourceBackfillsHistoryAndIsIdempotent() throws Exception
    {
        try (Connection connection = connection(); Statement statement = connection.createStatement())
        {
            statement.execute("CREATE TABLE phm_alarm_event (id BIGINT PRIMARY KEY, alarm_type VARCHAR(32), "
                + "status VARCHAR(32), alarm_time DATETIME)");
            statement.execute("INSERT INTO phm_alarm_event VALUES (1,'diagnosis','unhandled',NOW()),"
                + "(2,'threshold','handled',NOW()),(3,'manual','ignored',NOW())");
        }

        Flyway flyway = Flyway.configure()
            .dataSource(MYSQL.getJdbcUrl(), MYSQL.getUsername(), MYSQL.getPassword())
            .locations("classpath:db/migration-targeted")
            .failOnMissingLocations(false)
            .baselineOnMigrate(true).baselineVersion("2026081704")
            .javaMigrations(new V2026081705__PhmAlarmSource()).load();
        assertEquals(1, flyway.migrate().migrationsExecuted);
        assertEquals(0, flyway.migrate().migrationsExecuted);

        try (Connection connection = connection(); Statement statement = connection.createStatement())
        {
            assertTrue(exists(statement, "SELECT 1 FROM information_schema.columns "
                + "WHERE table_schema=DATABASE() AND table_name='phm_alarm_event' AND column_name='alarm_source'"));
            assertEquals(1, sourceCount(statement, "MODEL"));
            assertEquals(2, sourceCount(statement, "BUSINESS"));
            assertTrue(exists(statement, "SELECT 1 FROM information_schema.statistics "
                + "WHERE table_schema=DATABASE() AND table_name='phm_alarm_event' "
                + "AND index_name='idx_phm_alarm_source_status_time'"));
        }
    }

    private int sourceCount(Statement statement, String source) throws Exception
    {
        try (var rows = statement.executeQuery("SELECT COUNT(*) FROM phm_alarm_event WHERE alarm_source='" + source + "'"))
        {
            rows.next();
            return rows.getInt(1);
        }
    }

    private boolean exists(Statement statement, String sql) throws Exception
    {
        try (ResultSet rows = statement.executeQuery(sql))
        {
            return rows.next();
        }
    }

    private Connection connection() throws Exception
    {
        return DriverManager.getConnection(MYSQL.getJdbcUrl(), MYSQL.getUsername(), MYSQL.getPassword());
    }
}
