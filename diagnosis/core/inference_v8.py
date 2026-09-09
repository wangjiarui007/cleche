# -*- coding: utf-8 -*-
"""训练验证、离线评估和在线诊断共用的推理实现。"""

from __future__ import annotations

import warnings
from pathlib import Path
from typing import Mapping, Optional, Sequence

import numpy as np
import torch

if __package__ and __package__.startswith("diagnosis."):
    from diagnosis.core.decision_v8 import EXPECTED_CLASSES, decide_v8
else:
    from core.decision_v8 import EXPECTED_CLASSES, decide_v8
if __package__ and __package__.startswith("diagnosis."):
    from diagnosis.model.wdcnn_v8_bearing_dg import WDCNNV8DG
else:
    from model.wdcnn_v8_bearing_dg import WDCNNV8DG
if __package__ and __package__.startswith("diagnosis."):
    from diagnosis.core.utils_signal8 import count_windows, get_window, load_signal, resample_signal, zscore_1d
else:
    from core.utils_signal8 import count_windows, get_window, load_signal, resample_signal, zscore_1d


DEFAULT_REJECTION_CONFIG = {
    "min_confidence": 0.60,
    "min_segment_consistency": 0.55,
    "max_normalized_entropy": 0.90,
    "min_segments": 4,
    "require_head_agreement": True,
}


def resolve_device(requested: str | torch.device) -> torch.device:
    requested = str(requested)
    if requested.startswith("cuda") and not torch.cuda.is_available():
        warnings.warn("请求了 CUDA，但当前环境不可用，已回退到 CPU。", RuntimeWarning)
        return torch.device("cpu")
    return torch.device(requested)


def validate_class_order(classes: Sequence[str], where: str = "classes") -> list[str]:
    classes = [str(x) for x in classes]
    if tuple(classes) != EXPECTED_CLASSES:
        raise ValueError(
            f"{where} 必须严格为 {list(EXPECTED_CLASSES)}，实际为 {classes}。"
            "模型的二分类头和类别编号依赖该顺序，不能只改命令行顺序。"
        )
    return classes


def load_checkpoint(path: str | Path, map_location: torch.device) -> dict:
    """安全加载本项目 checkpoint，并验证最低结构要求。"""
    path = Path(path)
    if not path.is_file():
        raise FileNotFoundError(path)
    try:
        checkpoint = torch.load(path, map_location=map_location, weights_only=True)
    except TypeError:  # PyTorch < 2.0
        checkpoint = torch.load(path, map_location=map_location)
    except Exception as exc:
        raise RuntimeError(
            f"无法以安全模式加载 checkpoint: {path}。仅应加载本项目生成且可信的模型文件。"
        ) from exc

    if not isinstance(checkpoint, dict) or not isinstance(checkpoint.get("model_state"), Mapping):
        raise ValueError(f"checkpoint 缺少字典型 model_state: {path}")
    checkpoint["classes"] = validate_class_order(
        checkpoint.get("classes", EXPECTED_CLASSES), where="checkpoint classes"
    )
    return checkpoint


def decision_config_from_checkpoint(checkpoint: Mapping) -> dict:
    config = dict(checkpoint.get("decision_v8") or {})
    if not config:
        return {"mode": "probability_fusion", "binary_weight": 1.0}
    if "mode" not in config:
        # 旧模型明确走旧规则，避免升级代码后同一 checkpoint 悄悄改变结果。
        config["mode"] = "legacy_threshold"
        warnings.warn(
            "检测到旧版 checkpoint 的阈值判决配置，将以 legacy_threshold 复现。"
            "正式新实验请重新训练，使用无类别偏置的 probability_fusion。",
            RuntimeWarning,
        )
    return config


