"""Load model architecture, weights and calibrated inference policy together."""
import hashlib
from pathlib import Path
import numpy as np
if __package__ and (__package__ == "diagnosis" or __package__.startswith("diagnosis.")):
    from ..core.inference_v8 import (
        build_model_from_checkpoint, decision_config_from_checkpoint, load_checkpoint,
        rejection_config_from_checkpoint, resolve_device,
    )
else:
    from core.inference_v8 import (
        build_model_from_checkpoint, decision_config_from_checkpoint, load_checkpoint,
        rejection_config_from_checkpoint, resolve_device,
    )

class ModelService:
    def __init__(self, checkpoint_path, device="cuda", batch_size=32):
        self.device = resolve_device(device)
        checkpoint_path = Path(checkpoint_path)
        checkpoint = load_checkpoint(checkpoint_path, self.device)
        self.classes = checkpoint["classes"]
        self.fs = float(checkpoint.get("fs", 16000))
        self.win_len = int(checkpoint.get("win_len", 4096))
        self.stride = int(checkpoint.get("stride", self.win_len))
        self.batch_size = int(batch_size)
        if self.stride <= 0 or self.batch_size <= 0:
            raise ValueError("stride and batch_size must be positive")
        self.decision_config = decision_config_from_checkpoint(checkpoint)
        self.rejection_config = rejection_config_from_checkpoint(checkpoint)
        self.rpm_range = checkpoint.get("supported_rpm_range")
        if (self.rpm_range is None or len(self.rpm_range) != 2
                or not np.isfinite(self.rpm_range).all()
                or not 0 < self.rpm_range[0] < self.rpm_range[1]):
            raise ValueError("checkpoint must declare a valid supported_rpm_range")
        self.model = build_model_from_checkpoint(checkpoint, self.device)
        digest = hashlib.sha256()
        with checkpoint_path.open("rb") as handle:
            for chunk in iter(lambda: handle.read(1024 * 1024), b""):
                digest.update(chunk)
        self.model_version = digest.hexdigest()
