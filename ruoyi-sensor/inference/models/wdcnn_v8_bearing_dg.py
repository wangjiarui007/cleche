# -*- coding: utf-8 -*-
"""
models/wdcnn_v8_bearing_dg.py

v8 模型：
    WDCNN 时域分支
  + 阶次谱分支
  + Hilbert 包络阶次谱分支
  + direct 三分类头
  + healthy/fault 二分类头
  + domain discriminator

注意：
    v8 不使用 fault_sub 分支。
    重点通过训练损失强化 outer_ring 与 rolling_element 的边界：
        - outer_ring 类别权重
        - OR-vs-B margin loss
        - center loss 中 OR/B 中心分离
"""

from __future__ import annotations

from typing import Sequence

import torch
import torch.nn as nn
from torch.autograd import Function


VALID_FEATURE_BRANCHES = ("time", "order", "envelope")


def normalize_enabled_branches(branches: str | Sequence[str]) -> tuple[str, ...]:
    """校验并规范化消融实验使用的特征分支列表。"""
    if isinstance(branches, str):
        requested = [x.strip().lower() for x in branches.split(",") if x.strip()]
    else:
        requested = [str(x).strip().lower() for x in branches if str(x).strip()]

    unknown = sorted(set(requested) - set(VALID_FEATURE_BRANCHES))
    if unknown:
        raise ValueError(
            f"未知特征分支 {unknown}；可用分支为 {list(VALID_FEATURE_BRANCHES)}。"
        )
    if not requested:
        raise ValueError("至少需要启用一个特征分支。")
    if len(requested) != len(set(requested)):
        raise ValueError(f"特征分支不能重复: {requested}")
    return tuple(x for x in VALID_FEATURE_BRANCHES if x in requested)


class GradientReverseFunction(Function):
    @staticmethod
    def forward(ctx, x, lambd):
        ctx.lambd = float(lambd)
        return x.view_as(x)

    @staticmethod
    def backward(ctx, grad_output):
        return -ctx.lambd * grad_output, None


def grad_reverse(x, lambd=1.0):
    return GradientReverseFunction.apply(x, lambd)


class WDCNNFeature(nn.Module):
    def __init__(self, out_dim=128):
        super().__init__()

        self.net = nn.Sequential(
            nn.Conv1d(1, 16, kernel_size=64, stride=16, padding=24, bias=False),
            nn.BatchNorm1d(16),
            nn.ReLU(inplace=True),
            nn.MaxPool1d(2, 2),

            nn.Conv1d(16, 32, kernel_size=3, stride=1, padding=1, bias=False),
            nn.BatchNorm1d(32),
            nn.ReLU(inplace=True),
            nn.MaxPool1d(2, 2),

            nn.Conv1d(32, 64, kernel_size=3, stride=1, padding=1, bias=False),
            nn.BatchNorm1d(64),
            nn.ReLU(inplace=True),
            nn.MaxPool1d(2, 2),

            nn.Conv1d(64, 64, kernel_size=3, stride=1, padding=1, bias=False),
            nn.BatchNorm1d(64),
            nn.ReLU(inplace=True),
            nn.MaxPool1d(2, 2),

            nn.Conv1d(64, 128, kernel_size=3, stride=1, padding=1, bias=False),
            nn.BatchNorm1d(128),
            nn.ReLU(inplace=True),

            nn.AdaptiveAvgPool1d(1),
        )

        self.fc = nn.Sequential(
            nn.Flatten(),
            nn.Linear(128, out_dim),
            nn.BatchNorm1d(out_dim),
            nn.ReLU(inplace=True),
        )

    def forward(self, x):
        return self.fc(self.net(x))


