"""HTTP interface with lifecycle readiness, bounded uploads and backpressure."""
import asyncio
import logging
import os
import sqlite3
from contextlib import asynccontextmanager
from datetime import datetime
from threading import BoundedSemaphore
from typing import Annotated
from fastapi import FastAPI, HTTPException, Query, Request
from fastapi.responses import JSONResponse
from fastapi.exceptions import RequestValidationError
from pydantic import BaseModel, Field, ConfigDict, field_validator
if __package__ and __package__.startswith("diagnosis."):
    from diagnosis.database.database_manager import BatchConflict, ServiceBusy
else:
    from database.database_manager import BatchConflict, ServiceBusy

logger = logging.getLogger(__name__)
MAX_SAMPLES = int(os.getenv("DIAGNOSIS_MAX_SAMPLES", "1600000"))
MAX_BODY_BYTES = int(os.getenv("DIAGNOSIS_MAX_BODY_BYTES", "33554432"))
MAX_PENDING = int(os.getenv("DIAGNOSIS_MAX_PENDING", "4"))
if min(MAX_SAMPLES, MAX_BODY_BYTES, MAX_PENDING) <= 0:
    raise ValueError("request limits must be positive")

class DiagnoseRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", allow_inf_nan=False)
    device_id: str = Field(min_length=1, max_length=128)
    batch_id: str = Field(min_length=1, max_length=128)
    rpm: float = Field(gt=0)
    sampling_rate: int = Field(gt=0)
    signal: list[float] = Field(max_length=MAX_SAMPLES)
    collected_at: datetime | None = None

    @field_validator("device_id", "batch_id")
    @classmethod
    def nonblank(cls, value):
        if not value.strip():
            raise ValueError("identifier cannot be blank")
        return value

    @field_validator("collected_at")
    @classmethod
    def timezone_required(cls, value):
        if value is not None and value.tzinfo is None:
            raise ValueError("collected_at must include a timezone")
        return value

class UploadGuard:
    def __init__(self, app, max_body_bytes, max_pending):
        self.app = app
        self.max_body_bytes = max_body_bytes
        self.slots = BoundedSemaphore(max_pending)

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http" or scope["path"] != "/diagnose" or scope["method"] != "POST":
            return await self.app(scope, receive, send)
        if not self.slots.acquire(blocking=False):
            return await JSONResponse({"detail": "service busy"}, 503,
                                      headers={"Retry-After": "2"})(scope, receive, send)
        try:
            # Count actual chunks, including chunked bodies, before JSON decoding.
            body = bytearray()
            deadline = asyncio.get_running_loop().time() + 30
            while True:
                remaining = deadline - asyncio.get_running_loop().time()
                try:
                    message = await asyncio.wait_for(receive(), timeout=max(0, remaining))
                except asyncio.TimeoutError:
                    await JSONResponse({"detail": "upload timed out"}, 408)(scope, receive, send)
                    return
                if message["type"] == "http.disconnect":
                    return
                chunk = message.get("body", b"")
                if len(body) + len(chunk) > self.max_body_bytes:
                    return await JSONResponse({"detail": "request body too large"}, 413)(scope, receive, send)
                body.extend(chunk)
                if not message.get("more_body", False):
                    break
            delivered = False
            async def replay():
                nonlocal delivered
                if not delivered:
                    delivered = True
                    return {"type": "http.request", "body": bytes(body), "more_body": False}
                return await receive()
            await self.app(scope, replay, send)
        finally:
            self.slots.release()


def create_app(service_factory=None):
    @asynccontextmanager
    async def lifespan(application):
        if application.state.diagnosis_service is None:
            factory = service_factory
            if factory is None:
                if __package__.startswith("diagnosis."):
                    from diagnosis.main import create_service
                else:
                    from main import create_service
                factory = create_service
            application.state.diagnosis_service = factory()
        try:
            yield
        finally:
            service = application.state.diagnosis_service
            if service is not None:
                service.close()
            application.state.diagnosis_service = None

    application = FastAPI(title="Industrial Bearing Diagnosis API", version="1.1", lifespan=lifespan)
    application.state.diagnosis_service = None
    application.add_middleware(UploadGuard, max_body_bytes=MAX_BODY_BYTES, max_pending=MAX_PENDING)

    def service_for(request):
        service = request.app.state.diagnosis_service
        if service is None or not service.ready:
            raise HTTPException(503, "SERVICE_NOT_READY", headers={"Retry-After": "2"})
        return service

    @application.exception_handler(RequestValidationError)
    async def validation_error(request, exc):
        # Never echo huge signals or nonfinite values into JSON error responses.
        detail = [{"loc": list(e["loc"]), "msg": e["msg"], "type": e["type"]}
                  for e in exc.errors()[:20]]
        return JSONResponse({"detail": detail}, 422)

    @application.exception_handler(ServiceBusy)
    async def busy(request, exc):
        return JSONResponse({"detail": str(exc)}, 503, headers={"Retry-After": "2"})

    @application.exception_handler(BatchConflict)
    async def conflict(request, exc):
        return JSONResponse({"detail": str(exc)}, 409)

    @application.exception_handler(sqlite3.Error)
    async def database_error(request, exc):
        logger.exception("database failure", exc_info=exc)
        return JSONResponse({"detail": "STORAGE_UNAVAILABLE"}, 503)

    @application.get("/health")
    def health(request: Request):
        service = service_for(request)
        if not service.database.is_ready():
            raise HTTPException(503, "STORAGE_UNAVAILABLE")
        return {"service": "running", "model": "loaded", "database": "ready",
                "model_version": service.model_service.ms.model_version}

    @application.post("/diagnose")
    def diagnose(payload: DiagnoseRequest, request: Request):
        service = service_for(request)
        try:
            return service.run(**payload.model_dump())
        except (BatchConflict, ServiceBusy, sqlite3.Error):
            raise
        except ValueError as exc:
            raise HTTPException(422, str(exc)) from exc
        except Exception as exc:
            logger.exception("inference failed device=%s batch=%s", payload.device_id, payload.batch_id)
            raise HTTPException(500, "INFERENCE_FAILED") from exc

    @application.get("/device/{device_id}")
    def device_status(device_id: str, request: Request):
        result = service_for(request).database.get_device_status(device_id)
        if result is None:
            raise HTTPException(404, "DEVICE_NOT_FOUND")
        return result

    @application.get("/history/{device_id}")
    def history(device_id: str, request: Request, limit: Annotated[int, Query(ge=1, le=1000)] = 20):
        return service_for(request).database.get_latest_results(device_id, limit)

    return application

app = create_app()

def set_diagnosis_service(service):
    """Compatibility injection; the application lifespan owns shutdown."""
    app.state.diagnosis_service = service
