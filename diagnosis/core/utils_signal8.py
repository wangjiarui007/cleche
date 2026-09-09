# -*- coding: utf-8 -*-
"""
utils_signal.py

自建轴承 .mat 数据读取与信号处理工具。

当前数据集建议：
    signal_key = DE_time
    fs = 16000 Hz

支持：
    .mat / .npy / .npz / .csv / .txt / .xlsx
    普通 MATLAB .mat 与 v7.3 .mat
    单通道、多通道 column 选择
"""

from __future__ import annotations

import re
from pathlib import Path
from fractions import Fraction
from typing import Dict, List, Optional, Sequence, Tuple

import numpy as np


def _to_1d_signal(arr, column: Optional[int] = None) -> np.ndarray:
    x = np.asarray(arr)
    x = np.squeeze(x)

    if x.ndim == 0:
        raise ValueError("读取到的是标量，不是振动信号。")

    if np.iscomplexobj(x):
        if not np.allclose(x.imag, 0.0):
            raise ValueError("振动信号包含非零虚部，不能按实数时域信号处理。")
        x = x.real

    if x.ndim == 1:
        sig = x
    elif x.ndim == 2:
        # 默认把较短的一维视为通道维。显式 column 始终在通道维取值，
        # 不再在越界时悄悄切换方向。
        channel_axis = 1 if x.shape[0] >= x.shape[1] else 0
        if column is not None:
            column = int(column)
            n_channels = x.shape[channel_axis]
            if not 0 <= column < n_channels:
                raise IndexError(
                    f"column={column} 越界：推断通道数为 {n_channels}，原始 shape={x.shape}。"
                )
            sig = x[:, column] if channel_axis == 1 else x[column, :]
        else:
            sig = x[:, 0] if channel_axis == 1 else x[0, :]
    else:
        raise ValueError(
            f"读取到 {x.ndim} 维数组（shape={x.shape}），无法可靠判断采样轴和通道轴。"
        )

    sig = np.asarray(sig, dtype=np.float32).reshape(-1)
    if sig.size == 0:
        raise ValueError("读取到空振动信号。")
    if not np.isfinite(sig).all():
        bad = int((~np.isfinite(sig)).sum())
        raise ValueError(f"振动信号包含 {bad} 个 NaN/Inf，已拒绝静默替换。")
    return sig


def _load_mat_scipy(path: Path) -> Dict[str, np.ndarray]:
    import scipy.io as sio
    obj = sio.loadmat(str(path))
    out = {}
    for k, v in obj.items():
        if not k.startswith("__"):
            out[k] = v
    return out


def _load_mat_h5py(path: Path) -> Dict[str, np.ndarray]:
    import h5py
    out = {}
    with h5py.File(str(path), "r") as f:
        def visit(name, obj):
            if hasattr(obj, "shape"):
                try:
                    arr = np.array(obj)
                    if arr.ndim >= 2:
                        arr = arr.T
                    out[name.split("/")[-1]] = arr
                    out[name] = arr
                except Exception:
                    pass
        f.visititems(visit)
    return out


def inspect_mat_keys(path: str | Path) -> List[Tuple[str, Tuple[int, ...], str]]:
    path = Path(path)
    try:
        data = _load_mat_scipy(path)
    except Exception as scipy_exc:
        try:
            data = _load_mat_h5py(path)
        except Exception as h5_exc:
            raise RuntimeError(
                f"MAT 文件既不能由 scipy 读取，也不能由 h5py 读取: {path}; "
                f"scipy={scipy_exc}; h5py={h5_exc}"
            ) from scipy_exc

    rows = []
    for k, v in data.items():
        arr = np.asarray(v)
        rows.append((k, tuple(arr.shape), str(arr.dtype)))
    return rows


