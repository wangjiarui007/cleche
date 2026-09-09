"""Run with python main.py, or uvicorn api.diagnosis_api:app --workers 1."""
import logging
import os
from pathlib import Path
if __package__ and (__package__ == "diagnosis" or __package__.startswith("diagnosis.")):
    from diagnosis.model.model_service import ModelService
else:
    from model.model_service import ModelService
if __package__ and (__package__ == "diagnosis" or __package__.startswith("diagnosis.")):
    from diagnosis.sevice.diagnosis_service import IndustrialDiagnosisService
else:
    from sevice.diagnosis_service import IndustrialDiagnosisService

BASE_DIR = Path(__file__).resolve().parent

def create_service():
    model_service = ModelService(
        checkpoint_path=os.getenv("DIAGNOSIS_MODEL_PATH", str(BASE_DIR / "model/best_model.pth")),
        device=os.getenv("DIAGNOSIS_DEVICE", "cuda"),
        batch_size=int(os.getenv("DIAGNOSIS_BATCH_SIZE", "32")),
    )
    return IndustrialDiagnosisService(
        model_service=model_service,
        database_path=os.getenv("DIAGNOSIS_DATABASE_PATH", str(BASE_DIR / "database/diagnosis.db")),
        max_samples=int(os.getenv("DIAGNOSIS_MAX_SAMPLES", "1600000")),
        stale_after_seconds=float(os.getenv("DIAGNOSIS_STALE_SECONDS", "60")),
        max_data_age_seconds=float(os.getenv("DIAGNOSIS_MAX_DATA_AGE_SECONDS", "300")),
        clip_threshold=float(os.environ["DIAGNOSIS_CLIP_THRESHOLD"]) if "DIAGNOSIS_CLIP_THRESHOLD" in os.environ else None,
    )

if __name__ == "__main__":
    import uvicorn
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s")
    uvicorn.run("diagnosis.api.diagnosis_api:app" if __package__ == "diagnosis" else "api.diagnosis_api:app", host=os.getenv("DIAGNOSIS_HOST", "127.0.0.1"),
                port=int(os.getenv("DIAGNOSIS_PORT", "8000")), workers=1)