def rejection_config_from_checkpoint(
    checkpoint: Mapping,
    overrides: Optional[Mapping] = None,
) -> dict:
    """Load and validate the online unknown/rejection policy."""
    config = dict(DEFAULT_REJECTION_CONFIG)
    config.update(dict(checkpoint.get("rejection_v8") or {}))
    if overrides:
        config.update({key: value for key, value in overrides.items() if value is not None})

    for key in ("min_confidence", "min_segment_consistency", "max_normalized_entropy"):
        config[key] = float(config[key])
        if not 0.0 <= config[key] <= 1.0:
            raise ValueError(f"{key} 必须在 [0, 1]，实际为 {config[key]}。")
    config["min_segments"] = int(config["min_segments"])
    if config["min_segments"] <= 0:
        raise ValueError("min_segments 必须为正数。")
    config["require_head_agreement"] = bool(config["require_head_agreement"])
    return config


def assess_signal_quality(
    signal: np.ndarray,
    *,
    min_std: float = 1e-8,
    max_zero_fraction: float = 0.98,
    max_flat_step_fraction: float = 0.98,
    max_clipped_fraction: float = 0.05,
) -> dict:
    """Return scale-light signal health metrics and rejection reasons.

    These checks target common acquisition failures (NaN/Inf, unplugged or
    frozen channels, all-zero buffers, and ADC rail clipping).  Absolute
    engineering-unit limits remain acquisition-specific and can be added by
    the caller before invoking the model.
    """
    x = np.asarray(signal, dtype=np.float32).reshape(-1)
    reasons: list[str] = []
    if x.size == 0:
        return {"accepted": False, "reasons": ["empty_signal"], "num_samples": 0}
    if not np.isfinite(x).all():
        return {
            "accepted": False,
            "reasons": ["non_finite_signal"],
            "num_samples": int(x.size),
        }

    for name, value in (
        ("max_zero_fraction", max_zero_fraction),
        ("max_flat_step_fraction", max_flat_step_fraction),
        ("max_clipped_fraction", max_clipped_fraction),
    ):
        if not 0.0 <= float(value) <= 1.0:
            raise ValueError(f"{name} 必须在 [0, 1]。")
    if float(min_std) < 0:
        raise ValueError("min_std 不能为负数。")

    mean = float(x.mean())
    std = float(x.std())
    rms = float(np.sqrt(np.mean(np.square(x.astype(np.float64)))))
    peak = float(np.max(np.abs(x)))
    scale_eps = max(float(min_std), std * 1e-6, np.finfo(np.float32).eps)
    zero_fraction = float(np.mean(np.abs(x) <= scale_eps))
    flat_step_fraction = float(np.mean(np.abs(np.diff(x)) <= scale_eps)) if x.size > 1 else 1.0
    value_range = float(x.max() - x.min())
    rail_tol = max(value_range * 1e-6, np.finfo(np.float32).eps)
    clipped_fraction = float(
        np.mean((x <= float(x.min()) + rail_tol) | (x >= float(x.max()) - rail_tol))
    )

    if std < float(min_std):
        reasons.append("std_below_minimum")
    if zero_fraction > float(max_zero_fraction):
        reasons.append("mostly_zero")
    if flat_step_fraction > float(max_flat_step_fraction):
        reasons.append("frozen_or_flat_channel")
    if clipped_fraction > float(max_clipped_fraction):
        reasons.append("possible_adc_clipping")

    return {
        "accepted": not reasons,
        "reasons": reasons,
        "num_samples": int(x.size),
        "mean": mean,
        "std": std,
        "rms": rms,
        "peak": peak,
        "zero_fraction": zero_fraction,
        "flat_step_fraction": flat_step_fraction,
        "clipped_fraction": clipped_fraction,
    }


def _normalized_entropy(probabilities: np.ndarray) -> float:
    p = np.asarray(probabilities, dtype=np.float64).reshape(-1)
    p = p / p.sum()
    entropy = -np.sum(p * np.log(np.clip(p, np.finfo(np.float64).tiny, 1.0)))
    return float(entropy / np.log(len(p)))


