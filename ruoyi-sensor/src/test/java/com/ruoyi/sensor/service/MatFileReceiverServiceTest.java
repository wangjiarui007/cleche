package com.ruoyi.sensor.service;

import static org.assertj.core.api.Assertions.assertThat;
import static org.assertj.core.api.Assertions.assertThatThrownBy;
import static org.mockito.ArgumentMatchers.any;
import static org.mockito.Mockito.mock;
import static org.mockito.Mockito.when;

import com.baomidou.mybatisplus.core.conditions.query.LambdaQueryWrapper;
import com.ruoyi.sensor.domain.dto.MatFileProtocolHeader;
import com.ruoyi.sensor.domain.entity.PhmAcquisitionChannelEntity;
import com.ruoyi.sensor.domain.entity.PhmDiagnosisBindingEntity;
import com.ruoyi.sensor.domain.entity.PhmDeviceEntity;
import com.ruoyi.sensor.domain.entity.PhmMeasurePointEntity;
import com.ruoyi.sensor.mapper.PhmAcquisitionChannelMapper;
import com.ruoyi.sensor.mapper.PhmDeviceMapper;
import com.ruoyi.sensor.mapper.PhmDiagnosisBindingMapper;
import com.ruoyi.sensor.mapper.PhmMeasurePointMapper;
import java.lang.reflect.Field;
import java.lang.reflect.InvocationTargetException;
import java.lang.reflect.Method;
import java.util.List;
import org.junit.jupiter.api.BeforeEach;
import org.junit.jupiter.api.Test;
import org.springframework.test.util.ReflectionTestUtils;

/**
 * Verifies the device-point-channel mapping of a MAT V2 header, in particular
 * that the protocol channel id ({@link Integer}) is compared by value against
 * the persisted binding channel id ({@link Long}).
 */
class MatFileReceiverServiceTest
{
    private MatFileReceiverService service;
    private PhmDeviceMapper deviceMapper;
    private PhmMeasurePointMapper pointMapper;
    private PhmAcquisitionChannelMapper channelMapper;
    private PhmDiagnosisBindingMapper bindingMapper;

    @BeforeEach
    void setUp()
    {
        deviceMapper = mock(PhmDeviceMapper.class);
        pointMapper = mock(PhmMeasurePointMapper.class);
        channelMapper = mock(PhmAcquisitionChannelMapper.class);
        bindingMapper = mock(PhmDiagnosisBindingMapper.class);

        service = mock(MatFileReceiverService.class);
        ReflectionTestUtils.setField(service, "deviceMapper", deviceMapper);
        ReflectionTestUtils.setField(service, "pointMapper", pointMapper);
        ReflectionTestUtils.setField(service, "channelMapper", channelMapper);
        ReflectionTestUtils.setField(service, "bindingMapper", bindingMapper);
    }

    @Test
    void acceptsBindingWhenProtocolChannelMatchesPersistedLongChannel() throws Exception
    {
        stubMappings(List.of(bindingWithChannel(3L)));
        MatFileProtocolHeader header = header();
        header.setChannelId(3);
        Object mapping = invokeResolveMapping(header);

        assertThat(get(mapping, "device", "deviceCode")).isEqualTo("MOTOR-01");
        assertThat(get(mapping, "device", "id")).isEqualTo(1L);
    }

    @Test
    void rejectsBindingWhenProtocolChannelDiffersFromPersistedLongChannel()
    {
        stubMappings(List.of(bindingWithChannel(4L)));
        MatFileProtocolHeader header = header();
        header.setChannelId(3);

        assertThatThrownBy(() -> invokeResolveMappingChecked(header))
            .hasMessage("诊断模型绑定与设备、测点或物理通道不一致");
    }

    @Test
    void rejectsBindingWhenNoEnabledModelBound()
    {
        stubMappings(List.of());
        assertThatThrownBy(() -> invokeResolveMappingChecked(header()))
            .hasMessage("测点未绑定启用的诊断模型");
    }

    private void stubMappings(List<PhmDiagnosisBindingEntity> bindings)
    {
        when(deviceMapper.selectOne(any())).thenReturn(device());
        when(pointMapper.selectOne(any())).thenReturn(point());
        when(channelMapper.selectOne(any())).thenReturn(channel());
        when(bindingMapper.selectList(any())).thenReturn(bindings);
    }

    private Object invokeResolveMapping(MatFileProtocolHeader header) throws Exception
    {
        Method method = MatFileReceiverService.class.getDeclaredMethod("resolveMapping", MatFileProtocolHeader.class);
        method.setAccessible(true);
        return method.invoke(service, header);
    }

    private void invokeResolveMappingChecked(MatFileProtocolHeader header)
    {
        try
        {
            invokeResolveMapping(header);
        }
        catch (InvocationTargetException e)
        {
            Throwable cause = e.getCause();
            if (cause instanceof RuntimeException re)
            {
                throw re;
            }
            throw new RuntimeException(cause);
        }
        catch (Exception e)
        {
            throw new RuntimeException(e);
        }
    }

    private MatFileProtocolHeader header()
    {
        MatFileProtocolHeader header = new MatFileProtocolHeader();
        header.setDeviceCode("MOTOR-01");
        header.setPointCode("DE-VIB");
        header.setChannelId(3);
        return header;
    }

    private static Object get(Object target, String... fields) throws Exception
    {
        Object current = target;
        for (String fieldName : fields)
        {
            Field field = current.getClass().getDeclaredField(fieldName);
            field.setAccessible(true);
            current = field.get(current);
        }
        return current;
    }

    private PhmDeviceEntity device()
    {
        PhmDeviceEntity device = new PhmDeviceEntity();
        device.setId(1L);
        device.setDeviceCode("MOTOR-01");
        return device;
    }

    private PhmMeasurePointEntity point()
    {
        PhmMeasurePointEntity point = new PhmMeasurePointEntity();
        point.setId(20L);
        point.setDeviceId(1L);
        point.setPointCode("DE-VIB");
        point.setChannelId(3);
        return point;
    }

    private PhmAcquisitionChannelEntity channel()
    {
        PhmAcquisitionChannelEntity channel = new PhmAcquisitionChannelEntity();
        channel.setId(30L);
        channel.setDeviceId(1L);
        channel.setPointId(20L);
        channel.setChannelNo(3);
        return channel;
    }

    private PhmDiagnosisBindingEntity bindingWithChannel(long channelId)
    {
        PhmDiagnosisBindingEntity binding = new PhmDiagnosisBindingEntity();
        binding.setDeviceId(1L);
        binding.setDeviceCode("MOTOR-01");
        binding.setChannelId(channelId);
        return binding;
    }
}
