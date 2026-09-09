"""
振动诊断推理服务 v2

直接调用 04.4_diagnose_unlabeled_target.py 的诊断引擎，
使用 04.4 原生的 compute_fft_full_curve / downsample_curve / compute_industrial_metrics
等函数，仅保留一层薄映射将结果适配为前端期望的字段名。

生产边界：
- 仅暴露受内部令牌保护的推理、存活、就绪和指标接口
- 不直接访问业务数据库，诊断任务、结果和告警均由 Java 平台写入
- 只读取 Java 已完成鉴权、校验和病毒扫描的受信附件目录
"""

from __future__ import annotations

# =============================================================================
# 标准库
# =============================================================================
import importlib.util
import json
import hashlib
import logging
from datetime import datetime, timezone
import os
import secrets
import threading
import time
from collections import OrderedDict
from contextlib import asynccontextmanager
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple
from urllib.parse import unquote, urlparse

# =============================================================================
# 第三方库
# =============================================================================
import numpy as np
import scipy.io
import torch
import torch.nn as nn
import uvicorn
from fastapi import Depends, FastAPI, Header, HTTPException
from starlette.responses import JSONResponse, Response
from prometheus_client import CONTENT_TYPE_LATEST, Counter, Histogram, generate_latest

# =============================================================================
# 项目内部模块
# =============================================================================
from models.wdcnn_mech_dg2 import WDCNNMechDG
from models.resnet18_1d import ResNet1D18
from models.wdcnn_v8_bearing_dg import WDCNNV8DG
from core.inference_v8 import (
    build_model_from_checkpoint as v8_build_model,
    load_checkpoint as v8_load_checkpoint,
    predict_signal_v8 as v8_predict_signal,
    apply_rejection_v8 as v8_apply_rejection,
    decision_config_from_checkpoint as v8_decision_config,
    rejection_config_from_checkpoint as v8_rejection_config,
    assess_signal_quality as v8_assess_quality,
)
from utils_signal import _extract_signal_from_dict
from utils.dataset import FileInferenceDataset

# ---------------------------------------------------------------------------
# 动态导入 04.4 诊断模块（文件名以数字开头，无法直接 import）
# ---------------------------------------------------------------------------
BASE_DIR = Path(__file__).resolve().parent
_v6_spec = importlib.util.spec_from_file_location(
    "diagnose_v6",
    str(BASE_DIR / "04.4_diagnose_unlabeled_target.py"),
)
v6 = importlib.util.module_from_spec(_v6_spec)
_v6_spec.loader.exec_module(v6)

# =============================================================================
# 日志配置
# =============================================================================
class _JsonLogFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        payload = {
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "level": record.levelname,
            "logger": record.name,
            "message": record.getMessage(),
            "service": "phm-inference",
        }
        for key in ("requestId", "taskId", "eventId", "deviceCode", "modelType"):
            value = getattr(record, key, None)
            if value is not None:
                payload[key] = value
        if record.exc_info:
            payload["exception"] = self.formatException(record.exc_info)
        return json.dumps(payload, ensure_ascii=False)


_log_handler = logging.StreamHandler()
_log_handler.setFormatter(_JsonLogFormatter())
logging.basicConfig(level=logging.INFO, handlers=[_log_handler], force=True)
logger = logging.getLogger("inference_service")

# =============================================================================
# 路径与常量
# =============================================================================
GEAR_MODEL_PATH = Path(os.environ.get(
    "GEAR_MODEL_PATH", str(BASE_DIR / "get" / "best_model_classwise_maha.pth")
)).expanduser().resolve()
BEARING_MODEL_PATH = Path(os.environ.get(
    "BEARING_MODEL_PATH", str(BASE_DIR / "get" / "best_model.pth")
)).expanduser().resolve()
GEAR_MODEL_SHA256 = os.environ.get("GEAR_MODEL_SHA256", "").strip().lower()
BEARING_MODEL_SHA256 = os.environ.get("BEARING_MODEL_SHA256", "").strip().lower()
BEARING_V8_MODEL_PATH = Path(os.environ.get(
    "BEARING_V8_MODEL_PATH", str(BASE_DIR / "get" / "best_model.pth")
)).expanduser().resolve()
BEARING_V8_MODEL_SHA256 = os.environ.get("BEARING_V8_MODEL_SHA256", "").strip().lower()
ALLOW_UNVERIFIED_MODELS = os.environ.get(
    "INFERENCE_ALLOW_UNVERIFIED_MODELS", "false"
).strip().lower() == "true"
DATA_DIR = BASE_DIR / "get" / "got"
# Java 平台（application.yml 中 sensor.attachment.root 默认 ./.local-data/attachments）
# 将受信附件存放到 <项目根>/.local-data/attachments/objects 并向下发 filePath。
# 缺省可信根同时包含 DATA_DIR 与该平台附件目录，使 Java 下发的路径无需额外
# 环境变量即可通过校验；生产环境仍可用 INFERENCE_ALLOWED_INPUT_ROOTS 显式覆盖。
_PLATFORM_ATTACHMENT_OBJECTS = BASE_DIR.parent.parent / ".local-data" / "attachments" / "objects"
_DEFAULT_INPUT_ROOTS = os.pathsep.join(
    str(p) for p in (DATA_DIR, _PLATFORM_ATTACHMENT_OBJECTS)
)
ALLOWED_INPUT_ROOTS = tuple(
    Path(value.strip()).expanduser().resolve()
    for value in os.environ.get("INFERENCE_ALLOWED_INPUT_ROOTS", _DEFAULT_INPUT_ROOTS).split(os.pathsep)
    if value.strip()
)

DEFAULT_PORT = int(os.environ.get("PORT", 5000))
INFERENCE_BIND_HOST = os.environ.get("INFERENCE_BIND_HOST", "127.0.0.1")
INTERNAL_TOKEN = os.environ.get("INFERENCE_INTERNAL_TOKEN", "")
GEAR_MODEL_VERSION = os.environ.get("GEAR_MODEL_VERSION", "gear-unregistered")
BEARING_MODEL_VERSION = os.environ.get("BEARING_MODEL_VERSION", "bearing-v8-unregistered")
BEARING_V8_MODEL_VERSION = os.environ.get("BEARING_V8_MODEL_VERSION", "bearing-v8-unregistered")
MODEL_ROOT = Path(os.environ.get(
    "INFERENCE_MODEL_ROOT", str(BASE_DIR / "get")
)).expanduser().resolve()
MODEL_CACHE_SIZE = max(1, int(os.environ.get("INFERENCE_MODEL_CACHE_SIZE", "3")))
DISPLAY_POINTS = 2048
DISPLAY_SPECTRUM_POINTS = 512
CONFIDENCE_MIN = 1.0
CONFIDENCE_MAX = 99.0
CACHE_MAX_SIZE = 32
VALID_MODEL_TYPES = {"gear", "bearing"}
_enabled_model_values = {
    value.strip().lower()
    for value in os.environ.get("INFERENCE_ENABLED_MODELS", "gear,bearing").split(",")
    if value.strip()
}
ENABLED_MODELS = _enabled_model_values.intersection(VALID_MODEL_TYPES) or set(VALID_MODEL_TYPES)
INFERENCE_WORKERS = max(1, int(os.environ.get("INFERENCE_WORKERS", "1")))
MAX_RAW_SIGNAL_SAMPLES = max(1024, int(os.environ.get("INFERENCE_MAX_RAW_SIGNAL_SAMPLES", "262144")))
INFERENCE_BATCH_MAX_ITEMS = max(1, int(os.environ.get("INFERENCE_BATCH_MAX_ITEMS", "8")))
BEARING_CLASS_CN_MAP = {
    "N": "正常",
    "normal": "正常",
    "healthy": "正常",
    "OR": "轴承外圈故障",
    "outer_ring": "轴承外圈故障",
    "outer": "轴承外圈故障",
    "B": "滚动体故障",
    "ball": "滚动体故障",
    "rolling_element": "滚动体故障",
    "IR": "轴承内圈故障",
    "inner_ring": "轴承内圈故障",
    "inner": "轴承内圈故障",
}

# =============================================================================
# 响应缓存
# =============================================================================
_response_cache: Dict[str, Tuple[float, Dict[str, Any]]] = {}
_model_bundle_cache: "OrderedDict[str, Dict[str, Any]]" = OrderedDict()
_inference_executor = ThreadPoolExecutor(max_workers=INFERENCE_WORKERS, thread_name_prefix="inference-worker")
_gear_inference_lock = threading.Lock()
_bearing_inference_lock = threading.Lock()
_model_cache_lock = threading.Lock()
_batch_requests = Counter(
    "phm_inference_batch_requests_total",
    "Realtime inference batch requests",
    ("status",),
)
_batch_items = Counter(
    "phm_inference_batch_items_total",
    "Realtime inference batch items",
    ("model_type", "status"),
)
_model_duration = Histogram(
    "phm_inference_model_duration_seconds",
    "Inference duration by model type",
    ("model_type",),
)

