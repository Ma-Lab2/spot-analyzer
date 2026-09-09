"""Domain records shared by the analysis core and its adapters."""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from types import MappingProxyType
from typing import Any, Mapping

import numpy as np


def _freeze(value: Any) -> Any:
    if isinstance(value, Mapping):
        return MappingProxyType({key: _freeze(item) for key, item in value.items()})
    if isinstance(value, (list, tuple)):
        return tuple(_freeze(item) for item in value)
    return value


class FlowStatus(str, Enum):
    PROCESSING = "processing"
    INPUT_INVALID = "input_invalid"
    INPUT_DECODE_FAILED = "input_decode_failed"
    PARAMETER_INVALID = "parameter_invalid"
    ANALYSIS_FAILED = "analysis_failed"
    COMPUTED = "computed"
    EXPORTED = "exported"
    CANCELLED = "cancelled"
    EXPORT_FAILED = "export_failed"


class MeasurementStatus(str, Enum):
    VALID = "valid"
    CAUTION = "caution"
    INVALID = "invalid"
    UNAVAILABLE = "unavailable"


@dataclass(frozen=True)
class InputImage:
    """Decoded input image; the core never receives a path or file handle."""

    data: np.ndarray
    dtype: str = "float64"
    bit_depth: int | None = None
    channels: int = 1
    channels_identical: bool = False
    encoding_semantic: str = "unconfirmed"
    encoding_semantic_confirmed: bool = False
    asset_id: str = "memory-input"
    sha256: str | None = None
    uri_hint: str = "memory://input"
    byte_order: str | None = None
    metadata: Mapping[str, Any] = field(default_factory=dict)
    read_status: str = "read"

    def __post_init__(self) -> None:
        array = np.asarray(self.data)
        if array.ndim != 2:
            raise ValueError("InputImage.data must be a two-dimensional grayscale array")
        if not np.issubdtype(array.dtype, np.number):
            raise ValueError("InputImage.data must contain numeric intensity values")
        copied = np.array(array, dtype=np.float64, copy=True)
        copied.setflags(write=False)
        object.__setattr__(self, "data", copied)
        object.__setattr__(self, "dtype", str(array.dtype))
        object.__setattr__(self, "metadata", _freeze(self.metadata))
        if self.read_status not in {"read", "unavailable", "hash_mismatch"}:
            raise ValueError("read_status is not supported")


@dataclass(frozen=True)
class AnalysisRegion:
    x: int
    y: int
    width: int
    height: int
    confirmed: bool = True

    def bounds(self) -> tuple[int, int, int, int]:
        return self.x, self.y, self.x + self.width, self.y + self.height


@dataclass(frozen=True)
class SpatialCalibration:
    x_unit_per_pixel: float | None = None
    y_unit_per_pixel: float | None = None
    physical_unit: str = "um"
    source: str | None = None
    confirmation: str = "missing"

    def __post_init__(self) -> None:
        if self.confirmation not in {"confirmed", "provisional", "missing"}:
            raise ValueError("calibration confirmation is not supported")
        values = (self.x_unit_per_pixel, self.y_unit_per_pixel)
        if self.confirmation == "missing":
            if any(value is not None for value in values):
                raise ValueError("missing calibration must not contain scale values")
            return
        if any(value is None or value <= 0 for value in values):
            raise ValueError("confirmed or provisional calibration requires positive x/y scales")
        if not self.source:
            raise ValueError("confirmed or provisional calibration requires a source")

    @property
    def is_usable(self) -> bool:
        return (
            self.x_unit_per_pixel is not None
            and self.y_unit_per_pixel is not None
            and self.x_unit_per_pixel > 0
            and self.y_unit_per_pixel > 0
            and self.confirmation in {"confirmed", "provisional"}
        )


