"""Online adapter around the common batched inference and rejection policy."""
from time import perf_counter
import numpy as np
if __package__ and __package__.startswith("diagnosis."):
    from diagnosis.core.inference_v8 import predict_signal_v8, apply_rejection_v8
else:
    from core.inference_v8 import predict_signal_v8, apply_rejection_v8

class DiagnosisService:
    def __init__(self, model_service):
        self.ms = model_service

    def diagnose_signal(self, signal, rpm, input_fs, device_id=None, batch_id=None):
        started = perf_counter()
        result = dict(device_id=device_id, batch_id=batch_id, rpm=rpm,
                      sampling_rate=input_fs, model_version=self.ms.model_version,
                      fault_type=None, confidence=None, reason=[])
        x = np.asarray(signal, dtype=np.float32)
        if x.ndim != 1 or not x.size or not np.isfinite(x).all():
            return dict(result, status="DATA_INVALID", reason=["invalid_signal"])
        if not np.isfinite(rpm) or rpm <= 0:
            return dict(result, status="DATA_INVALID", reason=["invalid_rpm"])
        if input_fs != self.ms.fs:
            return dict(result, status="FS_ERROR", expected_fs=self.ms.fs,
                        reason=["sampling_rate_mismatch"])
        if not self.ms.rpm_range[0] <= rpm <= self.ms.rpm_range[1]:
            return dict(result, status="OUT_OF_RANGE", reason=["unsupported_rpm"],
                        supported_rpm_range=list(self.ms.rpm_range))
        if len(x) < self.ms.win_len:
            return dict(result, status="DATA_INVALID", reason=["signal_too_short"])
        raw = predict_signal_v8(
            model=self.ms.model, signal=x, rpm=rpm, device=self.ms.device,
            win_len=self.ms.win_len, stride=self.ms.stride,
            batch_size=self.ms.batch_size, decision_kwargs=self.ms.decision_config,
            input_fs=input_fs, model_fs=self.ms.fs,
        )
        prediction = apply_rejection_v8(raw, self.ms.rejection_config)
        pred = prediction["pred"]
        accepted = prediction["accepted"]
        result.update(
            status=("HEALTHY" if pred == 0 else "FAULT") if accepted else "UNCERTAIN",
            fault_type=self.ms.classes[pred] if accepted else None,
            candidate_fault_type=self.ms.classes[pred],
            confidence=prediction["confidence"],
            segment_consistency=prediction["segment_consistency"],
            normalized_entropy=prediction["normalized_entropy"],
            head_agreement=prediction["head_agreement"],
            segments=prediction["num_segments"], reason=prediction["rejection_reasons"],
            probability=dict(zip(self.ms.classes, map(float, prediction["decision_scores"]))),
            direct_probability=dict(zip(self.ms.classes, map(float, prediction["direct_mean"]))),
            binary_fault_probability=float(prediction["binary_mean"][1]),
            decision_reason=prediction["decision_reason"],
            latency_ms=round((perf_counter()-started)*1000, 2),
        )
        return result