# =============================================================================
# FastAPI 应用
# =============================================================================
@asynccontextmanager
async def lifespan(app: FastAPI):
    """Lifespan context manager: startup & shutdown logic (FastAPI modern pattern)."""
    global gear_model, gear_model_params, gear_class_names, gear_classwise_cfg
    global bearing_model, bearing_model_params, bearing_class_names
    global bearing_v8_model, bearing_v8_params, bearing_v8_class_names
    global bearing_v8_decision_config, bearing_v8_rejection_config
    global CLASS_FILE_UNK_OVERRIDES, MEAN_MAHA_ACCEPT_OVERRIDES, KNOWN_FAULT_PROTECT_CLASSES

    if len(INTERNAL_TOKEN.encode("utf-8")) < 32:
        raise RuntimeError("INFERENCE_INTERNAL_TOKEN must contain at least 32 UTF-8 bytes.")

    # Models are isolated: one broken artifact must not make the other model unusable.
    if "gear" in ENABLED_MODELS:
        logger.info("Loading gear model from %s", GEAR_MODEL_PATH)
        try:
            gear_model, gear_model_params, gear_class_names, gear_classwise_cfg = load_gear_model()
            model_load_errors.pop("gear", None)
            logger.info("Gear model loaded on %s", DEVICE)
        except Exception as exc:
            gear_model = None
            gear_model_params = {}
            gear_class_names = []
            gear_classwise_cfg = {}
            model_load_errors["gear"] = str(exc)
            logger.exception("Gear model failed to load")
    else:
        gear_model = None
        gear_model_params = {}
        gear_class_names = []
        gear_classwise_cfg = {}

    if "bearing" in ENABLED_MODELS:
        # Try V8 first, fall back to ResNet1D18
        try:
            logger.info("Loading bearing V8 model from %s", BEARING_V8_MODEL_PATH)
            (bearing_v8_model, bearing_v8_params, bearing_v8_class_names,
             bearing_v8_decision_config, bearing_v8_rejection_config) = load_bearing_v8_model()
            model_load_errors.pop("bearing", None)
            model_load_errors.pop("bearing_v8", None)
            logger.info("Bearing V8 model loaded on %s", DEVICE)
        except Exception as exc:
            bearing_v8_model = None
            bearing_v8_params = {}
            bearing_v8_class_names = []
            bearing_v8_decision_config = {}
            bearing_v8_rejection_config = {}
            model_load_errors["bearing_v8"] = str(exc)
            logger.warning("Bearing V8 model failed to load, trying ResNet1D18 fallback: %s", exc)
            try:
                logger.info("Loading bearing ResNet1D18 model from %s", BEARING_MODEL_PATH)
                bearing_model, bearing_model_params, bearing_class_names = load_bearing_model()
                model_load_errors.pop("bearing", None)
                logger.info("Bearing ResNet1D18 model loaded on %s", DEVICE)
            except Exception as exc2:
                bearing_model = None
                bearing_model_params = {}
                bearing_class_names = []
                model_load_errors["bearing"] = str(exc2)
                logger.exception("Bearing ResNet1D18 model also failed to load")
    else:
        bearing_model = None
        bearing_model_params = {}
        bearing_class_names = []
        bearing_v8_model = None
        bearing_v8_params = {}
        bearing_v8_class_names = []
        bearing_v8_decision_config = {}
        bearing_v8_rejection_config = {}

    CLASS_FILE_UNK_OVERRIDES = v6.parse_class_threshold_overrides(
        "healthy:0.70,single_pitting:0.85,multi_pitting:0.85,single_spalling:0.85"
    )
    MEAN_MAHA_ACCEPT_OVERRIDES = v6.parse_maha_accept_overrides(
        "single_pitting:30,multi_pitting:25,single_spalling:20"
    )
    KNOWN_FAULT_PROTECT_CLASSES = v6.parse_protect_classes(
        "single_pitting,multi_pitting,single_spalling"
    )
    logger.info("v6 thresholds configured")

    yield  # app runs here

    # ---- shutdown ----
    _inference_executor.shutdown(wait=False, cancel_futures=True)
    logger.info("Inference executor stopped")


app = FastAPI(
    title="Vibration Diagnosis Internal Service",
    version="3.0.0",
    lifespan=lifespan,
    docs_url=None,
    redoc_url=None,
    openapi_url=None,
)

INFERENCE_REQUESTS = Counter(
    "phm_inference_requests_total", "Inference HTTP requests", ["path", "method", "status"]
)
INFERENCE_DURATION = Histogram(
    "phm_inference_request_duration_seconds", "Inference HTTP request duration", ["path", "method"]
)


@app.middleware("http")
async def observe_requests(request, call_next):
    started = time.perf_counter()
    status = "500"
    try:
        response = await call_next(request)
        status = str(response.status_code)
        return response
    finally:
        path = request.url.path
        INFERENCE_REQUESTS.labels(path, request.method, status).inc()
        INFERENCE_DURATION.labels(path, request.method).observe(time.perf_counter() - started)


def require_internal_token(
    x_internal_token: str = Header(default="", alias="X-Internal-Token"),
) -> None:
    if not INTERNAL_TOKEN or not secrets.compare_digest(x_internal_token, INTERNAL_TOKEN):
        raise HTTPException(status_code=401, detail="Invalid internal service token.")


# =============================================================================
# 全局模型状态
# =============================================================================
DEVICE = torch.device("cuda" if torch.cuda.is_available() else "cpu")
gear_model: Optional[WDCNNMechDG] = None
gear_model_params: Dict[str, Any] = {}
gear_class_names: List[str] = []
gear_classwise_cfg: Dict[str, Any] = {}
bearing_model: Optional[nn.Module] = None
bearing_model_params: Dict[str, Any] = {}
bearing_class_names: List[str] = []
bearing_v8_model: Optional[WDCNNV8DG] = None
bearing_v8_params: Dict[str, Any] = {}
bearing_v8_class_names: List[str] = []
bearing_v8_decision_config: Dict[str, Any] = {}
bearing_v8_rejection_config: Dict[str, Any] = {}
model_load_errors: Dict[str, str] = {}
CLASS_FILE_UNK_OVERRIDES: Dict[str, float] = {}
MEAN_MAHA_ACCEPT_OVERRIDES: Dict[str, float] = {}
KNOWN_FAULT_PROTECT_CLASSES: List[str] = []


# =============================================================================
# 信号加载 — 委托给 utils_signal
# =============================================================================
def _resolve_analysis_file(file_name: Optional[str]) -> Path:

    if file_name:
        safe_name = Path(file_name).name
        suffix = Path(safe_name).suffix.lower()
        candidates = [DATA_DIR / safe_name] if suffix in {".mat", ".npy"} else [
            DATA_DIR / f"{Path(safe_name).stem}.npy",
            DATA_DIR / f"{Path(safe_name).stem}.mat",
        ]
        file_path = next((p for p in candidates if p.exists()), candidates[0])
        if not file_path.exists():
            raise FileNotFoundError(f"File not found: {file_path}")
    else:
        all_files = sorted(
            list(DATA_DIR.glob("*.mat")) + list(DATA_DIR.glob("*.npy")),
            key=lambda p: (p.stat().st_mtime, p.name),
            reverse=True,
        )
        if not all_files:
            raise FileNotFoundError(f"No .mat or .npy files found in {DATA_DIR}")
        file_path = all_files[0]

    suffix = file_path.suffix.lower()
    if suffix not in {".mat", ".npy"}:
        raise ValueError(f"Unsupported file type: {suffix}. Only .mat and .npy are supported.")
    return file_path


def _resolve_trusted_input_path(file_path: str) -> Path:
    source_path = Path(file_path)
    if not source_path.is_absolute():
        raise HTTPException(status_code=400, detail="filePath must be an absolute trusted storage path.")
    resolved = source_path.expanduser().resolve()
    if not any(resolved == root or root in resolved.parents for root in ALLOWED_INPUT_ROOTS):
        raise HTTPException(status_code=403, detail="filePath is outside configured inference input roots.")
    if not resolved.is_file():
        raise HTTPException(status_code=404, detail=f"File not found: {resolved}")
    if resolved.suffix.lower() not in {".mat", ".npy"}:
        raise HTTPException(
            status_code=400,
            detail=f"Unsupported file type: {resolved.suffix.lower()}. Only .mat and .npy are supported.",
        )
    return resolved


