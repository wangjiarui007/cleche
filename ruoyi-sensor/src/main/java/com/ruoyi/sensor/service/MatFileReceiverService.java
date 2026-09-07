package com.ruoyi.sensor.service;

import java.io.BufferedInputStream;
import java.io.BufferedOutputStream;
import java.io.DataInputStream;
import java.io.DataOutputStream;
import java.io.IOException;
import java.net.InetSocketAddress;
import java.net.ServerSocket;
import java.net.Socket;
import java.nio.charset.StandardCharsets;
import java.nio.file.Files;
import java.nio.file.Path;
import java.nio.file.StandardCopyOption;
import java.security.MessageDigest;
import java.time.OffsetDateTime;
import java.util.Date;
import java.util.HexFormat;
import java.util.LinkedHashMap;
import java.util.List;
import java.util.Map;
import java.util.concurrent.ExecutorService;
import java.util.concurrent.ConcurrentHashMap;
import java.util.concurrent.Executors;
import java.util.concurrent.Semaphore;
import java.util.concurrent.TimeUnit;
import java.util.concurrent.atomic.AtomicInteger;
import java.util.concurrent.atomic.AtomicLong;

import com.alibaba.fastjson2.JSON;
import com.baomidou.mybatisplus.core.conditions.query.LambdaQueryWrapper;
import com.fasterxml.jackson.databind.ObjectMapper;
import com.ruoyi.sensor.domain.dto.MatFileProtocolHeader;
import com.ruoyi.sensor.domain.entity.PhmAcquisitionChannelEntity;
import com.ruoyi.sensor.domain.entity.PhmAttachmentEntity;
import com.ruoyi.sensor.domain.entity.PhmDeviceEntity;
import com.ruoyi.sensor.domain.entity.PhmDiagnosisBindingEntity;
import com.ruoyi.sensor.domain.entity.PhmMeasurePointEntity;
import com.ruoyi.sensor.domain.entity.SensorIngestFileEntity;
import com.ruoyi.sensor.mapper.PhmAcquisitionChannelMapper;
import com.ruoyi.sensor.mapper.PhmDeviceMapper;
import com.ruoyi.sensor.mapper.PhmDiagnosisBindingMapper;
import com.ruoyi.sensor.mapper.PhmMeasurePointMapper;
import com.ruoyi.sensor.mapper.SensorIngestFileMapper;
import com.ruoyi.sensor.web.VibrationDiagnosisController;
import org.slf4j.Logger;
import org.slf4j.LoggerFactory;
import org.springframework.beans.factory.annotation.Value;
import org.springframework.boot.autoconfigure.condition.ConditionalOnProperty;
import org.springframework.context.SmartLifecycle;
import org.springframework.stereotype.Service;

/** Spring-managed CWRU_MAT_V2 receiver. */
@Service
@ConditionalOnProperty(prefix = "sensor.mat-receiver", name = "enabled", havingValue = "true", matchIfMissing = true)
public class MatFileReceiverService implements SmartLifecycle
{
    private static final Logger log = LoggerFactory.getLogger(MatFileReceiverService.class);
    private static final String MAGIC = "CWRU_MAT_V2";
    private static final int MAX_HEADER_LENGTH = 64 * 1024;

    private final ObjectMapper objectMapper;
    private final PhmDeviceMapper deviceMapper;
    private final PhmMeasurePointMapper pointMapper;
    private final PhmAcquisitionChannelMapper channelMapper;
    private final PhmDiagnosisBindingMapper bindingMapper;
    private final SensorIngestFileMapper ingestMapper;
    private final PhmAttachmentStorageService attachmentStorage;
    private final VibrationDiagnosisController diagnosisController;
    private final String bindAddress;
    private final int port;
    private final long maxFileSize;
    private final int socketTimeoutMs;
    private final Path root;
    private final Path quarantineRoot;
    private final ExecutorService workers;
    private final Semaphore permits;
    private final ConcurrentHashMap<String, Object> processingLocks = new ConcurrentHashMap<>();
    private final AtomicInteger activeConnections = new AtomicInteger();
    private final AtomicLong receivedCount = new AtomicLong();
    private final AtomicLong acceptedCount = new AtomicLong();
    private final AtomicLong duplicateCount = new AtomicLong();
    private final AtomicLong quarantineCount = new AtomicLong();
    private final AtomicLong failedCount = new AtomicLong();
    private volatile boolean running;
    private ServerSocket serverSocket;
    private Thread acceptThread;