def apply_rejection_v8(result: Mapping, rejection_config: Mapping) -> dict:
    """Attach a deterministic accepted/rejected decision to a raw prediction."""
    config = rejection_config_from_checkpoint({}, rejection_config)
    output = dict(result)
    reasons: list[str] = []
    confidence = float(output["confidence"])
    consistency = float(output["segment_consistency"])
    entropy = float(output.get("normalized_entropy", _normalized_entropy(output["decision_scores"])))
    num_segments = int(output["num_segments"])

    if num_segments < config["min_segments"]:
        reasons.append("insufficient_segments")
    if confidence < config["min_confidence"]:
        reasons.append("low_confidence")
    if consistency < config["min_segment_consistency"]:
        reasons.append("low_segment_consistency")
    if entropy > config["max_normalized_entropy"]:
        reasons.append("high_entropy")

    raw_pred = int(output["pred"])
    direct_group = 0 if int(np.argmax(output["direct_mean"])) == 0 else 1
    binary_group = int(np.argmax(output["binary_mean"]))
    predicted_group = 0 if raw_pred == 0 else 1
    head_agreement = direct_group == binary_group == predicted_group
    if config["require_head_agreement"] and not head_agreement:
        reasons.append("classifier_head_disagreement")

    output["normalized_entropy"] = entropy
    output["head_agreement"] = bool(head_agreement)
    output["accepted"] = not reasons
    output["rejection_reasons"] = reasons
    return output


def aggregate_prediction_results_v8(
    results: Sequence[Mapping],
    decision_kwargs: Mapping,
    segment_vote_threshold: Optional[float] = None,
) -> dict:
    """Aggregate already inferred windows for a rolling online decision."""
    if not results:
        raise ValueError("至少需要一个窗口结果。")
    direct = np.stack([np.asarray(item["direct_mean"], dtype=np.float64) for item in results])
    binary = np.stack([np.asarray(item["binary_mean"], dtype=np.float64) for item in results])
    return _aggregate_segment_probabilities(
        direct,
        binary,
        decision_kwargs=decision_kwargs,
        segment_vote_threshold=segment_vote_threshold,
    )


def build_model_from_checkpoint(checkpoint: Mapping, device: torch.device) -> WDCNNV8DG:
    class_names = validate_class_order(checkpoint.get("classes", EXPECTED_CLASSES), "checkpoint classes")
    fs = float(checkpoint.get("fs", 16000.0))
    win_len = int(checkpoint.get("win_len", 4096))
    num_domains = int(checkpoint.get("num_domains", 2))
    feat_dim = int(checkpoint.get("feat_dim", 256))
    # 旧 checkpoint 未记录该字段，默认启用全部分支，保持严格向后兼容。
    enabled_branches = checkpoint.get("enabled_branches", ("time", "order", "envelope"))
    if "envelope_mode" in checkpoint:
        envelope_mode = str(checkpoint["envelope_mode"])
    else:
        envelope_mode = "abs"
        warnings.warn(
            "旧 checkpoint 未记录 envelope_mode，将保留旧版 abs 近似包络行为。"
            "新训练模型默认使用真正的 Hilbert 包络。",
            RuntimeWarning,
        )
    if fs <= 0 or win_len <= 0 or num_domains <= 0 or feat_dim <= 0:
        raise ValueError("checkpoint 中 fs/win_len/num_domains/feat_dim 必须为正数。")

    model = WDCNNV8DG(
        num_classes=len(class_names),
        num_domains=num_domains,
        fs=fs,
        win_len=win_len,
        feat_dim=feat_dim,
        envelope_mode=envelope_mode,
        enabled_branches=enabled_branches,
    ).to(device)
    model.load_state_dict(checkpoint["model_state"], strict=True)
    model.eval()
    return model


