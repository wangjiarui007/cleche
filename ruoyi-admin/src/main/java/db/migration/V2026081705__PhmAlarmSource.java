package db.migration;

import java.sql.Connection;
import java.sql.PreparedStatement;
import java.sql.ResultSet;
import java.sql.Statement;
import org.flywaydb.core.api.migration.BaseJavaMigration;
import org.flywaydb.core.api.migration.Context;

/** Adds the explicit BUSINESS/MODEL source to the unified PHM alarm table. */
public class V2026081705__PhmAlarmSource extends BaseJavaMigration
{
    @Override
    public boolean canExecuteInTransaction()
    {
        return false;
    }

    @Override
    public void migrate(Context context) throws Exception
    {
        Connection connection = context.getConnection();
        if (!tableExists(connection, "phm_alarm_event"))
        {
            return;
        }
        if (!columnExists(connection, "phm_alarm_event", "alarm_source"))
        {
            try (Statement statement = connection.createStatement())
            {
                statement.execute("ALTER TABLE phm_alarm_event "
                    + "ADD COLUMN alarm_source VARCHAR(16) NULL "
                    + "COMMENT 'BUSINESS/MODEL' AFTER alarm_type");
            }
        }
        try (PreparedStatement statement = connection.prepareStatement(
            "UPDATE phm_alarm_event SET alarm_source = "
            + "CASE WHEN LOWER(alarm_type) = 'diagnosis' THEN 'MODEL' ELSE 'BUSINESS' END "
            + "WHERE alarm_source IS NULL"))
        {
            statement.executeUpdate();
        }
        if (!columnNotNullOrDefault(connection, "phm_alarm_event", "alarm_source"))
        {
            try (Statement statement = connection.createStatement())
            {
                statement.execute("ALTER TABLE phm_alarm_event "
                    + "MODIFY alarm_source VARCHAR(16) NOT NULL DEFAULT 'BUSINESS' "
                    + "COMMENT 'BUSINESS/MODEL' AFTER alarm_type");
            }
        }
        if (!indexExists(connection, "phm_alarm_event", "idx_phm_alarm_source_status_time"))
        {
            try (Statement statement = connection.createStatement())
            {
                statement.execute("CREATE INDEX idx_phm_alarm_source_status_time "
                    + "ON phm_alarm_event (alarm_source, status, alarm_time)");
            }
        }
    }

    private boolean tableExists(Connection connection, String tableName) throws Exception
    {
        try (ResultSet rows = connection.getMetaData().getTables(connection.getCatalog(), null, tableName, new String[] {"TABLE"}))
        {
            return rows.next();
        }
    }

    private boolean columnExists(Connection connection, String tableName, String columnName) throws Exception
    {
        try (PreparedStatement statement = connection.prepareStatement(
            "SELECT 1 FROM information_schema.columns WHERE table_schema=? AND table_name=? AND column_name=?"))
        {
            statement.setString(1, connection.getCatalog());
            statement.setString(2, tableName);
            statement.setString(3, columnName);
            try (ResultSet rows = statement.executeQuery())
            {
                return rows.next();
            }
        }
    }

    private boolean columnNotNullOrDefault(Connection connection, String tableName, String columnName) throws Exception
    {
        try (PreparedStatement statement = connection.prepareStatement(
            "SELECT 1 FROM information_schema.columns WHERE table_schema=? AND table_name=? "
            + "AND column_name=? AND is_nullable='NO' AND column_default IS NOT NULL"))
        {
            statement.setString(1, connection.getCatalog());
            statement.setString(2, tableName);
            statement.setString(3, columnName);
            try (ResultSet rows = statement.executeQuery())
            {
                return rows.next();
            }
        }
    }

    private boolean indexExists(Connection connection, String tableName, String indexName) throws Exception
    {
        try (PreparedStatement statement = connection.prepareStatement(
            "SELECT 1 FROM information_schema.statistics WHERE table_schema=? AND table_name=? AND index_name=?"))
        {
            statement.setString(1, connection.getCatalog());
            statement.setString(2, tableName);
            statement.setString(3, indexName);
            try (ResultSet rows = statement.executeQuery())
            {
                return rows.next();
            }
        }
    }
}
