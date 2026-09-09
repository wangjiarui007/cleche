package com.ruoyi.config;

import java.nio.charset.StandardCharsets;
import java.nio.file.Path;
import java.util.ArrayList;
import java.util.Arrays;
import java.util.List;
import org.springframework.boot.ApplicationArguments;
import org.springframework.boot.ApplicationRunner;
import org.springframework.core.env.Environment;
import org.springframework.context.annotation.Profile;
import org.springframework.stereotype.Component;
import org.springframework.core.annotation.Order;
import org.springframework.util.StringUtils;

/**
 * Refuses to start the production profile with development defaults or missing secrets.
 */
@Component
@Profile("prod")
@Order(20)
public class ProductionConfigurationValidator implements ApplicationRunner
{
    private final Environment environment;

    public ProductionConfigurationValidator(Environment environment)
    {
        this.environment = environment;
    }

    @Override
    public void run(ApplicationArguments args)
    {
        List<String> errors = validate(environment);
        if (!errors.isEmpty())
        {
            throw new IllegalStateException("Production configuration is invalid:\n - "
                    + String.join("\n - ", errors));
        }
    }

    static List<String> validate(Environment env)
    {
        List<String> errors = new ArrayList<>();
        requireSecret(env, errors, "sensor.inference.internal-token", 32);
        requireSecret(env, errors, "spring.datasource.password", 8);
        requireSecret(env, errors, "spring.data.redis.password", 8);
        requireSecret(env, errors, "sensor.iotdb.password", 8);
        requireSecret(env, errors, "lowcode.datasource.password", 16);

        requireValue(env, errors, "spring.datasource.url");
        requireValue(env, errors, "spring.datasource.username");
        requireValue(env, errors, "spring.data.redis.host");
        requireValue(env, errors, "sensor.iotdb.node-urls");
        requireValue(env, errors, "sensor.iotdb.username");
        requireValue(env, errors, "lowcode.datasource.url");
        requireValue(env, errors, "lowcode.datasource.username");
        requireValue(env, errors, "lowcode.connector.proxy-host");
        int proxyPort = env.getProperty("lowcode.connector.proxy-port", Integer.class, 0);
        if (proxyPort < 1 || proxyPort > 65535) errors.add("lowcode.connector.proxy-port must be between 1 and 65535");
        validateLowCodeDataSource(env, errors);
        validateWritablePath(env, errors, "ruoyi.profile");
        validateWritablePath(env, errors, "logging.file.path");
        validateWritablePath(env, errors, "sensor.attachment.root");
        validateWritablePath(env, errors, "sensor.attachment.virus-scan-command");
        requireValue(env, errors, "sensor.attachment.virus-scan-arguments");
        validateOrigins(env, errors, "cors.allowed-origins");
        validateOrigins(env, errors, "sensor.websocket.allowed-origins");
        validateInternalInferenceUrl(env, errors);
        return errors;
    }

    private static void requireValue(Environment env, List<String> errors, String key)
    {
        if (!StringUtils.hasText(env.getProperty(key)))
        {
            errors.add(key + " must be configured");
        }
    }

    private static void requireSecret(Environment env, List<String> errors, String key, int minimumBytes)
    {
        String value = env.getProperty(key);
        if (!StringUtils.hasText(value))
        {
            errors.add(key + " must be configured");
            return;
        }
        if (value.getBytes(StandardCharsets.UTF_8).length < minimumBytes)
        {
            errors.add(key + " must contain at least " + minimumBytes + " UTF-8 bytes");
        }
        String normalized = value.trim().toLowerCase();
        if (normalized.contains("change-me") || normalized.contains("replace-with")
                || normalized.equals("admin123") || normalized.equals("123456")
                || normalized.equals("root") || normalized.startsWith("dev-"))
        {
            errors.add(key + " must not use a development/default value");
        }
    }

    private static void validateOrigins(Environment env, List<String> errors, String key)
    {
        String value = env.getProperty(key);
        if (!StringUtils.hasText(value))
        {
            errors.add(key + " must be configured");
            return;
        }
        boolean invalid = Arrays.stream(value.split(","))
                .map(String::trim)
                .anyMatch(origin -> origin.equals("*")
                        || origin.contains("localhost")
                        || origin.contains("127.0.0.1"));
        if (invalid)
        {
            errors.add(key + " must contain production origins only");
        }
    }

    private static void validateWritablePath(Environment env, List<String> errors, String key)
    {
        String value = env.getProperty(key);
        if (!StringUtils.hasText(value))
        {
            errors.add(key + " must be configured");
            return;
        }
        if (!isAbsolutePath(value))
        {
            errors.add(key + " must be an absolute path");
        }
    }

    private static boolean isAbsolutePath(String value)
    {
        try
        {
            if (Path.of(value).isAbsolute())
            {
                return true;
            }
            return value.matches("^[A-Za-z]:[/\\\\].*");
        }
        catch (Exception ex)
        {
            return false;
        }
    }

    private static void validateInternalInferenceUrl(Environment env, List<String> errors)
    {
        String value = env.getProperty("sensor.inference.gear-url");
        if (!StringUtils.hasText(value))
        {
            errors.add("sensor.inference.gear-url must be configured");
            return;
        }
        if (!(value.startsWith("http://127.0.0.1:") || value.startsWith("http://localhost:")))
        {
            errors.add("sensor.inference.gear-url must target the local internal inference service");
        }
    }

    private static void validateLowCodeDataSource(Environment env, List<String> errors)
    {
        String lowCodeUrl = env.getProperty("lowcode.datasource.url", "").trim();
        String systemUrl = env.getProperty("spring.datasource.url", "").trim();
        String lowCodeUser = env.getProperty("lowcode.datasource.username", "").trim();
        String systemUser = env.getProperty("spring.datasource.username", "").trim();
        if (lowCodeUrl.equalsIgnoreCase(systemUrl))
        {
            errors.add("lowcode.datasource.url must target a dedicated schema");
        }
        if (lowCodeUser.equalsIgnoreCase(systemUser))
        {
            errors.add("lowcode.datasource.username must use a dedicated least-privilege account");
        }
    }
}