def _load_signal_from_path(
    file_path: Path,
    preferred_signal_key: Optional[str] = None,
) -> Tuple[np.ndarray, str, Dict[str, Any]]:
    """
    从文件路径加载振动信号（.mat / .npy）。

    Parameters
    ----------
    file_path : Path
    preferred_signal_key : str or None
        优先使用的信号变量名。对 .mat 文件会优先匹配该 key；
        对 .npy 文件会作为 load_npy_signal 的 signal_key。

    Returns
    -------
    (signal, filename, metadata)
        metadata 包含从文件中提取的 sample_rate / rpm（可能为 None）。
    """
    from utils_signal import load_npy_signal, _extract_mat_metadata

    suffix = file_path.suffix.lower()
    meta: Dict[str, Any] = {"sample_rate": None, "rpm": None}

    if suffix == ".mat":
        if not file_path.exists():
            raise FileNotFoundError(f".mat file not found: {file_path}")
        mat = scipy.io.loadmat(str(file_path))
        sig = _extract_signal_from_dict(
            mat,
            source_name=file_path.name,
            preferred_signal_key=preferred_signal_key,
        )
        # 从 .mat 元数据字段提取 sample_rate / rpm
        meta = _extract_mat_metadata(mat)
    elif suffix == ".npy":
        sig = load_npy_signal(
            file_path,
            signal_key=preferred_signal_key or "sig_acc_5120",
        )
    else:
        raise ValueError(f"Unsupported file type: {suffix}. Only .mat and .npy are supported.")

    sig = np.nan_to_num(np.asarray(sig, dtype=np.float32).reshape(-1),
                        nan=0.0, posinf=0.0, neginf=0.0)
    return sig, file_path.name, meta


# =============================================================================
# 缓存辅助
# =============================================================================
def _get_cached_or_compute(
    file_path: Path,
    model_type: str,
    model_version: str,
    compute_fn,
    *args: Any,
    **kwargs: Any,
) -> Dict[str, Any]:
    cache_key = f"{model_type}:{model_version}:{file_path.resolve()}"
    try:
        mtime = file_path.stat().st_mtime
    except OSError:
        mtime = 0.0

    if cache_key in _response_cache:
        cached_mtime, cached_result = _response_cache[cache_key]
        if cached_mtime == mtime:
            logger.info("Cache hit for %s", file_path.name)
            return cached_result

    result = compute_fn(*args, **kwargs)
    _response_cache[cache_key] = (mtime, result)

    if len(_response_cache) > CACHE_MAX_SIZE:
        oldest = next(iter(_response_cache))
        del _response_cache[oldest]
        logger.debug("Cache evicted oldest entry")
    return result


# =============================================================================
# 模型加载
# =============================================================================
_TORCH_SAFE_GLOBALS_REGISTERED = False


def _register_torch_safe_globals() -> None:
    """Allow trusted NumPy metadata in ``weights_only`` checkpoints.

    PyTorch 2.6+ defaults ``torch.load`` to ``weights_only=True``.  The
    project's checkpoints contain NumPy dtype metadata, so the restricted
    unpickler must be explicitly told about the small set of NumPy types we
    trust.  Keep this registration in the tracked service module (rather than
    relying on a generated/ignored diagnostic script) so every deployment has
    the same behavior.
    """
    global _TORCH_SAFE_GLOBALS_REGISTERED
    if _TORCH_SAFE_GLOBALS_REGISTERED:
        return
    add_safe_globals = getattr(torch.serialization, "add_safe_globals", None)
    if add_safe_globals is None:
        _TORCH_SAFE_GLOBALS_REGISTERED = True
        return

    np_core = getattr(np, "_core", None) or getattr(np, "core", None)
    reconstruct = getattr(getattr(np_core, "multiarray", None), "_reconstruct", None)
    safe_types = [np.ndarray, np.dtype]
    if reconstruct is not None:
        safe_types.append(reconstruct)
    for dtype_name in ("Float32DType", "Float64DType", "Int32DType", "Int64DType"):
        dtype_type = getattr(getattr(np, "dtypes", None), dtype_name, None)
        if dtype_type is not None:
            safe_types.append(dtype_type)
    # NumPy 1.x represents scalar dtypes as ``type(np.dtype(...))``.
    for dtype in (np.float32, np.float64, np.int32, np.int64):
        safe_types.append(type(np.dtype(dtype)))
    add_safe_globals(safe_types)
    _TORCH_SAFE_GLOBALS_REGISTERED = True


def _clean_state_dict(state_dict: Dict[str, torch.Tensor]) -> Dict[str, torch.Tensor]:
    """Remove 'module.' prefix from DataParallel-wrapped state dict keys."""
    cleaned: Dict[str, torch.Tensor] = {}
    for k, v in state_dict.items():
        if k.startswith("module."):
            cleaned[k[len("module."):]] = v
        else:
            cleaned[k] = v
    return cleaned


def _verify_model_artifact(path: Path, expected_sha256: str, model_type: str) -> None:
    if not path.is_file():
        raise FileNotFoundError(f"{model_type} model checkpoint not found: {path}")
    if not expected_sha256:
        if ALLOW_UNVERIFIED_MODELS:
            logger.warning("%s model hash verification is explicitly disabled", model_type)
            return
        raise RuntimeError(f"{model_type.upper()}_MODEL_SHA256 must be configured")
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    actual = digest.hexdigest()
    if not secrets.compare_digest(actual, expected_sha256):
        raise RuntimeError(
            f"{model_type} model SHA-256 mismatch: expected {expected_sha256}, got {actual}"
        )


def load_gear_model(
    model_path: Path = GEAR_MODEL_PATH,
    expected_sha256: str = GEAR_MODEL_SHA256,
) -> Tuple[WDCNNMechDG, Dict[str, Any], List[str], Dict[str, Any]]:
    _verify_model_artifact(model_path, expected_sha256, "gear")

    _register_torch_safe_globals()
    ckpt = v6.safe_torch_load(model_path, map_location=DEVICE)
    params = ckpt["params"]
    class_names = [str(x) for x in ckpt["classes"]]
    num_classes = len(class_names)
    classwise_cfg = ckpt["classwise_open_set"]

    model = WDCNNMechDG(
        num_classes=num_classes,
        num_domains=2,
        fs=float(params["fs"]),
        win_len=int(params["win_len"]),
        feat_dim=int(params["feat_dim"]),
    ).to(DEVICE)
    model.load_state_dict(ckpt["model_state"])
    model.eval()

    logger.info("Gear model loaded: %d classes %s on %s", num_classes, class_names, DEVICE)
    return model, params, class_names, classwise_cfg


def load_bearing_model(
    model_path: Path = BEARING_MODEL_PATH,
    expected_sha256: str = BEARING_MODEL_SHA256,
) -> Tuple[nn.Module, Dict[str, Any], List[str]]:
    """
    Load bearing diagnosis model (ResNet1D18) from best_model.pth.

    The checkpoint follows the CCDG training pipeline format:
        state_dict  — model weights (possibly wrapped with 'module.' prefix)
        classes     — list of class label strings, e.g. ['N', 'OR', 'B']
        window_size / stride / signal_key / normalize — preprocessing config
    """
    _verify_model_artifact(model_path, expected_sha256, "bearing")

    _register_torch_safe_globals()
    ckpt = v6.safe_torch_load(model_path, map_location=DEVICE)

    # ---- extract state dict ----
    if "state_dict" in ckpt:
        state_dict = _clean_state_dict(ckpt["state_dict"])
    else:
        state_dict = _clean_state_dict(ckpt)

    # ---- class names ----
    if "classes" in ckpt and ckpt["classes"]:
        class_names = [str(c) for c in ckpt["classes"]]
    else:
        # fallback: infer from fc.weight shape
        for k, v in state_dict.items():
            if k.endswith("fc.weight"):
                num_classes = int(v.shape[0])
                class_names = [f"class_{i}" for i in range(num_classes)]
                break
        else:
            class_names = ["N", "OR", "B"]

    num_classes = len(class_names)

    # ---- params ----
    params = {
        "fs": float(ckpt.get("sample_rate", ckpt.get("fs", 16000.0))),
        "win_len": int(ckpt.get("window_size", ckpt.get("win_len", 4096))),
        "stride": int(ckpt.get("stride", 4096)),
        "batch_size": int(ckpt.get("batch_size", 64)),
        "signal_key": str(ckpt.get("signal_key", "DE_time")),
        "normalize": str(ckpt.get("normalize", "zscore")),
        "num_classes": num_classes,
    }

    # ---- build model ----
    model = ResNet1D18(num_classes=num_classes).to(DEVICE)
    model.load_state_dict(state_dict, strict=True)
    model.eval()

    logger.info(
        "Bearing model loaded: %d classes %s on %s (window=%d, stride=%d, fs=%.0f)",
        num_classes, class_names, DEVICE,
        params["win_len"], params["stride"], params["fs"],
    )
    return model, params, class_names


def load_bearing_v8_model(
    model_path: Path = BEARING_V8_MODEL_PATH,
    expected_sha256: str = BEARING_V8_MODEL_SHA256,
) -> Tuple[WDCNNV8DG, Dict[str, Any], List[str], Dict[str, Any], Dict[str, Any]]:
    """Load WDCNN-V8-DG bearing diagnosis model from checkpoint."""
    _verify_model_artifact(model_path, expected_sha256, "bearing_v8")

    _register_torch_safe_globals()
    checkpoint = v8_load_checkpoint(model_path, map_location=DEVICE)

    class_names = list(checkpoint["classes"])
    params = {
        "fs": float(checkpoint.get("fs", 16000.0)),
        "win_len": int(checkpoint.get("win_len", 4096)),
        "stride": int(checkpoint.get("win_len", 4096)),
        "feat_dim": int(checkpoint.get("feat_dim", 256)),
        "num_domains": int(checkpoint.get("num_domains", 2)),
        "envelope_mode": str(checkpoint.get("envelope_mode", "hilbert")),
        "signal_key": str(checkpoint.get("signal_key", "DE_time")),
        "num_classes": len(class_names),
    }
    decision_config = v8_decision_config(checkpoint)
    rejection_config = v8_rejection_config(checkpoint)

    model = v8_build_model(checkpoint, device=DEVICE)

    logger.info(
        "Bearing V8 model loaded: %d classes %s on %s (win_len=%d, fs=%.0f, envelope=%s)",
        len(class_names), class_names, DEVICE,
        params["win_len"], params["fs"], params["envelope_mode"],
    )
    return model, params, class_names, decision_config, rejection_config


