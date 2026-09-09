"""Compatibility import for the original misspelled module name."""
if __package__ and (__package__ == "diagnosis" or __package__.startswith("diagnosis.")):
    from diagnosis.model.model_service import ModelService
else:
    from model.model_service import ModelService
__all__ = ["ModelService"]