    public MatFileReceiverService(ObjectMapper objectMapper,
        PhmDeviceMapper deviceMapper, PhmMeasurePointMapper pointMapper,
        PhmAcquisitionChannelMapper channelMapper, PhmDiagnosisBindingMapper bindingMapper,
        SensorIngestFileMapper ingestMapper, PhmAttachmentStorageService attachmentStorage,
        VibrationDiagnosisController diagnosisController,
        @Value("${sensor.mat-receiver.bind-address:0.0.0.0}") String bindAddress,
        @Value("${sensor.mat-receiver.port:8888}") int port,
        @Value("${sensor.mat-receiver.max-file-size:134217728}") long maxFileSize,
        @Value("${sensor.mat-receiver.socket-timeout-ms:120000}") int socketTimeoutMs,
        @Value("${sensor.mat-receiver.root:./.local-data/mat-receiver}") String root,
        @Value("${sensor.mat-receiver.worker-count:4}") int workerCount,
        @Value("${sensor.mat-receiver.max-connections:8}") int maxConnections) throws IOException
    {
        this.objectMapper = objectMapper;
        this.deviceMapper = deviceMapper;
        this.pointMapper = pointMapper;
        this.channelMapper = channelMapper;
        this.bindingMapper = bindingMapper;
        this.ingestMapper = ingestMapper;
        this.attachmentStorage = attachmentStorage;
        this.diagnosisController = diagnosisController;
        this.bindAddress = bindAddress;
        this.port = port;
        this.maxFileSize = Math.max(1, maxFileSize);
        this.socketTimeoutMs = Math.max(1000, socketTimeoutMs);
        this.root = Path.of(root).toAbsolutePath().normalize();
        this.quarantineRoot = this.root.resolve("quarantine").normalize();
        Files.createDirectories(this.root);
        Files.createDirectories(this.quarantineRoot);
        this.workers = Executors.newFixedThreadPool(Math.max(1, workerCount), runnable -> {
            Thread thread = new Thread(runnable, "mat-v2-worker");
            thread.setDaemon(true);
            return thread;
        });
        this.permits = new Semaphore(Math.max(1, maxConnections));
    }

    @Override
    public synchronized void start()
    {
        if (running) return;
        try
        {
            serverSocket = new ServerSocket();
            serverSocket.setReuseAddress(true);
            serverSocket.bind(new InetSocketAddress(bindAddress, port));
            running = true;
            acceptThread = new Thread(this::acceptLoop, "mat-v2-acceptor");
            acceptThread.setDaemon(true);
            acceptThread.start();
            log.info("CWRU_MAT_V2 接收服务已启动: {}:{}", bindAddress, port);
        }
        catch (IOException ex)
        {
            running = false;
            throw new IllegalStateException("无法启动 MAT 接收服务 " + bindAddress + ":" + port, ex);
        }
    }

    private void acceptLoop()
    {
        while (running)
        {
            try
            {
                Socket socket = serverSocket.accept();
                if (!permits.tryAcquire())
                {
                    writeErrorAndClose(socket, "BUSY", "MAT 接收并发数已达上限");
                    continue;
                }
                workers.execute(() -> {
                    activeConnections.incrementAndGet();
                    try { handle(socket); }
                    finally
                    {
                        activeConnections.decrementAndGet();
                        permits.release();
                    }
                });
            }
            catch (IOException ex)
            {
                if (running) log.error("MAT 接收服务接受连接失败", ex);
            }
        }
    }

