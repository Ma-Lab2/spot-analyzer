"""Traceable focal spot analysis primitives."""

from .core import analyze
from .detection import (
    DEFAULT_DETECTION_PROFILE,
    AutomaticAnalysisProposal,
    FocalSpotCandidate,
    FocalSpotDetectionProfile,
    detect_main_focal_spot,
    propose_auto_analysis,
)
from .input import DecodeOutcome, decode_png
from .validation import (
    run_complete_validation,
    run_identity_validation,
    run_issue10_validation,
    run_performance_baseline,
    run_real_fixture_validation,
    run_report_validation,
    run_validation,
)
from .models import (
    AnalysisConfiguration,
    AnalysisModel,
    AnalysisOutcome,
    AnalysisRecord,
    AnalysisRegion,
    FlowStatus,
    InputImage,
    MeasurementStatus,
    Metric,
    PreprocessingConfiguration,
    SpatialCalibration,
)

__all__ = [
    "AnalysisConfiguration",
    "AnalysisModel",
    "AutomaticAnalysisProposal",
    "DEFAULT_DETECTION_PROFILE",
    "FocalSpotCandidate",
    "FocalSpotDetectionProfile",
    "DecodeOutcome",
    "AnalysisOutcome",
    "AnalysisRecord",
    "AnalysisRegion",
    "FlowStatus",
    "InputImage",
    "MeasurementStatus",
    "Metric",
    "PreprocessingConfiguration",
    "SpatialCalibration",
    "analyze",
    "decode_png",
    "detect_main_focal_spot",
    "propose_auto_analysis",
    "run_complete_validation",
    "run_identity_validation",
    "run_issue10_validation",
    "run_performance_baseline",
    "run_real_fixture_validation",
    "run_report_validation",
    "run_validation",
]