def _aggregate_segment_probabilities(
    direct_values: np.ndarray,
    binary_values: np.ndarray,
    *,
    decision_kwargs: Mapping,
    segment_vote_threshold: Optional[float] = None,
) -> dict:
    direct_values = np.asarray(direct_values, dtype=np.float64)
    binary_values = np.asarray(binary_values, dtype=np.float64)
    if direct_values.ndim != 2 or direct_values.shape[1] != 3:
        raise ValueError(f"direct_values 应为 [N,3]，实际为 {direct_values.shape}。")
    if binary_values.shape != (direct_values.shape[0], 2):
        raise ValueError(f"binary_values 应为 [N,2]，实际为 {binary_values.shape}。")
    if direct_values.shape[0] == 0:
        raise ValueError("不能聚合空窗口集合。")
    if segment_vote_threshold is not None and not 0.5 <= segment_vote_threshold <= 1.0:
        raise ValueError("segment_vote_threshold 必须在 [0.5, 1.0]，或设为 None 禁用。")

    segment_predictions = np.asarray(
        [
            int(decide_v8(direct_one, binary_one, **decision_kwargs)[0])
            for direct_one, binary_one in zip(direct_values, binary_values)
        ],
        dtype=np.int64,
    )
    direct_mean = direct_values.mean(axis=0)
    binary_mean = binary_values.mean(axis=0)
    file_pred, reason, decision_scores = decide_v8(
        direct_mean, binary_mean, return_scores=True, **decision_kwargs
    )

    vote_counts = np.bincount(segment_predictions, minlength=3)
    best_count = int(vote_counts.max())
    tied = np.flatnonzero(vote_counts == best_count)
    vote_pred = int(tied[np.argmax(decision_scores[tied])])
    vote_ratio = float(best_count / len(segment_predictions))

    final_pred = int(file_pred)
    if (
        segment_vote_threshold is not None
        and vote_ratio >= segment_vote_threshold
        and vote_pred != final_pred
    ):
        final_pred = vote_pred
        reason = f"segment_vote_override_{reason}"

    return {
        "pred": final_pred,
        "confidence": float(decision_scores[final_pred]),
        "normalized_entropy": _normalized_entropy(decision_scores),
        "segment_consistency": float((segment_predictions == final_pred).mean()),
        "vote_pred": vote_pred,
        "vote_ratio": vote_ratio,
        "decision_reason": reason,
        "decision_scores": decision_scores,
        "direct_mean": direct_mean,
        "binary_mean": binary_mean,
        "num_segments": int(direct_values.shape[0]),
    }


@torch.no_grad()
def predict_windows_v8(
    model: WDCNNV8DG,
    windows: np.ndarray,
    rpms: float | Sequence[float] | np.ndarray,
    device: torch.device,
    batch_size: int,
    decision_kwargs: Mapping,
) -> list[dict]:
    """Infer independent fixed-length windows efficiently in batches."""
    values = np.asarray(windows, dtype=np.float32)
    if values.ndim == 1:
        values = values.reshape(1, -1)
    if values.ndim != 2 or values.shape[1] != int(model.win_len):
        raise ValueError(f"windows 应为 [N,{model.win_len}]，实际为 {values.shape}。")
    if not np.isfinite(values).all():
        raise ValueError("窗口输入包含 NaN/Inf。")
    if batch_size <= 0:
        raise ValueError("batch_size 必须为正数。")
    rpm_values = np.asarray(rpms, dtype=np.float32).reshape(-1)
    if rpm_values.size == 1:
        rpm_values = np.full(values.shape[0], float(rpm_values[0]), dtype=np.float32)
    if rpm_values.shape != (values.shape[0],):
        raise ValueError("rpms 必须是标量或与窗口数量相同的一维数组。")
    if not np.isfinite(rpm_values).all() or (rpm_values <= 0).any():
        raise ValueError("rpms 必须为有限正数。")

    direct_parts: list[np.ndarray] = []
    binary_parts: list[np.ndarray] = []
    model.eval()
    for start in range(0, len(values), batch_size):
        batch = np.stack([zscore_1d(x) for x in values[start : start + batch_size]])
        xb = torch.from_numpy(batch).float().unsqueeze(1).to(device)
        rb = torch.from_numpy(rpm_values[start : start + len(batch)]).float().to(device)
        output = model(xb, rb, grl_lambda=0.0)
        direct_parts.append(torch.softmax(output["logits"], dim=1).cpu().numpy())
        binary_parts.append(torch.softmax(output["binary_logits"], dim=1).cpu().numpy())

    direct_values = np.concatenate(direct_parts, axis=0)
    binary_values = np.concatenate(binary_parts, axis=0)
    return [
        _aggregate_segment_probabilities(
            direct_values[index : index + 1],
            binary_values[index : index + 1],
            decision_kwargs=decision_kwargs,
        )
        for index in range(len(values))
    ]