def _resolve_model_artifact(artifact_uri: str) -> Path:
    value = str(artifact_uri or "").strip()
    if not value:
        raise ValueError("modelArtifactUri is required for a registered model version.")
    parsed = urlparse(value)
    is_windows_drive = len(value) > 2 and value[1] == ":" and value[2] in ("\\", "/")
    if parsed.scheme and parsed.scheme.lower() != "file" and not is_windows_drive:
        raise ValueError("Only local file model artifacts are supported.")
    if is_windows_drive:
        candidate = Path(value)
    elif parsed.scheme.lower() == "file":
        raw_path = unquote(parsed.path)
        if parsed.netloc:
            raw_path = f"//{parsed.netloc}{raw_path}"
        if os.name == "nt" and raw_path.startswith("/") and len(raw_path) > 2 and raw_path[2] == ":":
            raw_path = raw_path[1:]
        candidate = Path(raw_path)
    else:
        candidate = Path(value)
    if not candidate.is_absolute():
        candidate = MODEL_ROOT / candidate
    resolved = candidate.expanduser().resolve()
    try:
        resolved.relative_to(MODEL_ROOT)
    except ValueError as exc:
        raise ValueError("Model artifact is outside INFERENCE_MODEL_ROOT.") from exc
    return resolved


def _default_model_bundle(model_type: str, requested_version: str) -> Dict[str, Any]:
    if model_type == "gear":
        if requested_version and requested_version != GEAR_MODEL_VERSION:
            raise ValueError(f"Requested gear model version is not loaded: {requested_version}")
        if gear_model is None:
            raise RuntimeError(model_load_errors.get("gear", "Gear model is not loaded."))
        return {
            "model": gear_model,
            "params": gear_model_params,
            "classes": gear_class_names,
            "classwise_cfg": gear_classwise_cfg,
            "version": GEAR_MODEL_VERSION,
        }
    # bearing: prefer V8, fall back to ResNet1D18
    if bearing_v8_model is not None:
        version = requested_version or BEARING_V8_MODEL_VERSION
        return {
            "model": bearing_v8_model,
            "params": bearing_v8_params,
            "classes": bearing_v8_class_names,
            "decision_config": bearing_v8_decision_config,
            "rejection_config": bearing_v8_rejection_config,
            "version": version,
            "engine": "v8",
        }
    if requested_version and requested_version != BEARING_MODEL_VERSION:
        raise ValueError(f"Requested bearing model version is not loaded: {requested_version}")
    if bearing_model is None:
        raise RuntimeError(model_load_errors.get("bearing", "Bearing model is not loaded."))
    return {
        "model": bearing_model,
        "params": bearing_model_params,
        "classes": bearing_class_names,
        "version": BEARING_MODEL_VERSION,
        "engine": "resnet18",
    }


def _load_model_bundle(
    model_type: str,
    requested_version: str,
    artifact_uri: str = "",
    expected_sha256: str = "",
) -> Dict[str, Any]:
    if not artifact_uri:
        return _default_model_bundle(model_type, requested_version)
    if not requested_version:
        raise ValueError("modelVersion is required for a registered model artifact.")
    expected = str(expected_sha256 or "").strip().lower()
    if not expected or len(expected) != 64:
        raise ValueError("A valid modelArtifactSha256 is required.")
    artifact_path = _resolve_model_artifact(artifact_uri)
    cache_key = f"{model_type}:{requested_version}:{expected}:{artifact_path}"
    with _model_cache_lock:
        cached = _model_bundle_cache.get(cache_key)
        if cached is not None:
            _model_bundle_cache.move_to_end(cache_key)
            return cached

        if model_type == "gear":
            model, params, classes, classwise_cfg = load_gear_model(artifact_path, expected)
            bundle = {
                "model": model,
                "params": params,
                "classes": classes,
                "classwise_cfg": classwise_cfg,
                "version": requested_version,
                "engine": "gear",
            }
        else:
            # Try V8 first, fall back to ResNet1D18
            try:
                model, params, classes, dec_cfg, rej_cfg = load_bearing_v8_model(artifact_path, expected)
                bundle = {
                    "model": model,
                    "params": params,
                    "classes": classes,
                    "decision_config": dec_cfg,
                    "rejection_config": rej_cfg,
                    "version": requested_version,
                    "engine": "v8",
                }
            except Exception:
                model, params, classes = load_bearing_model(artifact_path, expected)
                bundle = {
                    "model": model,
                    "params": params,
                    "classes": classes,
                    "version": requested_version,
                    "engine": "resnet18",
                }
        _model_bundle_cache[cache_key] = bundle
        while len(_model_bundle_cache) > MODEL_CACHE_SIZE:
            _model_bundle_cache.popitem(last=False)
            if torch.cuda.is_available():
                torch.cuda.empty_cache()
        return bundle