    private void handle(Socket socket)
    {
        Path temp = null;
        SensorIngestFileEntity ingest = null;
        MatFileProtocolHeader header = null;
        DataInputStream input = null;
        DataOutputStream output = null;
        try
        {
            socket.setSoTimeout(socketTimeoutMs);
            input = new DataInputStream(new BufferedInputStream(socket.getInputStream()));
            output = new DataOutputStream(new BufferedOutputStream(socket.getOutputStream()));
            String magic = readLine(input, 128);
            if (!MAGIC.equals(magic))
            {
                writeResult(output, Map.of("status", "ERROR", "errorCode", "UNSUPPORTED_PROTOCOL",
                    "message", "仅支持 CWRU_MAT_V2"));
                return;
            }
            int headerLength = input.readInt();
            if (headerLength <= 0 || headerLength > MAX_HEADER_LENGTH)
            {
                writeResult(output, Map.of("status", "ERROR", "errorCode", "INVALID_HEADER_LENGTH"));
                return;
            }
            byte[] headerBytes = input.readNBytes(headerLength);
            if (headerBytes.length != headerLength)
            {
                writeResult(output, Map.of("status", "ERROR", "errorCode", "TRUNCATED_HEADER"));
                return;
            }
            header = objectMapper.readValue(headerBytes, MatFileProtocolHeader.class);
            header.validate(maxFileSize);
            OffsetDateTime acquisitionTime = header.parsedAcquisitionTime();
            ingest = createLedger(header, socket);
            writeLine(output, "READY");

            temp = root.resolve(safeName(ingest.getId() + "-" + header.getFilename() + ".part")).normalize();
            receiveBody(input, temp, header.getFilesize(), header.getSha256());
            validateMatSignature(temp);

            Mapping mapping;
            try
            {
                mapping = resolveMapping(header);
            }
            catch (IllegalArgumentException mappingError)
            {
                Path isolated = quarantine(temp, header, "MAPPING_FAILED");
                ingest.setSourceRef(isolated == null ? ingest.getSourceRef() : isolated.toString());
                updateLedger(ingest, null, "UNMAPPED", "MAPPING_FAILED", mappingError.getMessage());
                writeResult(output, Map.of("status", "QUARANTINED", "ingestId", ingest.getId(),
                    "errorCode", "MAPPING_FAILED", "message", safeMessage(mappingError)));
                return;
            }
            if (mapping.binding == null)
            {
                Path isolated = quarantine(temp, header, "MODEL_NOT_BOUND");
                ingest.setSourceRef(isolated == null ? ingest.getSourceRef() : isolated.toString());
                updateLedger(ingest, null, "UNMAPPED", "MODEL_NOT_BOUND", "测点未配置唯一启用的主诊断模型");
                writeResult(output, Map.of("status", "QUARANTINED", "ingestId", ingest.getId(),
                    "errorCode", "MODEL_NOT_BOUND"));
                return;
            }

            String lockKey = mapping.device.getId() + ":" + mapping.point.getId() + ":"
                + header.getSha256().toLowerCase();
            Object lock = processingLocks.computeIfAbsent(lockKey, ignored -> new Object());
            try
            {
                synchronized (lock)
                {
                    PhmAttachmentEntity duplicate = attachmentStorage.findDiagnosisInputByDevicePointAndSha256(
                        mapping.device.getId(), mapping.point.getId(), header.getSha256());
                    if (duplicate != null)
                    {
                        duplicateCount.incrementAndGet();
                        updateLedger(ingest, duplicate.getId(), "DUPLICATE", null, null);
                        Files.deleteIfExists(temp);
                        writeResult(output, Map.of("status", "DUPLICATE", "ingestId", ingest.getId(),
                            "attachmentId", duplicate.getId()));
                        return;
                    }

                    PhmAttachmentEntity attachment = attachmentStorage.importDiagnosisFile(temp,
                        mapping.device.getId(), mapping.point.getId(), header.getChannelId(), "mat-tcp");
                    applyMapping(ingest, mapping.device, mapping.point, header.getChannelId());
                    Map<String, Object> task = diagnosisController.submitInternalMatTask(
                        mapping.device.getDeviceCode(), mapping.point.getId(), header.getChannelId(),
                        attachment, mapping.binding.getModelType(), mapping.binding.getModelVersion(),
                        Date.from(acquisitionTime.toInstant()));
                    updateLedger(ingest, attachment.getId(), "ACCEPTED", null, null);
                    acceptedCount.incrementAndGet();
                    Files.deleteIfExists(temp);
                    writeResult(output, Map.of("status", "ACCEPTED", "ingestId", ingest.getId(),
                        "attachmentId", attachment.getId(), "taskId", task.get("id")));
                }
            }
            finally
            {
                processingLocks.remove(lockKey, lock);
            }
        }
        catch (Exception ex)
        {
            if (temp != null)
            {
                try
                {
                    Path isolated = quarantine(temp, header, "RECEIVE_FAILED");
                    if (ingest != null && isolated != null) ingest.setSourceRef(isolated.toString());
                }
                catch (Exception ignored) { }
            }
            if (ingest != null)
            {
                failedCount.incrementAndGet();
                updateLedger(ingest, null, "FAILED", "RECEIVE_FAILED", safeMessage(ex));
            }
            log.warn("MAT 文件接收失败, remote={}: {}", socket.getRemoteSocketAddress(), ex.getMessage());
            try
            {
                if (output != null) writeResult(output, Map.of("status", "ERROR", "errorCode", "RECEIVE_FAILED",
                    "message", safeMessage(ex)));
            }
            catch (Exception ignored) { }
        }
        finally
        {
            try { if (output != null) output.close(); } catch (IOException ignored) { }
            try { if (input != null) input.close(); } catch (IOException ignored) { }
            try { socket.close(); } catch (IOException ignored) { }
        }
    }

