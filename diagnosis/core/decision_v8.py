# -*- coding: utf-8 -*-
"""统一的闭集三分类判决。

默认使用概率融合：direct head 给出三分类概率，binary head 给出
healthy/fault 概率，两者按乘积专家模型在对数域融合。该规则对两个故障
类别完全对称，不再使用针对 OR 的人工优先规则。

``legacy_threshold`` 仅用于复现旧 v8 checkpoint；新实验不建议使用，
因为其中的类别特定 tie-break 容易造成系统性偏置。
"""

from __future__ import annotations

from typing import Tuple

import numpy as np


EXPECTED_CLASSES = ("healthy", "outer_ring", "rolling_element")


def _as_probability(x, size: int, name: str) -> np.ndarray:
    p = np.asarray(x, dtype=np.float64).reshape(-1)
    if p.shape != (size,):
        raise ValueError(f"{name} 应包含 {size} 个概率，实际 shape={p.shape}。")
    if not np.isfinite(p).all() or (p < 0).any():
        raise ValueError(f"{name} 必须是有限的非负数。")
    total = float(p.sum())
    if total <= 0:
        raise ValueError(f"{name} 的概率和必须大于 0。")
    return p / total


def _probability_fusion(d: np.ndarray, b: np.ndarray, binary_weight: float) -> np.ndarray:
    if binary_weight < 0:
        raise ValueError(f"binary_weight 必须非负，实际为 {binary_weight}。")
    eps = np.finfo(np.float64).tiny
    group_prob = np.asarray([b[0], b[1], b[1]], dtype=np.float64)
    log_score = np.log(np.clip(d, eps, 1.0)) + binary_weight * np.log(np.clip(group_prob, eps, 1.0))
    log_score -= log_score.max()
    score = np.exp(log_score)
    return score / score.sum()


def _legacy_threshold_decision(
    d: np.ndarray,
    b: np.ndarray,
    healthy_accept_thr: float,
    fault_accept_thr: float,
    gray_direct_fault_thr: float,
    min_fault_gap: float,
    or_tie_delta: float,
    or_min_prob: float,
) -> Tuple[int, str]:
    p_h, p_or, p_b = d.tolist()
    p_bin_h, p_bin_f = b.tolist()
    direct_id = int(np.argmax(d))

    if p_or >= or_min_prob and p_b > p_or and (p_b - p_or) <= or_tie_delta:
        best_fault_id, best_fault_prob, tie_used = 1, p_or, True
    else:
        best_fault_id = 1 if p_or >= p_b else 2
        best_fault_prob = max(p_or, p_b)
        tie_used = False

    if p_bin_h >= healthy_accept_thr and best_fault_prob < gray_direct_fault_thr:
        return 0, "legacy_strong_healthy_protection"
    if p_bin_f >= fault_accept_thr:
        reason = "legacy_fault_gate_or_tie" if tie_used else "legacy_fault_gate"
        return best_fault_id, reason
    if direct_id == 0:
        if best_fault_prob >= gray_direct_fault_thr and best_fault_prob > p_h + min_fault_gap:
            reason = "legacy_gray_fault_or_tie" if tie_used else "legacy_gray_fault"
            return best_fault_id, reason
        return 0, "legacy_gray_healthy"
    if best_fault_prob >= gray_direct_fault_thr:
        reason = "legacy_direct_fault_or_tie" if tie_used else "legacy_direct_fault"
        return best_fault_id, reason
    return 0, "legacy_low_fault_default_healthy"


def decide_v8(
    direct_prob,
    binary_prob,
    mode: str = "probability_fusion",
    binary_weight: float = 1.0,
    healthy_accept_thr: float = 0.78,
    fault_accept_thr: float = 0.55,
    gray_direct_fault_thr: float = 0.38,
    min_fault_gap: float = 0.03,
    or_tie_delta: float = 0.06,
    or_min_prob: float = 0.22,
    return_scores: bool = False,
):
    """返回 ``(类别编号, 原因)``；可选返回第三项融合概率。"""
    d = _as_probability(direct_prob, 3, "direct_prob")
    b = _as_probability(binary_prob, 2, "binary_prob")
    mode = str(mode).strip().lower()

    fused = _probability_fusion(d, b, float(binary_weight))
    if mode == "probability_fusion":
        pred = int(np.argmax(fused))
        reason = "probability_fusion"
    elif mode == "direct":
        fused = d
        pred = int(np.argmax(d))
        reason = "direct_head"
    elif mode == "legacy_threshold":
        pred, reason = _legacy_threshold_decision(
            d,
            b,
            healthy_accept_thr=float(healthy_accept_thr),
            fault_accept_thr=float(fault_accept_thr),
            gray_direct_fault_thr=float(gray_direct_fault_thr),
            min_fault_gap=float(min_fault_gap),
            or_tie_delta=float(or_tie_delta),
            or_min_prob=float(or_min_prob),
        )
    else:
        raise ValueError(
            f"未知 decision mode={mode!r}，应为 probability_fusion/direct/legacy_threshold。"
        )

    if return_scores:
        return pred, reason, fused.astype(np.float64, copy=False)
    return pred, reason