class OrderSpectrumFeature(nn.Module):
    def __init__(
        self,
        fs: float = 16000.0,
        win_len: int = 4096,
        max_order: float = 80.0,
        num_order_bins: int = 160,
        out_dim: int = 64,
        use_abs_envelope: bool = False,
        envelope_mode: str | None = None,
    ):
        super().__init__()

        self.fs = float(fs)
        self.win_len = int(win_len)
        self.max_order = float(max_order)
        self.num_order_bins = int(num_order_bins)
        if envelope_mode is None:
            envelope_mode = "abs" if use_abs_envelope else "none"
        self.envelope_mode = str(envelope_mode).lower()
        if self.envelope_mode not in {"none", "abs", "hilbert"}:
            raise ValueError("envelope_mode 应为 none/abs/hilbert。")

        freqs = torch.fft.rfftfreq(self.win_len, d=1.0 / self.fs)
        self.register_buffer("freqs", freqs)

        order_centers = torch.linspace(0.5, self.max_order, self.num_order_bins)
        self.register_buffer("order_centers", order_centers)

        self.mlp = nn.Sequential(
            nn.Linear(num_order_bins, 128),
            nn.BatchNorm1d(128),
            nn.ReLU(inplace=True),
            nn.Dropout(0.1),

            nn.Linear(128, out_dim),
            nn.BatchNorm1d(out_dim),
            nn.ReLU(inplace=True),
        )

    def _order_pool_one(self, mag_one: torch.Tensor, rpm_one: torch.Tensor):
        fr = torch.clamp(rpm_one.float() / 60.0, min=1e-3)
        orders = self.freqs / fr

        bandwidth = self.max_order / self.num_order_bins * 1.5
        dist = torch.abs(orders.unsqueeze(0) - self.order_centers.unsqueeze(1))
        weight = torch.clamp(1.0 - dist / bandwidth, min=0.0)
        weight = weight / (weight.sum(dim=1, keepdim=True) + 1e-8)

        return torch.matmul(weight, mag_one)

    @staticmethod
    def _analytic_envelope(sig: torch.Tensor) -> torch.Tensor:
        """可微的 Hilbert 解析信号包络，替代旧版的简单 ``abs(x)``。"""
        n = sig.shape[1]
        spectrum = torch.fft.fft(sig, dim=1)
        multiplier = torch.zeros(n, dtype=sig.dtype, device=sig.device)
        multiplier[0] = 1.0
        if n % 2 == 0:
            multiplier[n // 2] = 1.0
            multiplier[1 : n // 2] = 2.0
        else:
            multiplier[1 : (n + 1) // 2] = 2.0
        analytic = torch.fft.ifft(spectrum * multiplier.unsqueeze(0), dim=1)
        return torch.abs(analytic)

    def forward(self, x: torch.Tensor, rpm: torch.Tensor):
        if x.ndim != 3 or x.shape[1] != 1 or x.shape[2] != self.win_len:
            raise ValueError(
                f"阶次谱输入应为 [B, 1, {self.win_len}]，实际为 {tuple(x.shape)}。"
            )
        rpm = rpm.reshape(-1)
        if rpm.shape[0] != x.shape[0]:
            raise ValueError(f"rpm 数量 {rpm.shape[0]} 与 batch size {x.shape[0]} 不一致。")
        if not torch.isfinite(rpm).all() or (rpm <= 0).any():
            raise ValueError("rpm 必须是有限正数。")

        sig = x[:, 0, :]
        sig = sig - sig.mean(dim=1, keepdim=True)

        if self.envelope_mode == "hilbert":
            sig = self._analytic_envelope(sig)
            sig = sig - sig.mean(dim=1, keepdim=True)
        elif self.envelope_mode == "abs":
            sig = torch.abs(sig)
            sig = sig - sig.mean(dim=1, keepdim=True)

        win = torch.hann_window(sig.shape[1], device=sig.device, dtype=sig.dtype)
        spec = torch.fft.rfft(sig * win.unsqueeze(0), dim=1)
        mag = torch.log1p(torch.abs(spec))

        feats = []
        for i in range(mag.shape[0]):
            feats.append(self._order_pool_one(mag[i], rpm[i]))

        order_feat = torch.stack(feats, dim=0)
        return self.mlp(order_feat)


class WDCNNV8DG(nn.Module):
    def __init__(
        self,
        num_classes: int = 3,
        num_domains: int = 2,
        fs: float = 16000.0,
        win_len: int = 4096,
        feat_dim: int = 256,
        envelope_mode: str = "hilbert",
        enabled_branches: str | Sequence[str] = VALID_FEATURE_BRANCHES,
    ):
        super().__init__()

        if num_classes != 3:
            raise ValueError("当前 v8 按 healthy / outer_ring / rolling_element 三分类设计，num_classes 应为 3。")

        self.num_classes = int(num_classes)
        self.num_domains = int(num_domains)
        self.fs = float(fs)
        self.win_len = int(win_len)
        self.feat_dim = int(feat_dim)
        self.envelope_mode = str(envelope_mode).lower()
        self.enabled_branches = normalize_enabled_branches(enabled_branches)

        self.time_branch = WDCNNFeature(out_dim=128)
        self.order_branch = OrderSpectrumFeature(
            fs=fs,
            win_len=win_len,
            max_order=80.0,
            num_order_bins=160,
            out_dim=64,
            use_abs_envelope=False,
        )
        self.env_order_branch = OrderSpectrumFeature(
            fs=fs,
            win_len=win_len,
            max_order=80.0,
            num_order_bins=160,
            out_dim=64,
            use_abs_envelope=True,
            envelope_mode=self.envelope_mode,
        )

        self.fusion = nn.Sequential(
            nn.Linear(128 + 64 + 64, feat_dim),
            nn.BatchNorm1d(feat_dim),
            nn.ReLU(inplace=True),
            nn.Dropout(0.2),
        )

        self.classifier = nn.Linear(feat_dim, num_classes)
        self.binary_classifier = nn.Linear(feat_dim, 2)

        self.domain_discriminator = nn.Sequential(
            nn.Linear(feat_dim, 128),
            nn.ReLU(inplace=True),
            nn.Dropout(0.2),
            nn.Linear(128, num_domains),
        )

    def forward(self, x: torch.Tensor, rpm: torch.Tensor, grl_lambda: float = 0.0):
        # 保持 fusion 输入维度固定，使所有消融共享同一主干接口，并与旧 checkpoint
        # 完全兼容。被关闭的分支不执行前向计算，以同形状零向量替代。
        ft = (
            self.time_branch(x)
            if "time" in self.enabled_branches
            else x.new_zeros((x.shape[0], 128))
        )
        fo = (
            self.order_branch(x, rpm)
            if "order" in self.enabled_branches
            else x.new_zeros((x.shape[0], 64))
        )
        fe = (
            self.env_order_branch(x, rpm)
            if "envelope" in self.enabled_branches
            else x.new_zeros((x.shape[0], 64))
        )

        feat = self.fusion(torch.cat([ft, fo, fe], dim=1))

        logits = self.classifier(feat)
        binary_logits = self.binary_classifier(feat)

        rev_feat = grad_reverse(feat, grl_lambda)
        domain_logits = self.domain_discriminator(rev_feat)

        return {
            "feat": feat,
            "logits": logits,
            "binary_logits": binary_logits,
            "domain_logits": domain_logits,
        }