    private SensorIngestFileEntity createLedger(MatFileProtocolHeader header, Socket socket)
    {
        Date now = new Date();
        SensorIngestFileEntity entity = new SensorIngestFileEntity();
        entity.setSourceType("MAT_TCP");
        entity.setSourceRef(String.valueOf(socket.getRemoteSocketAddress()));
        entity.setDeviceCode(header.getDeviceCode());
        entity.setPointCode(header.getPointCode());
        entity.setChannelId(header.getChannelId());
        entity.setFileName(header.getFilename());
        entity.setFileExt("mat");
        entity.setFileSize(header.getFilesize());
        entity.setSha256(header.getSha256().toLowerCase());
        entity.setStatus("RECEIVING");
        entity.setRetryCount(0);
        entity.setAcquisitionTime(Date.from(header.parsedAcquisitionTime().toInstant()));
        entity.setReceivedTime(now);
        entity.setCreateBy("mat-tcp");
        entity.setCreateTime(now);
        entity.setUpdateTime(now);
        ingestMapper.insert(entity);
        receivedCount.incrementAndGet();
        return entity;
    }

    private void applyMapping(SensorIngestFileEntity ingest, PhmDeviceEntity device,
        PhmMeasurePointEntity point, Integer channelId)
    {
        ingest.setDeviceId(device.getId());
        ingest.setDeviceCode(device.getDeviceCode());
        ingest.setPointId(point.getId());
        ingest.setPointCode(point.getPointCode());
        ingest.setChannelId(channelId);
    }