@dataclass(frozen=True)
class PreprocessingConfiguration:
    background_source: str = "confirmed_region_affine"
    bad_pixel_policy: str = "mask_only"
    negative_value_policy: str = "preserve_signed"
    filtering: str = "none"
    dpc: str = "none"
    advanced_processing_enabled: bool = False
    background_signal_sigma_threshold: float = 3.0
    background_signal_peak_fraction: float = 0.10
    background_mask_dilation_pixels: int = 1
    background_huber_delta: float = 1.345
    background_max_iterations: int = 50
    convergence_tolerance: float = 1e-8
    localization_sigma_pixels: float = 1.0
    localization_truncate_sigma: float = 3.0
    core_threshold_fraction: float = 0.5
    core_invalid_fraction: float = 0.8
    core_caution_fraction: float = 0.95
    snr_invalid_threshold: float = 5.0
    snr_caution_threshold: float = 10.0
    multiple_peak_relative_threshold: float = 0.20
    multiple_peak_noise_threshold: float = 5.0
    multiple_peak_min_support_pixels: int = 9
    multiple_peak_min_separation_pixels: float = 3.0
    version: str = "preprocessing-v1"

    def __post_init__(self) -> None:
        if self.background_source != "confirmed_region_affine":
            raise ValueError("only confirmed-region affine background is implemented")
        if self.bad_pixel_policy != "mask_only":
            raise ValueError("only mask-only bad-pixel handling is implemented")
        if self.negative_value_policy != "preserve_signed":
            raise ValueError("signed corrected intensity must be preserved")
        if self.filtering != "none" or self.dpc != "none" or self.advanced_processing_enabled:
            raise ValueError("advanced preprocessing is not implemented")
        frozen_parameters = (
            self.background_signal_sigma_threshold,
            self.background_signal_peak_fraction,
            self.background_mask_dilation_pixels,
            self.background_huber_delta,
            self.background_max_iterations,
            self.convergence_tolerance,
            self.localization_sigma_pixels,
            self.localization_truncate_sigma,
            self.core_threshold_fraction,
            self.core_invalid_fraction,
            self.core_caution_fraction,
            self.snr_invalid_threshold,
            self.snr_caution_threshold,
            self.multiple_peak_relative_threshold,
            self.multiple_peak_noise_threshold,
            self.multiple_peak_min_support_pixels,
            self.multiple_peak_min_separation_pixels,
            self.version,
        )
        expected = (3.0, 0.10, 1, 1.345, 50, 1e-8, 1.0, 3.0, 0.5, 0.8, 0.95, 5.0, 10.0, 0.20, 5.0, 9, 3.0, "preprocessing-v1")
        if frozen_parameters != expected:
            raise ValueError("preprocessing parameters do not match preprocessing-v1")


@dataclass(frozen=True)
class AnalysisModel:
    name: str = "rotated_elliptical_gaussian"
    sigma_min_pixels: float = 0.5
    sigma_max_roi_fraction: float = 0.5
    fallback_sigma_roi_fraction: float = 1.0 / 6.0
    optimizer: str = "bounded-least-squares-trf"
    optimizer_tolerance: float = 1e-12
    optimizer_max_evaluations: int = 1000
    version: str = "gaussian-model-v1"

    def __post_init__(self) -> None:
        expected = (
            "rotated_elliptical_gaussian",
            0.5,
            0.5,
            1.0 / 6.0,
            "bounded-least-squares-trf",
            1e-12,
            1000,
            "gaussian-model-v1",
        )
        actual = (
            self.name,
            self.sigma_min_pixels,
            self.sigma_max_roi_fraction,
            self.fallback_sigma_roi_fraction,
            self.optimizer,
            self.optimizer_tolerance,
            self.optimizer_max_evaluations,
            self.version,
        )
        if actual != expected:
            raise ValueError("analysis model parameters do not match gaussian-model-v1")


@dataclass(frozen=True)
class AnalysisConfiguration:
    region: AnalysisRegion
    calibration: SpatialCalibration = field(default_factory=SpatialCalibration)
    preprocessing: PreprocessingConfiguration = field(default_factory=PreprocessingConfiguration)
    model: AnalysisModel = field(default_factory=AnalysisModel)
    background_region: AnalysisRegion | None = None
    rref_pixels: float | None = None
    analysis_contract: str = "analysis-contract-v1"
    standard_profile: str = "standard-profile-v1"
    quality_profile: str = "quality-profile-v1"
    profile_validation: str = "provisional"
    algorithm_version: str = "analysis-core-v1"
    bad_pixel_coordinates: tuple[tuple[int, int], ...] = ()
    bad_pixel_mask_version: str = "bad-pixel-mask-v1"

    def __post_init__(self) -> None:
        if self.profile_validation != "provisional":
            raise ValueError("profile validation is locked to provisional until Issue #11 acceptance")
        coordinates = tuple(tuple(coordinate) for coordinate in self.bad_pixel_coordinates)
        for coordinate in coordinates:
            if len(coordinate) != 2 or any(not isinstance(value, (int, np.integer)) for value in coordinate):
                raise ValueError("bad_pixel_coordinates must contain integer (x, y) pairs")
        if len(set(coordinates)) != len(coordinates):
            raise ValueError("bad_pixel_coordinates must not contain duplicates")
        if not self.bad_pixel_mask_version.strip():
            raise ValueError("bad_pixel_mask_version must not be empty")
        object.__setattr__(self, "bad_pixel_coordinates", coordinates)


