"""Alarm hysteresis. The service persists transitions in its batch transaction."""
from dataclasses import dataclass, asdict
from datetime import datetime, timezone
from threading import RLock

@dataclass
class DeviceAlarmState:
    device_id: str
    alarm_level: str = "NORMAL"
    fault_type: str = ""
    fault_count: int = 0
    normal_count: int = 0
    last_update: str = ""
    message: str = ""
    last_collected_at: str | None = None

    def to_dict(self):
        return asdict(self)

class AlarmManager:
    def __init__(self, warning_count=3, alarm_count=5, clear_count=10, max_gap_seconds=60):
        if not 0 < warning_count <= alarm_count or clear_count <= 0 or max_gap_seconds <= 0:
            raise ValueError("invalid alarm thresholds")
        self.warning_count, self.alarm_count = warning_count, alarm_count
        self.clear_count, self.max_gap_seconds = clear_count, max_gap_seconds
        self.devices = {}
        self._lock = RLock()

    def transition(self, previous, result):
        state = DeviceAlarmState(**previous) if previous else DeviceAlarmState(result["device_id"])
        now = result.get("timestamp") or datetime.now(timezone.utc).isoformat()
        if state.last_update:
            gap = (datetime.fromisoformat(now)-datetime.fromisoformat(state.last_update)).total_seconds()
            if gap > self.max_gap_seconds:
                state.fault_count = state.normal_count = 0
        if state.last_collected_at and result.get("collected_at"):
            acquisition_gap = (datetime.fromisoformat(result["collected_at"])
                               - datetime.fromisoformat(state.last_collected_at)).total_seconds()
            if acquisition_gap > self.max_gap_seconds:
                state.fault_count = state.normal_count = 0
        status = result["status"]
        if status == "FAULT":
            fault = result.get("fault_type") or "unknown_fault"
            if fault != state.fault_type:
                state.fault_count = 0
            state.fault_count += 1
            state.normal_count = 0
            state.fault_type = fault
            if state.fault_count >= self.alarm_count:
                state.alarm_level = "ALARM"
            elif state.fault_count >= self.warning_count and state.alarm_level != "ALARM":
                state.alarm_level = "WARNING"
            # A renewed fault must never clear an existing alarm.
            state.message = "故障确认中" if state.alarm_level == "NORMAL" else f"持续检测到{fault}"
        elif status == "HEALTHY":
            state.normal_count += 1
            state.fault_count = 0
            if state.alarm_level == "NORMAL" or state.normal_count >= self.clear_count:
                state.alarm_level = "NORMAL"
                state.fault_type = ""
                state.message = "设备正常"
            else:
                state.message = "恢复确认中，保留报警"
        else:
            # Unknown or bad data breaks consecutive evidence but never clears an alarm.
            state.fault_count = state.normal_count = 0
            state.message = f"诊断不可用：{status}；保留原报警级别"
        state.last_update = now
        if result.get("collected_at"):
            state.last_collected_at = result["collected_at"]
        return state.to_dict()

    def update(self, diagnosis_result):
        """Standalone in-memory helper; use IndustrialDiagnosisService for durable deduplication."""
        device_id = diagnosis_result["device_id"]
        with self._lock:
            state = self.transition(self.devices.get(device_id), diagnosis_result)
            self.devices[device_id] = state
            return dict(state)

    def get_state(self, device_id):
        with self._lock:
            return dict(self.devices.get(device_id) or DeviceAlarmState(device_id).to_dict())

    def reset(self, device_id):
        with self._lock:
            self.devices[device_id] = DeviceAlarmState(device_id).to_dict()
        return {"device_id": device_id, "message": "alarm reset"}