    private Mapping resolveMapping(MatFileProtocolHeader header)
    {
        PhmDeviceEntity device = deviceMapper.selectOne(new LambdaQueryWrapper<PhmDeviceEntity>()
            .eq(PhmDeviceEntity::getDeviceCode, header.getDeviceCode()).last("LIMIT 1"));
        if (device == null) throw new IllegalArgumentException("设备不存在: " + header.getDeviceCode());
        PhmMeasurePointEntity point = pointMapper.selectOne(new LambdaQueryWrapper<PhmMeasurePointEntity>()
            .eq(PhmMeasurePointEntity::getDeviceId, device.getId())
            .eq(PhmMeasurePointEntity::getPointCode, header.getPointCode())
            .eq(PhmMeasurePointEntity::getEnabled, true)
            .eq(PhmMeasurePointEntity::getSignalType, "vibration")
            .last("LIMIT 1"));
        if (point == null) throw new IllegalArgumentException("振动测点不存在或已停用: " + header.getPointCode());
        if (!header.getChannelId().equals(point.getChannelId()))
        {
            throw new IllegalArgumentException("协议通道与测点通道不一致");
        }
        PhmAcquisitionChannelEntity channel = channelMapper.selectOne(new LambdaQueryWrapper<PhmAcquisitionChannelEntity>()
            .eq(PhmAcquisitionChannelEntity::getDeviceId, device.getId())
            .eq(PhmAcquisitionChannelEntity::getPointId, point.getId())
            .eq(PhmAcquisitionChannelEntity::getChannelNo, header.getChannelId())
            .eq(PhmAcquisitionChannelEntity::getEnabled, true)
            .last("LIMIT 1"));
        if (channel == null) throw new IllegalArgumentException("设备通道映射不存在或已停用");
        List<PhmDiagnosisBindingEntity> bindings = bindingMapper.selectList(new LambdaQueryWrapper<PhmDiagnosisBindingEntity>()
            .eq(PhmDiagnosisBindingEntity::getPointId, point.getId())
            .eq(PhmDiagnosisBindingEntity::getEnabled, true));
        if (bindings.size() > 1) throw new IllegalArgumentException("测点存在多个启用的主诊断模型");
        if (bindings.isEmpty()) throw new IllegalArgumentException("测点未绑定启用的诊断模型");
        PhmDiagnosisBindingEntity binding = bindings.get(0);
        if (!device.getId().equals(binding.getDeviceId())
            || !device.getDeviceCode().equals(binding.getDeviceCode())
            || !Long.valueOf(header.getChannelId()).equals(binding.getChannelId()))
        {
            throw new IllegalArgumentException("诊断模型绑定与设备、测点或物理通道不一致");
        }
        return new Mapping(device, point, binding);
    }

    private void receiveBody(DataInputStream input, Path target, long expectedSize, String expectedSha) throws Exception
    {
        MessageDigest digest = MessageDigest.getInstance("SHA-256");
        Files.createDirectories(target.getParent());
        long received = 0;
        byte[] buffer = new byte[64 * 1024];
        try (BufferedOutputStream output = new BufferedOutputStream(Files.newOutputStream(target)))
        {
            while (received < expectedSize)
            {
                int count = input.read(buffer, 0, (int)Math.min(buffer.length, expectedSize - received));
                if (count < 0) throw new IOException("文件体提前结束");
                output.write(buffer, 0, count);
                digest.update(buffer, 0, count);
                received += count;
            }
        }
        String actual = HexFormat.of().formatHex(digest.digest());
        if (!actual.equalsIgnoreCase(expectedSha)) throw new IOException("SHA-256 校验失败");
    }

    private void validateMatSignature(Path file) throws IOException
    {
        byte[] signature = new byte[128];
        try (var input = Files.newInputStream(file))
        {
            int offset = 0;
            while (offset < signature.length)
            {
                int count = input.read(signature, offset, signature.length - offset);
                if (count < 0) break;
                offset += count;
            }
            if (offset < 8) throw new IOException("MAT 文件签名不完整");
        }
        boolean hdf5 = signature[0] == (byte)0x89 && signature[1] == 'H'
            && signature[2] == 'D' && signature[3] == 'F'
            && signature[4] == 0x0D && signature[5] == 0x0A
            && signature[6] == 0x1A && signature[7] == 0x0A;
        boolean matV5 = new String(signature, 0, 6, StandardCharsets.US_ASCII).startsWith("MATLAB")
            && ((signature[126] == 'I' && signature[127] == 'M')
                || (signature[126] == 'M' && signature[127] == 'I'));
        if (!hdf5 && !matV5) throw new IOException("MAT 文件签名校验失败");
    }