def load_signal(
    path: str | Path,
    signal_key: Optional[str] = "DE_time",
    column: Optional[int] = None,
    allow_key_fallback: bool = False,
    allow_pickle: bool = False,
) -> np.ndarray:
    path = Path(path)
    if not path.is_file():
        raise FileNotFoundError(path)
    suffix = path.suffix.lower()

    if suffix == ".mat":
        try:
            data = _load_mat_scipy(path)
        except Exception as scipy_exc:
            try:
                data = _load_mat_h5py(path)
            except Exception as h5_exc:
                raise RuntimeError(
                    f"MAT 文件读取失败: {path}; scipy={scipy_exc}; h5py={h5_exc}"
                ) from scipy_exc

        if not data:
            raise RuntimeError(f"无法从 mat 文件读取任何变量: {path}")

        if signal_key and signal_key in data:
            return _to_1d_signal(data[signal_key], column=column)

        if signal_key:
            for k in data:
                if k.lower() == signal_key.lower():
                    return _to_1d_signal(data[k], column=column)

        if signal_key and not allow_key_fallback:
            available = sorted(str(k) for k in data.keys())
            raise KeyError(
                f"{path} 中找不到 signal_key={signal_key!r}。可用变量: {available}。"
                "请先运行 00_inspect_mat_file.py；若确需自动选择，显式设置 allow_key_fallback=True。"
            )

        candidates = []
        for k, v in data.items():
            arr = np.asarray(v)
            if not np.issubdtype(arr.dtype, np.number):
                continue
            score = int(np.prod(arr.shape))
            lower = k.lower()
            if lower in {"sample_time", "time", "t"}:
                score -= 20_000_000
            elif "time" in lower and "de_time" not in lower:
                score -= 10_000_000
            candidates.append((score, k, arr))

        if not candidates:
            raise KeyError(f"{path} 中找不到数值型变量。已有变量: {list(data.keys())}")

        candidates.sort(reverse=True, key=lambda x: x[0])
        chosen_key = candidates[0][1]
        print(f"[警告] {path.name} 自动使用数值变量: {chosen_key}")
        return _to_1d_signal(data[chosen_key], column=column)

    if suffix == ".npy":
        obj = np.load(path, allow_pickle=allow_pickle)
        if isinstance(obj, np.ndarray) and obj.shape == ():
            obj = obj.item()
        if isinstance(obj, dict):
            if signal_key and signal_key in obj:
                return _to_1d_signal(obj[signal_key], column=column)
            if not obj:
                raise ValueError(f"空字典 npy 文件: {path}")
            if signal_key and not allow_key_fallback:
                raise KeyError(f"{path} 中找不到 signal_key={signal_key!r}。可用变量: {list(obj)}")
            k = max(obj.keys(), key=lambda kk: np.asarray(obj[kk]).size)
            return _to_1d_signal(obj[k], column=column)
        return _to_1d_signal(obj, column=column)

    if suffix == ".npz":
        with np.load(path, allow_pickle=allow_pickle) as obj:
            keys = list(obj.keys())
            if not keys:
                raise ValueError(f"空 npz 文件: {path}")
            if signal_key and signal_key in keys:
                return _to_1d_signal(obj[signal_key], column=column)
            if signal_key and not allow_key_fallback:
                raise KeyError(f"{path} 中找不到 signal_key={signal_key!r}。可用变量: {keys}")
            k = max(keys, key=lambda kk: np.asarray(obj[kk]).size)
            return _to_1d_signal(obj[k], column=column)

    if suffix in [".csv", ".txt"]:
        arr = np.loadtxt(path, delimiter="," if suffix == ".csv" else None)
        return _to_1d_signal(arr, column=column)

    if suffix in [".xlsx", ".xls"]:
        import pandas as pd
        arr = pd.read_excel(path).values
        return _to_1d_signal(arr, column=column)

    raise ValueError(f"不支持的文件类型: {path}")


def _metadata_scalar(value) -> Optional[float]:
    """Best-effort conversion of a MAT metadata value to one finite float."""
    if value is None:
        return None
    arr = np.asarray(value).squeeze()
    if arr.size != 1:
        return None
    try:
        result = float(arr.reshape(-1)[0])
    except (TypeError, ValueError):
        return None
    return result if np.isfinite(result) else None


def load_signal_metadata(path: str | Path) -> Dict[str, float]:
    """Read trusted scalar acquisition metadata without loading the full signal.

    The portable-rotor MAT files use ``rpm``, ``sample_rate``/``sr``, and
    acquisition-overrun flags. Missing metadata is not fabricated here;
    callers must choose an explicit fallback.
    """
    path = Path(path)
    if not path.is_file() or path.suffix.lower() != ".mat":
        return {}

    names = ("rpm", "sample_rate", "sr", "hardware_overrun", "buffer_overrun")
    raw: Dict[str, object] = {}
    try:
        import scipy.io as sio

        raw = sio.loadmat(str(path), variable_names=list(names))
    except NotImplementedError:
        # MATLAB v7.3 files are HDF5 containers.
        try:
            import h5py

            with h5py.File(str(path), "r") as handle:
                for name in names:
                    if name in handle:
                        raw[name] = np.asarray(handle[name])
        except Exception as exc:
            raise RuntimeError(f"无法读取 MAT 采集元数据: {path}: {exc}") from exc
    except Exception as exc:
        raise RuntimeError(f"无法读取 MAT 采集元数据: {path}: {exc}") from exc

    result: Dict[str, float] = {}
    rpm = _metadata_scalar(raw.get("rpm"))
    sample_rate = _metadata_scalar(raw.get("sample_rate"))
    if sample_rate is None:
        sample_rate = _metadata_scalar(raw.get("sr"))
    if rpm is not None:
        result["rpm"] = rpm
    if sample_rate is not None:
        result["sample_rate"] = sample_rate
    for name in ("hardware_overrun", "buffer_overrun"):
        value = _metadata_scalar(raw.get(name))
        if value is not None:
            result[name] = value
    return result


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


