# -*- coding: utf-8 -*-
"""V8 推理所需的信号处理工具函数（从 diagnosis/core/utils_signal8.py 提取）。"""

from __future__ import annotations

from fractions import Fraction
from typing import Optional

import numpy as np


def zscore_1d(x: np.ndarray, eps: float = 1e-8) -> np.ndarray:
    x = np.asarray(x, dtype=np.float64)
    if x.size == 0 or not np.isfinite(x).all():
        raise ValueError("normalization requires nonempty finite signal")
    std = float(x.std())
    if std < eps:
        return np.zeros_like(x, dtype=np.float32)
    return ((x - x.mean()) / std).astype(np.float32)


def count_windows(sig_len: int, win_len: int, stride: int) -> int:
    sig_len = int(sig_len)
    win_len = int(win_len)
    stride = int(stride)
    if sig_len <= 0:
        raise ValueError(f"sig_len 必须为正数，实际为 {sig_len}。")
    if win_len <= 0 or stride <= 0:
        raise ValueError(f"win_len 和 stride 必须为正数，实际为 {win_len}/{stride}。")
    if sig_len <= win_len:
        return 1
    return 1 + (sig_len - win_len) // stride


def get_window(sig: np.ndarray, start: int, win_len: int) -> np.ndarray:
    sig = np.asarray(sig, dtype=np.float32).reshape(-1)
    if win_len <= 0:
        raise ValueError(f"win_len 必须为正数，实际为 {win_len}。")
    if sig.size == 0:
        raise ValueError("不能从空信号截取窗口。")
    start = max(0, int(start))
    end = start + int(win_len)

    if len(sig) >= end:
        return sig[start:end].astype(np.float32)

    out = np.zeros(win_len, dtype=np.float32)
    part = sig[start:]
    out[: len(part)] = part
    return out


def resample_signal(signal: np.ndarray, input_fs: float, output_fs: float) -> np.ndarray:
    """用有理数多相滤波把输入信号重采样到模型采样率。"""
    signal = np.asarray(signal, dtype=np.float32).reshape(-1)
    input_fs = float(input_fs)
    output_fs = float(output_fs)
    if input_fs <= 0 or output_fs <= 0:
        raise ValueError(f"采样率必须为正数，实际为 input_fs={input_fs}, output_fs={output_fs}。")
    if np.isclose(input_fs, output_fs, rtol=0.0, atol=1e-9):
        return signal

    import scipy.signal as sg

    ratio = Fraction(output_fs / input_fs).limit_denominator(10_000)
    y = sg.resample_poly(signal, ratio.numerator, ratio.denominator)
    return np.asarray(y, dtype=np.float32)