    /** Completes a quarantined MAT file after an operator fixes its mapping. */
    public synchronized void retryQuarantined(Long ingestId, Long deviceId, Long pointId, Integer channelNo)
        throws Exception
    {
        SensorIngestFileEntity ingest = ingestMapper.selectById(ingestId);
        if (ingest == null || !"MAT_TCP".equals(ingest.getSourceType()))
            throw new IllegalArgumentException("MAT 接收台账不存在");
        if (!"UNMAPPED".equals(ingest.getStatus()) && !"VALIDATING".equals(ingest.getStatus())
            && !"RECEIVING".equals(ingest.getStatus()))
            throw new IllegalArgumentException("当前文件状态不允许重新关联");
        if (ingest.getSourceRef() == null || ingest.getSourceRef().isBlank())
            throw new IllegalArgumentException("隔离文件路径为空，无法重试");
        Path source = Path.of(ingest.getSourceRef()).toAbsolutePath().normalize();
        if (!source.startsWith(quarantineRoot) || !Files.isRegularFile(source))
            throw new IllegalArgumentException("隔离文件不存在，无法重试");
        PhmDeviceEntity device = deviceMapper.selectById(deviceId);
        PhmMeasurePointEntity point = pointMapper.selectById(pointId);
        if (device == null || point == null || !deviceId.equals(point.getDeviceId())
            || !Boolean.TRUE.equals(point.getEnabled()) || !"vibration".equalsIgnoreCase(point.getSignalType())
            || !channelNo.equals(point.getChannelId()))
            throw new IllegalArgumentException("设备、振动测点或物理通道不匹配");
        PhmAcquisitionChannelEntity channel = channelMapper.selectOne(new LambdaQueryWrapper<PhmAcquisitionChannelEntity>()
            .eq(PhmAcquisitionChannelEntity::getDeviceId, deviceId)
            .eq(PhmAcquisitionChannelEntity::getPointId, pointId)
            .eq(PhmAcquisitionChannelEntity::getChannelNo, channelNo)
            .eq(PhmAcquisitionChannelEntity::getEnabled, true)
            .last("LIMIT 1"));
        if (channel == null) throw new IllegalArgumentException("物理通道映射不存在或已停用");
        List<PhmDiagnosisBindingEntity> bindings = bindingMapper.selectList(new LambdaQueryWrapper<PhmDiagnosisBindingEntity>()
            .eq(PhmDiagnosisBindingEntity::getPointId, pointId)
            .eq(PhmDiagnosisBindingEntity::getEnabled, true));
        if (bindings.size() != 1) throw new IllegalArgumentException("测点必须配置唯一启用的主诊断模型");
        PhmAttachmentEntity duplicate = attachmentStorage.findDiagnosisInputByDevicePointAndSha256(
            deviceId, pointId, ingest.getSha256());
        if (duplicate != null)
        {
            duplicateCount.incrementAndGet();
            updateLedger(ingest, duplicate.getId(), "DUPLICATE", null, null);
            Files.deleteIfExists(source);
            return;
        }
        PhmAttachmentEntity attachment = attachmentStorage.importDiagnosisFile(source, deviceId, pointId,
            channelNo, "mat-tcp");
        applyMapping(ingest, device, point, channelNo);
        Map<String, Object> task = diagnosisController.submitInternalMatTask(device.getDeviceCode(), pointId,
            channelNo, attachment, bindings.get(0).getModelType(), bindings.get(0).getModelVersion(),
            ingest.getAcquisitionTime() == null
                ? (ingest.getReceivedTime() == null ? new Date() : ingest.getReceivedTime())
                : ingest.getAcquisitionTime());
        updateLedger(ingest, attachment.getId(), "ACCEPTED", null, null);
        acceptedCount.incrementAndGet();
        Files.deleteIfExists(source);
        log.info("隔离 MAT 文件已重新入队: ingestId={}, taskId={}", ingestId, task.get("id"));
    }