def get_scaled_window(sig: np.ndarray, start: int, win_len: int, scale: float = 1.0) -> np.ndarray:
    """
    轻量转速扰动增强窗口。

    scale > 1:
        取更长片段再压缩到 win_len，等效频率升高；
    scale < 1:
        取更短片段再拉伸到 win_len，等效频率降低。
    """
    scale = float(scale)
    if scale <= 0:
        raise ValueError(f"scale 必须为正数，实际为 {scale}。")
    if abs(scale - 1.0) < 1e-6:
        return get_window(sig, start, win_len)

    import scipy.signal as sg

    raw_len = max(32, int(round(win_len * scale)))
    sig_len = len(np.asarray(sig).reshape(-1))
    if sig_len < raw_len:
        raise ValueError(
            f"信号长度 {sig_len} 小于增强所需片段 {raw_len}；请缩小 rpm_aug_max。"
        )
    # 最后几个窗口在 scale>1 时需要更长原始片段；向前平移而不是尾部补零。
    safe_start = min(max(0, int(start)), sig_len - raw_len)
    raw = get_window(sig, safe_start, raw_len)
    y = sg.resample(raw, win_len).astype(np.float32)
    return y


def parse_rpm_from_text(
    text: str,
    default: Optional[float] = None,
    *,
    allow_generic_number: bool = False,
) -> Optional[float]:
    s = str(text).lower()
    # Support both common conventions: ``1600rpm`` and ``rpm1600``.
    # Explicit RPM markers must be checked before the generic numeric fallback;
    # filenames often also contain sampling-rate tokens such as ``sr16000``.
    rpm_patterns = (
        r"(?<![\d.])(\d+(?:\.\d+)?)\s*[-_]?\s*rpm(?![a-z0-9])",
        r"(?<![a-z0-9])rpm\s*[-_]?\s*(\d+(?:\.\d+)?)(?![\d.])",
    )
    for pattern in rpm_patterns:
        m = re.search(pattern, s)
        if m:
            return float(m.group(1))

    # Generic numbers in acquisition paths are commonly timestamps, sample
    # rates, channel numbers, or experiment IDs.  Treating one as rpm silently
    # corrupts the order spectrum, so this legacy fallback is opt-in only.
    if allow_generic_number:
        nums = re.findall(r"\d+(?:\.\d+)?", s)
        for n in nums:
            v = float(n)
            if 100 <= v <= 20000:
                return v
    return default


def list_files_by_exts(root: str | Path, file_exts: Sequence[str]) -> List[Path]:
    root = Path(root)
    if not root.is_dir():
        return []
    exts = [e.lower() if str(e).startswith(".") else "." + str(e).lower() for e in file_exts]
    files = []
    for p in root.rglob("*"):
        if p.is_file() and p.suffix.lower() in exts:
            files.append(p)
    return sorted(files)


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


def top_fft_peaks(
    signal: np.ndarray,
    fs: float = 16000.0,
    n_peaks: int = 8,
    f_min: float = 1.0,
    f_max: Optional[float] = None,
) -> str:
    signal = np.asarray(signal, dtype=np.float32).reshape(-1)
    if fs <= 0:
        raise ValueError(f"fs 必须为正数，实际为 {fs}。")
    signal = signal - signal.mean()

    if len(signal) < 32:
        return ""

    win = np.hanning(len(signal)).astype(np.float32)
    spec = np.abs(np.fft.rfft(signal * win))
    freqs = np.fft.rfftfreq(len(signal), d=1.0 / fs)

    mask = freqs >= f_min
    if f_max is not None:
        mask &= freqs <= f_max

    freqs = freqs[mask]
    spec = spec[mask]

    if len(spec) == 0:
        return ""

    idx = np.argsort(spec)[-n_peaks:][::-1]
    return "; ".join([f"{freqs[i]:.2f}Hz" for i in idx])