@torch.no_grad()
def predict_signal_v8(
    model: WDCNNV8DG,
    signal: np.ndarray,
    rpm: float,
    device: torch.device,
    win_len: int,
    stride: int,
    batch_size: int,
    decision_kwargs: Mapping,
    input_fs: Optional[float] = None,
    model_fs: Optional[float] = None,
    segment_vote_threshold: Optional[float] = None,
) -> dict:
    """对一条完整记录做统一文件级推理。"""
    rpm = float(rpm)
    if not np.isfinite(rpm) or rpm <= 0:
        raise ValueError(f"rpm 必须为有限正数，实际为 {rpm}。")
    if batch_size <= 0:
        raise ValueError(f"batch_size 必须为正数，实际为 {batch_size}。")
    if segment_vote_threshold is not None and not 0.5 <= segment_vote_threshold <= 1.0:
        raise ValueError("segment_vote_threshold 必须在 [0.5, 1.0]，或设为 None 禁用。")

    signal = np.asarray(signal, dtype=np.float32).reshape(-1)
    effective_model_fs = float(model_fs if model_fs is not None else model.fs)
    effective_input_fs = float(input_fs if input_fs is not None else effective_model_fs)
    signal = resample_signal(signal, effective_input_fs, effective_model_fs)
    if len(signal) < win_len:
        raise ValueError(
            f"重采样后信号长度 {len(signal)} 小于 win_len={win_len}，拒绝以大量补零生成诊断。"
        )
    n_win = count_windows(len(signal), win_len, stride)

    direct_all: list[torch.Tensor] = []
    binary_all: list[torch.Tensor] = []
    model.eval()
    for start_idx in range(0, n_win, batch_size):
        xs = [
            zscore_1d(get_window(signal, j * stride, win_len))
            for j in range(start_idx, min(start_idx + batch_size, n_win))
        ]
        xb = torch.from_numpy(np.stack(xs)).float().unsqueeze(1).to(device)
        rb = torch.full((len(xs),), rpm, dtype=torch.float32, device=device)
        output = model(xb, rb, grl_lambda=0.0)
        direct = torch.softmax(output["logits"], dim=1).cpu()
        binary = torch.softmax(output["binary_logits"], dim=1).cpu()
        direct_all.append(direct)
        binary_all.append(binary)

    result = _aggregate_segment_probabilities(
        torch.cat(direct_all, dim=0).numpy(),
        torch.cat(binary_all, dim=0).numpy(),
        decision_kwargs=decision_kwargs,
        segment_vote_threshold=segment_vote_threshold,
    )
    result.update({
        "input_fs": effective_input_fs,
        "model_fs": effective_model_fs,
    })
    return result


def predict_file_v8(
    model: WDCNNV8DG,
    path: str | Path,
    rpm: float,
    device: torch.device,
    win_len: int,
    stride: int,
    signal_key: str,
    column: Optional[int],
    batch_size: int,
    decision_kwargs: Mapping,
    input_fs: Optional[float] = None,
    model_fs: Optional[float] = None,
    segment_vote_threshold: Optional[float] = None,
) -> dict:
    signal = load_signal(path, signal_key=signal_key, column=column)
    return predict_signal_v8(
        model=model,
        signal=signal,
        rpm=rpm,
        device=device,
        win_len=win_len,
        stride=stride,
        batch_size=batch_size,
        decision_kwargs=decision_kwargs,
        input_fs=input_fs,
        model_fs=model_fs,
        segment_vote_threshold=segment_vote_threshold,
    )
