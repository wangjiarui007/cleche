"""Bounded, serialized diagnosis with transactional persistent alarm transitions."""
import hashlib
import json
import logging
from datetime import datetime, timezone, timedelta
from threading import Lock
from time import perf_counter
import numpy as np
if __package__ and __package__.startswith("diagnosis."):
    from diagnosis.quality.data_quality import DataQualityChecker
else:
    from quality.data_quality import DataQualityChecker
if __package__ and __package__.startswith("diagnosis."):
    from diagnosis.core.diagnose_signal import DiagnosisService
else:
    from core.diagnose_signal import DiagnosisService
if __package__ and __package__.startswith("diagnosis."):
    from diagnosis.alarm.alarm_manager import AlarmManager
else:
    from alarm.alarm_manager import AlarmManager
if __package__ and __package__.startswith("diagnosis."):
    from diagnosis.database.database_manager import DatabaseManager, BatchConflict, ServiceBusy
else:
    from database.database_manager import DatabaseManager, BatchConflict, ServiceBusy

logger = logging.getLogger(__name__)

class IndustrialDiagnosisService:
    def __init__(self, model_service, database_path="diagnosis.db", max_samples=1_600_000,
                 stale_after_seconds=60, max_data_age_seconds=300, clip_threshold=None):
        if max_samples < model_service.win_len or stale_after_seconds <= 0 or max_data_age_seconds <= 0:
            raise ValueError("invalid service limits")
        self.quality_checker = DataQualityChecker(min_length=model_service.win_len, clip_threshold=clip_threshold)
        self.model_service = DiagnosisService(model_service)
        self.alarm_manager = AlarmManager(max_gap_seconds=stale_after_seconds)
        self.database = DatabaseManager(database_path, stale_after_seconds)
        self.max_samples = max_samples
        self.max_data_age_seconds = max_data_age_seconds
        self._lock = Lock()
        self.ready = True

    def run(self, signal, rpm, sampling_rate, device_id, batch_id=None, collected_at=None):
        started = perf_counter()
        if not self.ready:
            raise ServiceBusy("service is not ready")
        if not isinstance(device_id, str) or not device_id.strip() or len(device_id) > 128:
            raise ValueError("device_id must have 1..128 characters")
        if not isinstance(batch_id, str) or not batch_id.strip() or len(batch_id) > 128:
            raise ValueError("batch_id is required and must have 1..128 characters")
        if not np.isfinite(rpm) or rpm <= 0 or not np.isfinite(sampling_rate) or sampling_rate <= 0:
            raise ValueError("rpm and sampling_rate must be finite positive numbers")
        x = np.asarray(signal, dtype=np.float32)
        if x.ndim != 1 or x.size > self.max_samples:
            raise ValueError(f"signal must be one-dimensional with at most {self.max_samples} samples")
        if collected_at is not None:
            if isinstance(collected_at, str):
                collected_at = datetime.fromisoformat(collected_at.replace("Z", "+00:00"))
            if collected_at.tzinfo is None:
                raise ValueError("collected_at must include a timezone")
            collected_at = collected_at.astimezone(timezone.utc).isoformat()
        metadata = json.dumps([float(rpm), float(sampling_rate), collected_at], allow_nan=False)
        fingerprint = hashlib.sha256(metadata.encode()+x.astype("<f4", copy=False).tobytes()).hexdigest()
        if not self._lock.acquire(timeout=2):
            raise ServiceBusy("inference queue is full; retry the same batch_id")
        try:
            def produce(previous):
                now = datetime.now(timezone.utc)
                if collected_at:
                    sample_time = datetime.fromisoformat(collected_at)
                    if sample_time > now + timedelta(seconds=30):
                        raise BatchConflict("collected_at is in the future")
                    if (now-sample_time).total_seconds() > self.max_data_age_seconds:
                        raise BatchConflict("sample is too old; use offline evaluation for historical records")
                    if previous and previous.get("last_collected_at") and collected_at <= previous["last_collected_at"]:
                        raise BatchConflict("out-of-order acquisition timestamp")
                quality = self.quality_checker.check(x, sampling_rate, rpm)
                if not quality.valid:
                    result = dict(status="DATA_INVALID", reason=quality.reasons, fault_type=None, confidence=None)
                else:
                    result = self.model_service.diagnose_signal(x, rpm, sampling_rate, device_id, batch_id)
                result.update(device_id=device_id, batch_id=batch_id, rpm=float(rpm),
                              sampling_rate=float(sampling_rate), timestamp=now.isoformat(),
                              collected_at=collected_at, quality=quality.to_dict(),
                              model_version=self.model_service.ms.model_version)
                alarm = self.alarm_manager.transition(previous, result)
                result.update(alarm_level=alarm["alarm_level"], alarm_message=alarm["message"],
                              latency_ms=round((perf_counter()-started)*1000, 2))
                return result, alarm
            result = self.database.process_batch(device_id, batch_id, fingerprint, produce)
            logger.info("diagnosis device=%s batch=%s status=%s elapsed_ms=%.2f",
                        device_id, batch_id, result["status"], (perf_counter()-started)*1000)
            return result
        finally:
            self._lock.release()

    def close(self):
        with self._lock:
            self.ready = False
            self.database.close()