    private Path quarantine(Path source, MatFileProtocolHeader header, String reason) throws IOException
    {
        if (source == null || !Files.exists(source)) return null;
        String prefix = header == null ? "unknown" : safeName(header.getDeviceCode() + "-" + header.getPointCode());
        Path target = quarantineRoot.resolve(prefix + "-" + System.currentTimeMillis() + ".mat").normalize();
        if (!target.startsWith(quarantineRoot)) throw new IOException("隔离路径非法");
        Files.move(source, target, StandardCopyOption.REPLACE_EXISTING);
        quarantineCount.incrementAndGet();
        Files.writeString(target.resolveSibling(target.getFileName() + ".reason.txt"), reason,
            StandardCharsets.UTF_8);
        return target;
    }

    private void updateLedger(SensorIngestFileEntity entity, Long attachmentId, String status,
        String errorCode, String errorMessage)
    {
        entity.setAttachmentId(attachmentId);
        entity.setStatus(status);
        entity.setErrorCode(errorCode);
        entity.setErrorMessage(errorMessage == null ? null : errorMessage.substring(0, Math.min(1000, errorMessage.length())));
        entity.setValidatedTime(new Date());
        entity.setUpdateTime(new Date());
        ingestMapper.updateById(entity);
    }

    private void writeErrorAndClose(Socket socket, String code, String message)
    {
        try (Socket client = socket)
        {
            DataOutputStream output = new DataOutputStream(new BufferedOutputStream(client.getOutputStream()));
            writeResult(output, Map.of("status", "ERROR", "errorCode", code, "message", message));
        }
        catch (Exception ignored) { }
    }

    private void writeResult(DataOutputStream output, Map<String, Object> result) throws IOException
    {
        writeLine(output, JSON.toJSONString(result));
    }

    private void writeLine(DataOutputStream output, String value) throws IOException
    {
        output.write((value + "\n").getBytes(StandardCharsets.UTF_8));
        output.flush();
    }

    private String readLine(DataInputStream input, int maxLength) throws IOException
    {
        StringBuilder value = new StringBuilder();
        while (true)
        {
            int character = input.read();
            if (character < 0) throw new IOException("协议行提前结束");
            if (character == '\n') return value.toString();
            if (character != '\r') value.append((char) character);
            if (value.length() > maxLength) throw new IOException("协议行过长");
        }
    }

    private String safeName(String value)
    {
        return value == null ? "unknown" : value.replaceAll("[^A-Za-z0-9._-]", "_");
    }

    private String safeMessage(Exception ex)
    {
        return ex.getMessage() == null ? ex.getClass().getSimpleName() : ex.getMessage();
    }

    @Override
    public synchronized void stop()
    {
        running = false;
        if (serverSocket != null)
        {
            try { serverSocket.close(); } catch (IOException ignored) { }
        }
        workers.shutdown();
        try { workers.awaitTermination(10, TimeUnit.SECONDS); } catch (InterruptedException ex) {
            Thread.currentThread().interrupt();
        }
        log.info("CWRU_MAT_V2 接收服务已停止");
    }

    @Override public boolean isRunning() { return running; }

    public Map<String, Object> healthDetails()
    {
        Map<String, Object> details = new LinkedHashMap<>();
        details.put("port", port);
        details.put("receivedCount", receivedCount.get());
        details.put("acceptedCount", acceptedCount.get());
        details.put("duplicateCount", duplicateCount.get());
        details.put("quarantineCount", quarantineCount.get());
        details.put("failedCount", failedCount.get());
        details.put("queueCount", activeConnections.get());
        return details;
    }
    @Override public boolean isAutoStartup() { return true; }
    @Override public int getPhase() { return Integer.MAX_VALUE; }

    private record Mapping(PhmDeviceEntity device, PhmMeasurePointEntity point,
        PhmDiagnosisBindingEntity binding) { }
}