# =============================================================================
# 诊断执行 — 直接调用 04.4 的 diagnose_signal_array
# =============================================================================
def _diagnose_gear(
    raw_signal: np.ndarray,
    source_name: str = "",
    bundle: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    """
    直接调用 04.4 的 diagnose_signal_array，无中间包装。
    """
    selected = bundle or _default_model_bundle("gear", GEAR_MODEL_VERSION)
    model = selected["model"]
    params = selected["params"]
    classes = selected["classes"]
    classwise_cfg = selected["classwise_cfg"]

    sig = np.asarray(raw_signal, dtype=np.float32).reshape(-1)
    win_len = int(params["win_len"])
    stride = int(params["stride"])
    if v6.count_windows(len(sig), win_len, stride) <= 0:
        raise ValueError(
            f"Signal too short ({sig.size} samples) for gear window size {win_len}"
        )

    return v6.diagnose_signal_array(
        model=model,
        sig=sig,
        device=DEVICE,
        win_len=win_len,
        stride=stride,
        batch_size=int(params.get("batch_size", 128)),
        class_names=classes,
        classwise_cfg=classwise_cfg,
        class_file_unk_overrides=CLASS_FILE_UNK_OVERRIDES,
        mean_maha_accept_overrides=MEAN_MAHA_ACCEPT_OVERRIDES,
        fs=float(params.get("fs", 5120.0)),
        unknown_vote_required=3,
        healthy_conf_protect=0.80,
        healthy_unknown_ratio_allow=1.01,
        known_fault_conf_protect=0.99,
        known_fault_unknown_ratio_allow=None,
        known_fault_mean_votes_allow=2.0,
        known_fault_protect_classes=KNOWN_FAULT_PROTECT_CLASSES,
        maha_accept_min_conf=0.85,
        maha_accept_mean_votes_allow=3.10,
        file_label=source_name,
    )


def _normalize_model_type(model_type: Optional[str]) -> str:
    value = str(model_type or "gear").strip().lower()
    if value not in VALID_MODEL_TYPES:
        raise HTTPException(status_code=400, detail="model_type must be 'gear' or 'bearing'.")
    return value


@torch.no_grad()
def _diagnose_bearing(
    raw_signal: np.ndarray,
    source_name: str = "",
    bundle: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    """
    Bearing diagnosis using ResNet1D18 with sliding-window inference.

    Preprocessing matches the CCDG training pipeline:
      - Per-window z-score normalization
      - Window size / stride from checkpoint params
      - File-level prediction = argmax of mean softmax probabilities
    """
    selected = bundle or _default_model_bundle("bearing", BEARING_MODEL_VERSION)
    model = selected["model"]
    params = selected["params"]
    classes = selected["classes"]

    sig = np.asarray(raw_signal, dtype=np.float64).reshape(-1)
    win_len = int(params["win_len"])
    stride = int(params["stride"])
    batch_size = int(params.get("batch_size", 64))

    # window count
    if sig.size < win_len:
        n_win = 0
    else:
        n_win = (sig.size - win_len) // stride + 1

    if n_win == 0:
        raise ValueError(
            f"Signal too short ({sig.size} samples) for window size {win_len}"
        )

    probs_all: List[torch.Tensor] = []
    seg_preds: List[int] = []
    model.eval()

    for i in range(0, n_win, batch_size):
        windows = []
        for j in range(i, min(i + batch_size, n_win)):
            start = j * stride
            chunk = sig[start:start + win_len].copy()
            # per-window z-score (matches CCDG training pipeline)
            mu = chunk.mean()
            sigma = chunk.std()
            if sigma > 1e-8:
                chunk = (chunk - mu) / sigma
            else:
                chunk = chunk - mu
            windows.append(chunk)

        xb = torch.from_numpy(np.stack(windows)).float().unsqueeze(1).to(DEVICE)
        logits, _feat = model(xb)
        probs = torch.softmax(logits, dim=1).cpu()
        probs_all.append(probs)
        seg_preds.extend(torch.argmax(probs, dim=1).tolist())

    probs_tensor = torch.cat(probs_all, dim=0)          # [n_win, num_classes]
    mean_probs = probs_tensor.mean(dim=0).numpy()       # [num_classes]
    final_idx = int(np.argmax(mean_probs))

    seg_preds_np = np.asarray(seg_preds, dtype=np.int64)
    segment_consistency = (
        float((seg_preds_np == final_idx).mean()) if seg_preds_np.size else 0.0
    )

    entropy_per_segment = -torch.sum(
        probs_tensor * torch.log(torch.clamp(probs_tensor, min=1e-12)),
        dim=1,
    )
    mean_entropy = float(entropy_per_segment.mean().item())

    # determine decision reason based on confidence and consistency
    confidence = float(mean_probs[final_idx])
    if confidence >= 0.80 and segment_consistency >= 0.80:
        decision_reason = "bearing_high_confidence"
    elif confidence >= 0.65 and segment_consistency >= 0.60:
        decision_reason = "bearing_moderate_confidence"
    else:
        decision_reason = "bearing_low_confidence"

    return {
        "source_name": source_name,
        "prediction_index": final_idx,
        "prediction": classes[final_idx] if final_idx < len(classes) else "unknown",
        "confidence": confidence,
        "mean_probs": mean_probs,
        "segment_consistency": segment_consistency,
        "num_segments": int(n_win),
        "mean_entropy": mean_entropy,
        "decision_reason": decision_reason,
        "class_names": classes,
    }


@torch.no_grad()
def _diagnose_bearing_v8(
    raw_signal: np.ndarray,
    source_name: str = "",
    bundle: Optional[Dict[str, Any]] = None,
    rpm: Optional[float] = None,
) -> Dict[str, Any]:
    """Bearing diagnosis using WDCNN-V8-DG with probability fusion + rejection."""
    selected = bundle or _default_model_bundle("bearing", BEARING_V8_MODEL_VERSION)
    model = selected["model"]
    params = selected["params"]
    classes = selected["classes"]
    decision_cfg = selected.get("decision_config", {"mode": "probability_fusion", "binary_weight": 1.0})
    rejection_cfg = selected.get("rejection_config", {})

    sig = np.asarray(raw_signal, dtype=np.float32).reshape(-1)
    win_len = int(params["win_len"])
    stride = int(params.get("stride", win_len))
    batch_size = int(params.get("batch_size", 32))
    model_fs = float(params.get("fs", 16000.0))

    # Determine RPM: explicit > file metadata default > 1500
    effective_rpm = float(rpm) if rpm and rpm > 0 else 1500.0

    result = v8_predict_signal(
        model=model,
        signal=sig,
        rpm=effective_rpm,
        device=DEVICE,
        win_len=win_len,
        stride=stride,
        batch_size=batch_size,
        decision_kwargs=decision_cfg,
        input_fs=model_fs,
        model_fs=model_fs,
    )

    result = v8_apply_rejection(result, rejection_cfg)

    pred_idx = int(result["pred"])
    prediction = classes[pred_idx] if pred_idx < len(classes) else "unknown"
    confidence = float(result["confidence"])

    # Map to status
    if not result.get("accepted", True):
        status = "UNCERTAIN"
    elif prediction == "healthy":
        status = "HEALTHY"
    else:
        status = "FAULT"

    return {
        "source_name": source_name,
        "prediction_index": pred_idx,
        "prediction": prediction,
        "confidence": confidence,
        "mean_probs": result["decision_scores"],
        "direct_mean": result["direct_mean"],
        "binary_mean": result["binary_mean"],
        "segment_consistency": float(result["segment_consistency"]),
        "num_segments": int(result["num_segments"]),
        "mean_entropy": float(result.get("normalized_entropy", 0.0)),
        "normalized_entropy": float(result.get("normalized_entropy", 0.0)),
        "decision_reason": str(result["decision_reason"]),
        "class_names": classes,
        "status": status,
        "accepted": result.get("accepted", True),
        "rejection_reasons": result.get("rejection_reasons", []),
        "head_agreement": result.get("head_agreement", True),
        "vote_ratio": float(result.get("vote_ratio", 1.0)),
        "input_fs": result.get("input_fs", model_fs),
        "model_fs": result.get("model_fs", model_fs),
    }


# =============================================================================
# 前端数据映射 — 使用 04.4 原生函数，仅做字段名适配
# =============================================================================
def _build_evidence_list(v6_result: Dict[str, Any]) -> List[Dict[str, Any]]:
    """构建前端证据列表，使用 04.4 的 translate_decision_reason 生成中文描述。"""
    closed_pred = str(v6_result["closed_prediction"])
    confidence = float(v6_result["closed_confidence"])
    unknown_ratio = float(v6_result["unknown_ratio"])
    seg_consistency = float(v6_result["segment_consistency"])
    mean_maha = float(v6_result["mean_mahalanobis"])
    mean_entropy = float(v6_result["mean_entropy"])
    class_file_unk_thr = v6_result.get("class_file_unknown_ratio_threshold", "N/A")
    decision_reason = str(v6_result["decision_reason"])

    evidence: List[Dict[str, Any]] = [
        {
            "title": "决策原因",
            "desc": decision_reason,
            "type": "info",
            "level": "信息",
        },
        {
            "title": "闭集预测",
            "desc": f"{closed_pred} (置信度 {confidence:.4f})",
            "type": "info",
            "level": "信息",
        },
        {
            "title": "Unknown比例",
            "desc": f"{unknown_ratio:.4f} / 阈值 {class_file_unk_thr}",
            "type": "warning" if unknown_ratio > 0.3 else "info",
            "level": "高" if unknown_ratio > 0.5 else "中" if unknown_ratio > 0.3 else "低",
        },
        {
            "title": "片段一致性",
            "desc": f"{seg_consistency:.4f}",
            "type": "success" if seg_consistency > 0.8 else "warning",
            "level": "高" if seg_consistency > 0.8 else "中",
        },
        {
            "title": "Mean Mahalanobis",
            "desc": f"{mean_maha:.4f}",
            "type": "info",
            "level": "信息",
        },
        {
            "title": "Average entropy",
            "desc": f"{mean_entropy:.4f}",
            "type": "info",
            "level": "信息",
        },
    ]

    ef = v6_result.get("evidence_frequencies", [])
    if ef:
        freq_strs = [f"{f:.1f} Hz" for f, _ in ef[:3]]
        evidence.append({
            "title": "特征频率",
            "desc": ", ".join(freq_strs) if freq_strs else "无",
            "type": "info",
            "level": "信息",
        })

    return evidence


def _build_top_probabilities(
    v6_result: Dict[str, Any],
    class_names: Optional[List[str]] = None,
) -> List[Dict[str, Any]]:
    """从 v6_result 的 prob_{class} 字段构建前端概率列表。"""
    top_probs: List[Dict[str, Any]] = []
    for cname in (class_names or gear_class_names):
        key = f"prob_{cname}"
        if key in v6_result:
            top_probs.append({
                "class": cname,
                "probability": round(float(v6_result[key]) * 100.0, 2),
            })
    top_probs.sort(key=lambda x: x["probability"], reverse=True)
    return top_probs


def _build_frontend_payload(
    v6_result: Dict[str, Any],
    raw_signal: np.ndarray,
    source_name: str,
    sample_rate: float = 5120.0,
    extra: Optional[Dict[str, Any]] = None,
    class_names: Optional[List[str]] = None,
) -> Dict[str, Any]:
    """
    将 04.4 诊断结果映射为前端兼容格式。

    时域/频谱/工业指标直接使用 04.4 原生函数计算，
    不再重复实现 FFT / 降采样 / 统计量。
    """
    # ---- 核心诊断字段 ----
    final_pred = str(v6_result["final_prediction"])
    final_pred_cn = str(v6_result.get("final_result_cn", final_pred))
    closed_pred = str(v6_result["closed_prediction"])
    confidence = float(v6_result["closed_confidence"])
    health_score = float(v6_result["health_score"])
    alarm_level = str(v6_result["alarm_level"])
    decision_reason = str(v6_result["decision_reason"])

    # 中文决策原因（来自 04.4）
    decision_reason_cn = v6.translate_decision_reason(decision_reason)

    # ---- 时域波形（使用 04.4 的 downsample_curve） ----
    sig = np.asarray(raw_signal, dtype=np.float64).reshape(-1)
    n = sig.size
    time_axis_full = np.arange(n, dtype=np.float64) / sample_rate if sample_rate > 0 else np.arange(n, dtype=np.float64)
    time_axis, time_data = v6.downsample_curve(time_axis_full, sig, max_points=DISPLAY_POINTS)

    # ---- 频谱（使用 04.4 的 compute_fft_full_curve） ----
    spectrum = v6.compute_fft_full_curve(sig, fs=sample_rate, max_points=DISPLAY_SPECTRUM_POINTS, f_min=0.0)
    freq_axis = spectrum["freq_hz"]
    freq_data = spectrum["amplitude"]

    # ---- 工业指标（使用 04.4 的 compute_industrial_metrics） ----
    metrics = v6.compute_industrial_metrics(sig, fs=sample_rate)
    rms = v6.safe_float(metrics["rms"])
    peak = v6.safe_float(metrics["peak"])

    # ---- 证据列表 ----
    evidence = _build_evidence_list(v6_result)

    # ---- 类别概率 ----
    top_probs = _build_top_probabilities(v6_result, class_names)

    # ---- 置信度百分比 ----
    confidence_pct = round(float(np.clip(confidence * 100.0, CONFIDENCE_MIN, CONFIDENCE_MAX)), 2)

    # ---- 风险等级映射 ----
    alarm_to_risk = {"normal": "低", "attention": "中", "warning": "中", "alarm": "高"}

    # ---- 组装返回 ----
    data: Dict[str, Any] = {
        # 核心 — 优先使用中文名称
        "label": final_pred_cn,
        "diagnosisResult": final_pred_cn,
        "diagnosisName": final_pred_cn,
        "confidence": confidence_pct,
        "healthIndex": int(round(health_score)),
        "riskLevel": alarm_to_risk.get(alarm_level, "中"),
        "alarmLevel": alarm_level,
        "diagnosisDetail": (
            f"v6决策: {decision_reason} | "
            f"闭集:{closed_pred} conf={confidence:.4f} | "
            f"unk_ratio={v6_result['unknown_ratio']:.4f}"
        ),
        "diagnosis_detail": f"v6决策: {decision_reason_cn}",
        "decision_reason": decision_reason,

        # 中间指标
        "closedPrediction": closed_pred,
        "unknownRatio": round(float(v6_result["unknown_ratio"]), 6),
        "segmentConsistency": round(float(v6_result["segment_consistency"]), 6),
        "meanMahalanobis": round(float(v6_result["mean_mahalanobis"]), 6),
        "meanEntropy": round(float(v6_result["mean_entropy"]), 6),

        # 来源
        "source_name": source_name,
        "sourceName": source_name,

        # 可视化
        "topProbabilities": top_probs[:3],
        "evidence": evidence,
        "time_axis": time_axis,
        "time_data": time_data,
        "waveform": time_data,
        "freq_axis": freq_axis,
        "frequencyAxis": freq_axis,
        "freq_data": freq_data,
        "spectrum": freq_data,

        # 信号统计
        "rms": round(rms, 6),
        "latestRms": round(rms, 6),
        "peak": round(peak, 6),
        "latestPeak": round(peak, 6),

        # 元数据
        "sample_rate": sample_rate,
        "sampleRate": sample_rate,
        "count": len(time_data),
        "analysis_mode": "v6_mahalanobis",
    }

    if extra:
        data.update(extra)

    return data


def _build_bearing_frontend_payload(
    bearing_result: Dict[str, Any],
    raw_signal: np.ndarray,
    source_name: str,
    sample_rate: float = 16000.0,
    extra: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    sig = np.asarray(raw_signal, dtype=np.float64).reshape(-1)
    n = sig.size
    time_axis_full = np.arange(n, dtype=np.float64) / sample_rate if sample_rate > 0 else np.arange(n, dtype=np.float64)
    time_axis, time_data = v6.downsample_curve(time_axis_full, sig, max_points=DISPLAY_POINTS)
    spectrum = v6.compute_fft_full_curve(sig, fs=sample_rate, max_points=DISPLAY_SPECTRUM_POINTS, f_min=0.0)
    metrics = v6.compute_industrial_metrics(sig, fs=sample_rate)

    # use checkpoint class names dynamically
    class_names = bearing_result.get("class_names", bearing_class_names)
    prediction = str(bearing_result["prediction"])
    prediction_cn = BEARING_CLASS_CN_MAP.get(prediction, prediction)
    confidence = float(bearing_result["confidence"])
    confidence_pct = round(float(np.clip(confidence * 100.0, CONFIDENCE_MIN, CONFIDENCE_MAX)), 2)
    mean_entropy = float(bearing_result["mean_entropy"])
    segment_consistency = float(bearing_result["segment_consistency"])
    mean_probs = np.asarray(bearing_result["mean_probs"], dtype=np.float64)

    top_probs = [
        {"class": cname, "probability": round(float(prob) * 100.0, 2)}
        for cname, prob in zip(class_names, mean_probs)
    ]
    top_probs.sort(key=lambda x: x["probability"], reverse=True)

    # risk / health assessment
    is_normal = prediction.lower() in ("n", "normal", "healthy")
    risk_level = "低" if is_normal else "高"
    alarm_level = "normal" if is_normal else "alarm"
    health_score = confidence * 100.0 if is_normal else max(0.0, 100.0 - confidence * 100.0)

    evidence = [
        {
            "title": "模型类型",
            "desc": f"轴承诊断模型 (ResNet1D18, {len(class_names)}类)",
            "type": "info",
            "level": "信息",
        },
        {
            "title": "决策原因",
            "desc": str(bearing_result["decision_reason"]),
            "type": "info",
            "level": "信息",
        },
        {
            "title": "片段一致性",
            "desc": f"{segment_consistency:.4f}",
            "type": "success" if segment_consistency > 0.8 else "warning",
            "level": "高" if segment_consistency > 0.8 else "中",
        },
        {
            "title": "Average entropy",
            "desc": f"{mean_entropy:.4f}",
            "type": "info",
            "level": "信息",
        },
    ]

    data: Dict[str, Any] = {
        "label": prediction_cn,
        "diagnosisResult": prediction_cn,
        "diagnosisName": prediction_cn,
        "confidence": confidence_pct,
        "healthIndex": int(round(health_score)),
        "riskLevel": risk_level,
        "alarmLevel": alarm_level,
        "diagnosisDetail": (
            f"轴承诊断: {bearing_result['decision_reason']} | "
            f"预测:{prediction}({prediction_cn}) conf={confidence:.4f}"
        ),
        "diagnosis_detail": f"轴承诊断: {bearing_result['decision_reason']}",
        "decision_reason": str(bearing_result["decision_reason"]),
        "closedPrediction": prediction,
        "unknownRatio": 0.0,
        "segmentConsistency": round(segment_consistency, 6),
        "meanMahalanobis": 0.0,
        "meanEntropy": round(mean_entropy, 6),
        "source_name": source_name,
        "sourceName": source_name,
        "topProbabilities": top_probs[:len(class_names)],
        "evidence": evidence,
        "time_axis": time_axis,
        "time_data": time_data,
        "waveform": time_data,
        "freq_axis": spectrum["freq_hz"],
        "frequencyAxis": spectrum["freq_hz"],
        "freq_data": spectrum["amplitude"],
        "spectrum": spectrum["amplitude"],
        "rms": round(v6.safe_float(metrics["rms"]), 6),
        "latestRms": round(v6.safe_float(metrics["rms"]), 6),
        "peak": round(v6.safe_float(metrics["peak"]), 6),
        "latestPeak": round(v6.safe_float(metrics["peak"]), 6),
        "sample_rate": sample_rate,
        "sampleRate": sample_rate,
        "count": len(time_data),
        "analysis_mode": "bearing_resnet18",
        "modelType": "bearing",
        "modelVersion": "best_model.pth (ResNet1D18)",
        "numSegments": int(bearing_result["num_segments"]),
    }

    if extra:
        data.update(extra)
    return data


def _build_bearing_v8_frontend_payload(
    v8_result: Dict[str, Any],
    raw_signal: np.ndarray,
    source_name: str,
    sample_rate: float = 16000.0,
    extra: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    sig = np.asarray(raw_signal, dtype=np.float64).reshape(-1)
    n = sig.size
    time_axis_full = np.arange(n, dtype=np.float64) / sample_rate if sample_rate > 0 else np.arange(n, dtype=np.float64)
    time_axis, time_data = v6.downsample_curve(time_axis_full, sig, max_points=DISPLAY_POINTS)
    spectrum = v6.compute_fft_full_curve(sig, fs=sample_rate, max_points=DISPLAY_SPECTRUM_POINTS, f_min=0.0)
    metrics = v6.compute_industrial_metrics(sig, fs=sample_rate)

    class_names = v8_result.get("class_names", bearing_v8_class_names)
    prediction = str(v8_result["prediction"])
    prediction_cn = BEARING_CLASS_CN_MAP.get(prediction, prediction)
    confidence = float(v8_result["confidence"])
    confidence_pct = round(float(np.clip(confidence * 100.0, CONFIDENCE_MIN, CONFIDENCE_MAX)), 2)
    segment_consistency = float(v8_result["segment_consistency"])
    mean_entropy = float(v8_result.get("normalized_entropy", v8_result.get("mean_entropy", 0.0)))
    mean_probs = np.asarray(v8_result["mean_probs"], dtype=np.float64)

    top_probs = [
        {"class": cname, "probability": round(float(prob) * 100.0, 2)}
        for cname, prob in zip(class_names, mean_probs)
    ]
    top_probs.sort(key=lambda x: x["probability"], reverse=True)

    is_normal = prediction.lower() in ("healthy", "n", "normal")
    status = v8_result.get("status", "FAULT" if not is_normal else "HEALTHY")
    if status == "UNCERTAIN":
        risk_level = "中"
        alarm_level = "attention"
    elif is_normal:
        risk_level = "低"
        alarm_level = "normal"
    else:
        risk_level = "高"
        alarm_level = "alarm"
    health_score = confidence * 100.0 if is_normal else max(0.0, 100.0 - confidence * 100.0)

    accepted = v8_result.get("accepted", True)
    rejection_reasons = v8_result.get("rejection_reasons", [])

    evidence = [
        {
            "title": "模型类型",
            "desc": f"轴承诊断模型 (WDCNN-V8-DG, {len(class_names)}类)",
            "type": "info",
            "level": "信息",
        },
        {
            "title": "决策原因",
            "desc": str(v8_result["decision_reason"]),
            "type": "info",
            "level": "信息",
        },
        {
            "title": "片段一致性",
            "desc": f"{segment_consistency:.4f}",
            "type": "success" if segment_consistency > 0.8 else "warning",
            "level": "高" if segment_consistency > 0.8 else "中",
        },
        {
            "title": "Normalized entropy",
            "desc": f"{mean_entropy:.4f}",
            "type": "info",
            "level": "信息",
        },
        {
            "title": "Head agreement",
            "desc": str(v8_result.get("head_agreement", True)),
            "type": "success" if v8_result.get("head_agreement", True) else "warning",
            "level": "高" if v8_result.get("head_agreement", True) else "低",
        },
    ]
    if rejection_reasons:
        evidence.append({
            "title": "拒绝原因",
            "desc": ", ".join(rejection_reasons),
            "type": "warning",
            "level": "高",
        })

    data: Dict[str, Any] = {
        "label": prediction_cn,
        "diagnosisResult": prediction_cn,
        "diagnosisName": prediction_cn,
        "confidence": confidence_pct,
        "healthIndex": int(round(health_score)),
        "riskLevel": risk_level,
        "alarmLevel": alarm_level,
        "diagnosisDetail": (
            f"V8轴承诊断: {v8_result['decision_reason']} | "
            f"预测:{prediction}({prediction_cn}) conf={confidence:.4f} | "
            f"accepted={accepted}"
        ),
        "diagnosis_detail": f"V8轴承诊断: {v8_result['decision_reason']}",
        "decision_reason": str(v8_result["decision_reason"]),
        "closedPrediction": prediction,
        "unknownRatio": 0.0,
        "segmentConsistency": round(segment_consistency, 6),
        "meanMahalanobis": 0.0,
        "meanEntropy": round(mean_entropy, 6),
        "normalizedEntropy": round(mean_entropy, 6),
        "headAgreement": v8_result.get("head_agreement", True),
        "source_name": source_name,
        "sourceName": source_name,
        "topProbabilities": top_probs[:len(class_names)],
        "evidence": evidence,
        "time_axis": time_axis,
        "time_data": time_data,
        "waveform": time_data,
        "freq_axis": spectrum["freq_hz"],
        "frequencyAxis": spectrum["freq_hz"],
        "freq_data": spectrum["amplitude"],
        "spectrum": spectrum["amplitude"],
        "rms": round(v6.safe_float(metrics["rms"]), 6),
        "latestRms": round(v6.safe_float(metrics["rms"]), 6),
        "peak": round(v6.safe_float(metrics["peak"]), 6),
        "latestPeak": round(v6.safe_float(metrics["peak"]), 6),
        "sample_rate": sample_rate,
        "sampleRate": sample_rate,
        "count": len(time_data),
        "analysis_mode": "bearing_wdcnn_v8",
        "modelType": "bearing",
        "modelVersion": "WDCNN-V8-DG",
        "numSegments": int(v8_result["num_segments"]),
    }

    if extra:
        data.update(extra)
    return data


def _run_analysis(
    model_type: str,
    raw_signal: np.ndarray,
    source_name: str,
    analysis_mode: str,
    extra: Optional[Dict[str, Any]] = None,
    model_bundle: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    model_type = _normalize_model_type(model_type)
    mode = str(analysis_mode or "infer")
    if not mode.startswith(f"{model_type}_"):
        mode = f"{model_type}_{mode}"
    extra_payload = dict(extra or {})
    extra_payload["modelType"] = model_type
    selected = model_bundle or _default_model_bundle(
        model_type,
        GEAR_MODEL_VERSION if model_type == "gear" else BEARING_MODEL_VERSION,
    )
    params = selected["params"]
    extra_payload["modelVersion"] = selected["version"]

    inference_lock = _gear_inference_lock if model_type == "gear" else _bearing_inference_lock
    with inference_lock:
        if model_type == "gear":
            fs = float(extra_payload.get("sample_rate") or extra_payload.get("sampleRate")
                       or params.get("fs", 5120.0))
            v6_result = _diagnose_gear(raw_signal, source_name=source_name, bundle=selected)
            extra_payload["analysis_mode"] = mode
            return _build_frontend_payload(
                v6_result, raw_signal, source_name, sample_rate=fs,
                extra=extra_payload, class_names=selected["classes"])

        fs = float(extra_payload.get("sample_rate") or extra_payload.get("sampleRate")
                   or params.get("fs", 16000.0))
        engine = selected.get("engine", "resnet18")
        if engine == "v8":
            # Extract RPM from extra payload or use default
            rpm = extra_payload.get("rpm") or extra_payload.get("sampleRpm")
            bearing_result = _diagnose_bearing_v8(
                raw_signal, source_name=source_name, bundle=selected, rpm=rpm,
            )
            extra_payload["analysis_mode"] = mode
            return _build_bearing_v8_frontend_payload(
                bearing_result, raw_signal, source_name,
                sample_rate=fs, extra=extra_payload,
            )
        bearing_result = _diagnose_bearing(raw_signal, source_name=source_name, bundle=selected)
        extra_payload["analysis_mode"] = mode
        return _build_bearing_frontend_payload(
            bearing_result,
            raw_signal,
            source_name,
            sample_rate=fs,
            extra=extra_payload,
        )


def _build_health_payload() -> Dict[str, Any]:
    loaded_count = sum(int(model in ENABLED_MODELS and globals().get(f"{model}_model") is not None)
                       for model in VALID_MODEL_TYPES)
    expected_count = len(ENABLED_MODELS)
    bearing_any_loaded = bearing_v8_model is not None or bearing_model is not None
    return {
        "status": "ok" if loaded_count == expected_count else ("degraded" if loaded_count else "unavailable"),
        "device": str(DEVICE),
        "model_loaded": loaded_count == expected_count,
        "gear_model_loaded": gear_model is not None,
        "bearing_model_loaded": bearing_any_loaded,
        "bearing_v8_model_loaded": bearing_v8_model is not None,
        "gear_model_version": GEAR_MODEL_VERSION,
        "bearing_model_version": BEARING_V8_MODEL_VERSION if bearing_v8_model is not None else BEARING_MODEL_VERSION,
        "bearing_v8_model_version": BEARING_V8_MODEL_VERSION,
        "model_path": str(GEAR_MODEL_PATH),
        "gear_model_path": str(GEAR_MODEL_PATH),
        "bearing_model_path": str(BEARING_V8_MODEL_PATH),
        "bearing_v8_model_path": str(BEARING_V8_MODEL_PATH),
        "version": "v2_bearing_v8",
        "classes": gear_class_names,
        "gear_classes": gear_class_names,
        "bearing_classes": bearing_v8_class_names or bearing_class_names,
        "bearing_classes_cn": [BEARING_CLASS_CN_MAP.get(c, c) for c in (bearing_v8_class_names or bearing_class_names)],
        "win_len": gear_model_params.get("win_len"),
        "stride": gear_model_params.get("stride"),
        "fs": gear_model_params.get("fs"),
        "gear_params": gear_model_params,
        "bearing_params": bearing_v8_params or bearing_model_params,
        "model_errors": dict(model_load_errors),
        "batch_endpoint": True,
        "workers": INFERENCE_WORKERS,
        "enabled_models": sorted(ENABLED_MODELS),
    }




# =============================================================================
# API 端点
# =============================================================================

@app.get("/internal/health/live", dependencies=[Depends(require_internal_token)])
def health_live() -> Dict[str, Any]:
    return {"status": "ok"}


@app.get("/internal/health/ready", dependencies=[Depends(require_internal_token)])
def health_ready() -> JSONResponse:
    payload = _build_health_payload()
    return JSONResponse(content=payload, status_code=503 if payload["status"] == "unavailable" else 200)


@app.get("/internal/metrics", dependencies=[Depends(require_internal_token)])
def metrics() -> Response:
    return Response(content=generate_latest(), media_type=CONTENT_TYPE_LATEST)


@app.post("/internal/infer", dependencies=[Depends(require_internal_token)])
def infer(payload: Dict[str, Any]) -> Dict[str, Any]:
    model_type = _normalize_model_type(payload.get("modelType") or payload.get("model_type") or "gear")
    model_version = str(
        payload.get("modelVersion") or payload.get("model_version")
        or (GEAR_MODEL_VERSION if model_type == "gear" else BEARING_MODEL_VERSION)
    ).strip()
    log_context = {
        "requestId": payload.get("requestId"),
        "taskId": payload.get("taskId"),
        "deviceCode": payload.get("deviceCode"),
        "modelType": model_type,
        "modelVersion": model_version,
    }
    logger.info("Inference request accepted", extra=log_context)

    file_path = str(payload.get("filePath") or "")
    raw_signal_payload = payload.get("rawSignal")
    if file_path and raw_signal_payload is not None:
        raise HTTPException(status_code=400, detail="filePath and rawSignal are mutually exclusive.")
    if not file_path and raw_signal_payload is None:
        raise HTTPException(status_code=400, detail="filePath or rawSignal is required.")
    source_path = _resolve_trusted_input_path(file_path) if file_path else None
    if raw_signal_payload is not None:
        if not isinstance(raw_signal_payload, list) or not raw_signal_payload:
            raise HTTPException(status_code=400, detail="rawSignal must be a non-empty array.")
        if len(raw_signal_payload) > MAX_RAW_SIGNAL_SAMPLES:
            raise HTTPException(status_code=413, detail="rawSignal exceeds the configured sample limit.")
        try:
            raw_signal = np.asarray(raw_signal_payload, dtype=np.float64).reshape(-1)
        except (TypeError, ValueError) as exc:
            raise HTTPException(status_code=400, detail="rawSignal must contain numeric values.") from exc
        if not np.isfinite(raw_signal).all():
            raise HTTPException(status_code=400, detail="rawSignal contains NaN or infinite values.")
    started_at = time.perf_counter()
    try:
        model_bundle = _load_model_bundle(
            model_type,
            model_version,
            str(payload.get("modelArtifactUri") or ""),
            str(payload.get("modelArtifactSha256") or ""),
        )
    except (FileNotFoundError, RuntimeError, ValueError) as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc

    def _compute() -> Dict[str, Any]:
        params = model_bundle["params"]
        preferred_key = (
            params.get("signal_key", "DE_time") if model_type == "bearing"
            else params.get("signal_key", "sig_acc_5120")
        )
        if source_path is not None:
            raw_signal, _, file_meta = _load_signal_from_path(source_path, preferred_key)
        else:
            file_meta = {}
        extra = {
            "filename": payload.get("filename") or (source_path.name if source_path else "iotdb-vibration-frame.npy"),
        }
        if payload.get("sampleRate") or payload.get("sample_rate"):
            extra["sample_rate"] = float(payload.get("sampleRate") or payload.get("sample_rate"))
            extra["sampleRate"] = extra["sample_rate"]
        if file_meta.get("sample_rate"):
            extra["sample_rate"] = float(file_meta["sample_rate"])
            extra["sampleRate"] = float(file_meta["sample_rate"])
        # RPM: payload > file metadata > None (V8 model will use default 1500)
        rpm_value = payload.get("rpm") or payload.get("sampleRpm")
        if rpm_value is not None:
            try:
                extra["rpm"] = float(rpm_value)
            except (TypeError, ValueError):
                pass
        if "rpm" not in extra and file_meta.get("rpm"):
            extra["rpm"] = float(file_meta["rpm"])
        return _run_analysis(
            model_type,
            raw_signal,
            source_path.name if source_path else "iotdb-vibration-frame",
            payload.get("analysisMode", f"{model_type}_infer"),
            extra=extra,
            model_bundle=model_bundle,
        )

    result = dict(_compute() if source_path is None else _get_cached_or_compute(
        source_path, model_type, model_bundle["version"], _compute))
    result.update({
        "taskId": payload.get("taskId"),
        "requestId": payload.get("requestId"),
        "deviceCode": payload.get("deviceCode"),
        "pointId": payload.get("pointId"),
        "channelId": payload.get("channelId"),
        "filename": payload.get("filename") or (source_path.name if source_path else "iotdb-vibration-frame.npy"),
        "batchId": payload.get("batchId"),
        "sampleTime": payload.get("sampleTime"),
        "modelType": model_type,
        "modelVersion": model_bundle["version"],
    })
    _model_duration.labels(model_type=model_type).observe(time.perf_counter() - started_at)
    logger.info("Inference request completed", extra=log_context)
    return {"success": True, "data": result}


@app.post("/internal/preview", dependencies=[Depends(require_internal_token)])
def preview(payload: Dict[str, Any]) -> Dict[str, Any]:
    """Read-only signal preview; never loads a model or writes an inference result."""
    file_path = payload.get("filePath") or payload.get("file_path")
    if not file_path:
        raise HTTPException(status_code=400, detail="filePath is required")
    source_path = _resolve_trusted_input_path(str(file_path))
    max_points = max(256, min(int(payload.get("maxPoints", DISPLAY_POINTS)), 4096))
    raw_signal, source_name, metadata = _load_signal_from_path(source_path)
    if raw_signal.size < 2:
        raise HTTPException(status_code=422, detail="signal contains fewer than two samples")
    sample_rate = float(metadata.get("sample_rate") or payload.get("sampleRate") or 5120.0)
    if not np.isfinite(sample_rate) or sample_rate <= 0:
        raise HTTPException(status_code=422, detail="sample rate must be positive")

    signal = np.asarray(raw_signal, dtype=np.float64).reshape(-1)
    time_axis = np.arange(signal.size, dtype=np.float64) / sample_rate
    _, waveform = v6.downsample_curve(time_axis, signal, max_points=max_points)
    spectrum = v6.compute_fft_full_curve(
        signal, fs=sample_rate, max_points=min(max_points, DISPLAY_SPECTRUM_POINTS), f_min=0.0
    )
    metrics = v6.compute_industrial_metrics(signal, fs=sample_rate)
    features = {
        "rms": v6.safe_float(metrics.get("rms")),
        "peak": v6.safe_float(metrics.get("peak")),
        "peakToPeak": v6.safe_float(metrics.get("peak_to_peak")),
        "kurtosis": v6.safe_float(metrics.get("kurtosis")),
        "crestFactor": v6.safe_float(metrics.get("crest_factor")),
        "mainFrequency": v6.safe_float(metrics.get("frequency_center")),
    }
    return {"success": True, "data": {
        "source": "FILE",
        "sourceName": source_name,
        "sampleRate": sample_rate,
        "sampleTime": metadata.get("sample_time"),
        "waveform": waveform,
        "frequencyAxis": spectrum["freq_hz"],
        "spectrum": spectrum["amplitude"],
        "envelopeSpectrum": [],
        "waterfall": [],
        "features": features,
        "dataStatus": "full",
        "message": "已加载文件原始时域与频域数据",
        "metadata": {"rpm": metadata.get("rpm"), "sampleCount": int(signal.size)},
    }}


@app.post("/internal/infer/batch", dependencies=[Depends(require_internal_token)])
def infer_batch(payload: Dict[str, Any]) -> Dict[str, Any]:
    """Run independent realtime windows while preserving per-item failures."""
    items = payload.get("items") if isinstance(payload, dict) else None
    if not isinstance(items, list) or not items:
        _batch_requests.labels(status="rejected").inc()
        raise HTTPException(status_code=400, detail="items must be a non-empty array.")
    if len(items) > INFERENCE_BATCH_MAX_ITEMS:
        _batch_requests.labels(status="rejected").inc()
        raise HTTPException(
            status_code=413,
            detail=f"items exceeds the configured limit of {INFERENCE_BATCH_MAX_ITEMS}.",
        )

    results: List[Dict[str, Any]] = []
    for item in items:
        request = item if isinstance(item, dict) else {}
        request_id = request.get("requestId")
        model_type = str(request.get("modelType") or request.get("model_type") or "gear").lower()
        try:
            model_type = _normalize_model_type(model_type)
            response = infer(request)
            results.append({
                "requestId": request_id,
                "taskId": request.get("taskId"),
                "success": True,
                "data": response.get("data"),
            })
            _batch_items.labels(model_type=model_type, status="success").inc()
        except HTTPException as exc:
            message = str(exc.detail)
            results.append({
                "requestId": request_id,
                "taskId": request.get("taskId"),
                "success": False,
                "errorCode": f"HTTP_{exc.status_code}",
                "errorMessage": message,
            })
            _batch_items.labels(model_type=model_type, status="failure").inc()
        except Exception as exc:
            results.append({
                "requestId": request_id,
                "taskId": request.get("taskId"),
                "success": False,
                "errorCode": "INFERENCE_ERROR",
                "errorMessage": str(exc),
            })
            _batch_items.labels(model_type=model_type, status="failure").inc()

    _batch_requests.labels(status="success").inc()
    return {"success": True, "results": results}


# =============================================================================
# 入口
# =============================================================================
if __name__ == "__main__":
    uvicorn.run(app, host=INFERENCE_BIND_HOST, port=DEFAULT_PORT, reload=False)