@dataclass(frozen=True)
class Metric:
    value: Any
    unit: str
    status: MeasurementStatus
    reason_codes: tuple[str, ...] = ()
    method_version: str = ""
    physical_value: Any = None

    def __post_init__(self) -> None:
        object.__setattr__(self, "value", _freeze(self.value))
        object.__setattr__(self, "reason_codes", tuple(self.reason_codes))
        object.__setattr__(self, "physical_value", _freeze(self.physical_value))

    @property
    def reported_value(self) -> Any:
        if self.status in {MeasurementStatus.INVALID, MeasurementStatus.UNAVAILABLE}:
            return None
        return self.value


@dataclass(frozen=True)
class AnalysisRecord:
    record_id: str
    analysis_fingerprint: str
    flow_status: FlowStatus
    summary_status: MeasurementStatus
    metrics: Mapping[str, Metric]
    diagnostics: Mapping[str, Any]
    configuration: AnalysisConfiguration
    input_shape: tuple[int, int]
    input_intensity: np.ndarray
    corrected_intensity: np.ndarray
    positive_intensity: np.ndarray
    fitted_intensity: np.ndarray
    fit_residual_intensity: np.ndarray
    measurement_mask: np.ndarray
    core_mask: np.ndarray
    input_metadata: Mapping[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        for name in (
            "input_intensity",
            "corrected_intensity",
            "positive_intensity",
            "fitted_intensity",
            "fit_residual_intensity",
        ):
            array = np.array(getattr(self, name), dtype=np.float64, copy=True)
            array.setflags(write=False)
            object.__setattr__(self, name, array)
        for name in ("measurement_mask", "core_mask"):
            array = np.array(getattr(self, name), dtype=bool, copy=True)
            array.setflags(write=False)
            object.__setattr__(self, name, array)
        object.__setattr__(self, "metrics", MappingProxyType(dict(self.metrics)))
        object.__setattr__(self, "diagnostics", _freeze(self.diagnostics))
        object.__setattr__(self, "input_metadata", _freeze(self.input_metadata))

    def reportable_metrics(self) -> dict[str, Any]:
        length_metrics = {
            "peak_center",
            "gaussian_fwhm_major",
            "gaussian_fwhm_minor",
            "gaussian_ellipticity",
            "gaussian_angle",
            "moment_d4sigma_major",
            "moment_d4sigma_minor",
            "moment_angle",
            "ee50",
            "ee80",
            "centroid_offset",
        }
        reportable: dict[str, Any] = {}
        for key, metric in self.metrics.items():
            pixel = {
                "value": metric.reported_value,
                "unit": metric.unit,
                "status": metric.status.value,
                "reason_codes": list(metric.reason_codes),
                "method_version": metric.method_version,
            }
            if key not in length_metrics:
                reportable[key] = pixel
                continue
            calibration = self.configuration.calibration
            physical_reasons = set(metric.reason_codes)
            if metric.status in {MeasurementStatus.INVALID, MeasurementStatus.UNAVAILABLE}:
                physical_status = metric.status
                physical_value = None
            elif metric.physical_value is None:
                physical_status = MeasurementStatus.UNAVAILABLE
                physical_value = None
                physical_reasons = {"calibration_missing" if not calibration.is_usable else "physical_domain_unavailable"}
            else:
                physical_status = metric.status
                physical_value = metric.physical_value
                if calibration.confirmation == "provisional":
                    physical_status = MeasurementStatus.CAUTION
                    physical_reasons.add("calibration_provisional")
            reportable[key] = {
                "domains": {
                    "pixel": pixel,
                    "physical": {
                        "value": physical_value,
                        "unit": (
                            "deg"
                            if key in {"gaussian_angle", "moment_angle"}
                            else "fraction"
                            if key == "gaussian_ellipticity"
                            else calibration.physical_unit
                        ),
                        "status": physical_status.value,
                        "reason_codes": sorted(physical_reasons),
                        "method_version": metric.method_version,
                    },
                }
            }
        return reportable


@dataclass(frozen=True)
class AnalysisOutcome:
    flow_status: FlowStatus
    record: AnalysisRecord | None
    diagnostics: tuple[Mapping[str, Any], ...] = ()
