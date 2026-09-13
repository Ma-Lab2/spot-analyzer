"""Domain records shared by the analysis core and its adapters."""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
import math
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
    TIMEOUT = "timeout"
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
    background_frame: "InputImage | None" = None
    background_match_unverified: bool = False

    def __post_init__(self) -> None:
        array = np.asarray(self.data)
        if array.ndim != 2:
            raise ValueError("InputImage.data must be a two-dimensional grayscale array")
        if not np.issubdtype(array.dtype, np.number):
            raise ValueError("InputImage.data must contain numeric intensity values")
        copied = np.array(array, dtype=np.float64, copy=True)
        copied.setflags(write=False)
        object.__setattr__(self, "data", copied)
        # ``data`` is an immutable analysis-plane float array, while ``dtype``
        # records the source sample representation when an adapter supplies it.
        object.__setattr__(self, "dtype", self.dtype if self.dtype != "float64" else str(array.dtype))
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
    # Kept as a legacy display-unit default for v1 object construction; the
    # missing confirmation state still makes every physical value unavailable.
    physical_unit: str | None = "um"
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
        if not isinstance(self.physical_unit, str) or not self.physical_unit.strip():
            raise ValueError("confirmed or provisional calibration requires a physical unit")
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


def _profile_default(section: str, name: str) -> Any:
    # Import lazily so the profile loader can remain independent of domain
    # dataclasses.  The JSON profile is the sole source of these defaults.
    from .profiles import get_analysis_profile

    return get_analysis_profile()[section][name]


@dataclass(frozen=True)
class PreprocessingConfiguration:
    background_source: str = field(default_factory=lambda: _profile_default("preprocessing", "background_source"))
    bad_pixel_policy: str = field(default_factory=lambda: _profile_default("preprocessing", "bad_pixel_policy"))
    negative_value_policy: str = field(default_factory=lambda: _profile_default("preprocessing", "negative_value_policy"))
    filtering: str = field(default_factory=lambda: _profile_default("preprocessing", "filtering"))
    dpc: str = field(default_factory=lambda: _profile_default("preprocessing", "dpc"))
    advanced_processing_enabled: bool = field(default_factory=lambda: _profile_default("preprocessing", "advanced_processing_enabled"))
    background_signal_sigma_threshold: float = field(default_factory=lambda: _profile_default("preprocessing", "background_signal_sigma_threshold"))
    background_signal_peak_fraction: float = field(default_factory=lambda: _profile_default("preprocessing", "background_signal_peak_fraction"))
    background_mask_dilation_pixels: int = field(default_factory=lambda: _profile_default("preprocessing", "background_mask_dilation_pixels"))
    background_huber_delta: float = field(default_factory=lambda: _profile_default("preprocessing", "background_huber_delta"))
    background_max_iterations: int = field(default_factory=lambda: _profile_default("preprocessing", "background_max_iterations"))
    convergence_tolerance: float = field(default_factory=lambda: _profile_default("preprocessing", "convergence_tolerance"))
    localization_sigma_pixels: float = field(default_factory=lambda: _profile_default("preprocessing", "localization_sigma_pixels"))
    localization_truncate_sigma: float = field(default_factory=lambda: _profile_default("preprocessing", "localization_truncate_sigma"))
    core_threshold_fraction: float = field(default_factory=lambda: _profile_default("preprocessing", "core_threshold_fraction"))
    core_invalid_fraction: float = field(default_factory=lambda: _profile_default("preprocessing", "core_invalid_fraction"))
    core_caution_fraction: float = field(default_factory=lambda: _profile_default("preprocessing", "core_caution_fraction"))
    snr_invalid_threshold: float = field(default_factory=lambda: _profile_default("preprocessing", "snr_invalid_threshold"))
    snr_caution_threshold: float = field(default_factory=lambda: _profile_default("preprocessing", "snr_caution_threshold"))
    multiple_peak_relative_threshold: float = field(default_factory=lambda: _profile_default("preprocessing", "multiple_peak_relative_threshold"))
    multiple_peak_noise_threshold: float = field(default_factory=lambda: _profile_default("preprocessing", "multiple_peak_noise_threshold"))
    multiple_peak_min_support_pixels: int = field(default_factory=lambda: _profile_default("preprocessing", "multiple_peak_min_support_pixels"))
    multiple_peak_min_separation_pixels: float = field(default_factory=lambda: _profile_default("preprocessing", "multiple_peak_min_separation_pixels"))
    advanced_interpolation_sigma_pixels: float = field(default_factory=lambda: _profile_default("preprocessing", "advanced_interpolation_sigma_pixels"))
    advanced_interpolation_radius_pixels: int = field(default_factory=lambda: _profile_default("preprocessing", "advanced_interpolation_radius_pixels"))
    advanced_filter_sigma_pixels: float = field(default_factory=lambda: _profile_default("preprocessing", "advanced_filter_sigma_pixels"))
    advanced_filter_radius_pixels: int = field(default_factory=lambda: _profile_default("preprocessing", "advanced_filter_radius_pixels"))
    advanced_dpc_sigma_pixels: float = field(default_factory=lambda: _profile_default("preprocessing", "advanced_dpc_sigma_pixels"))
    advanced_dpc_radius_pixels: int = field(default_factory=lambda: _profile_default("preprocessing", "advanced_dpc_radius_pixels"))
    version: str = field(default_factory=lambda: _profile_default("preprocessing", "version"))

    def __post_init__(self) -> None:
        if self.background_source not in {"confirmed_region_affine", "matched_frame"}:
            raise ValueError("background_source is not supported")
        if self.bad_pixel_policy not in {"mask_only", "interpolate"}:
            raise ValueError("bad_pixel_policy is not supported")
        if self.negative_value_policy != "preserve_signed":
            raise ValueError("signed corrected intensity must be preserved")
        if self.filtering not in {"none", "gaussian"} or self.dpc not in {"none", "gradient"}:
            raise ValueError("preprocessing option is not supported")
        if self.advanced_processing_enabled and self.filtering == "none" and self.dpc == "none" and self.bad_pixel_policy == "mask_only":
            raise ValueError("advanced preprocessing requires an explicit branch option")
        integral_fields = {
            "background_mask_dilation_pixels": self.background_mask_dilation_pixels,
            "background_max_iterations": self.background_max_iterations,
            "multiple_peak_min_support_pixels": self.multiple_peak_min_support_pixels,
            "advanced_interpolation_radius_pixels": self.advanced_interpolation_radius_pixels,
            "advanced_filter_radius_pixels": self.advanced_filter_radius_pixels,
            "advanced_dpc_radius_pixels": self.advanced_dpc_radius_pixels,
        }
        if any(
            isinstance(value, bool) or not isinstance(value, (int, np.integer))
            for value in integral_fields.values()
        ):
            raise ValueError("preprocessing pixel and iteration counts must be integers")
        finite_fields = {
            "background_signal_sigma_threshold": self.background_signal_sigma_threshold,
            "background_signal_peak_fraction": self.background_signal_peak_fraction,
            "background_huber_delta": self.background_huber_delta,
            "convergence_tolerance": self.convergence_tolerance,
            "localization_sigma_pixels": self.localization_sigma_pixels,
            "localization_truncate_sigma": self.localization_truncate_sigma,
            "core_threshold_fraction": self.core_threshold_fraction,
            "core_invalid_fraction": self.core_invalid_fraction,
            "core_caution_fraction": self.core_caution_fraction,
            "snr_invalid_threshold": self.snr_invalid_threshold,
            "snr_caution_threshold": self.snr_caution_threshold,
            "multiple_peak_relative_threshold": self.multiple_peak_relative_threshold,
            "multiple_peak_noise_threshold": self.multiple_peak_noise_threshold,
            "multiple_peak_min_separation_pixels": self.multiple_peak_min_separation_pixels,
            "advanced_interpolation_sigma_pixels": self.advanced_interpolation_sigma_pixels,
            "advanced_filter_sigma_pixels": self.advanced_filter_sigma_pixels,
            "advanced_dpc_sigma_pixels": self.advanced_dpc_sigma_pixels,
        }
        if any(not math.isfinite(float(value)) for value in finite_fields.values()):
            raise ValueError("preprocessing parameters must be finite")
        if self.background_max_iterations <= 0 or self.convergence_tolerance <= 0:
            raise ValueError("background fit limits must be positive")
        if any(
            value <= 0
            for value in (
                self.advanced_interpolation_sigma_pixels,
                self.advanced_filter_sigma_pixels,
                self.advanced_dpc_sigma_pixels,
            )
        ) or any(
            value < 0
            for value in (
                self.advanced_interpolation_radius_pixels,
                self.advanced_filter_radius_pixels,
                self.advanced_dpc_radius_pixels,
            )
        ):
            raise ValueError("advanced preprocessing parameters must be positive")
        if self.background_signal_sigma_threshold <= 0 or self.background_signal_peak_fraction < 0:
            raise ValueError("background signal thresholds must be nonnegative")
        if self.background_mask_dilation_pixels < 0 or self.background_huber_delta <= 0:
            raise ValueError("background mask and robust-fit parameters are invalid")
        if not 0 <= self.core_threshold_fraction <= 1:
            raise ValueError("core threshold fraction must be between zero and one")
        if not 0 <= self.core_invalid_fraction <= self.core_caution_fraction <= 1:
            raise ValueError("core validity fractions must be ordered between zero and one")
        if not 0 <= self.snr_invalid_threshold <= self.snr_caution_threshold:
            raise ValueError("SNR thresholds must be ordered and nonnegative")
        if not 0 <= self.multiple_peak_relative_threshold <= 1:
            raise ValueError("multiple-peak relative threshold must be between zero and one")
        if self.multiple_peak_noise_threshold < 0 or self.multiple_peak_min_support_pixels <= 0:
            raise ValueError("multiple-peak thresholds must be nonnegative")
        if self.multiple_peak_min_separation_pixels < 0:
            raise ValueError("multiple-peak separation must be nonnegative")
        fixed_quality_values = {
            "core_threshold_fraction": 0.5,
            "core_invalid_fraction": 0.8,
            "core_caution_fraction": 0.95,
            "snr_invalid_threshold": 5.0,
            "snr_caution_threshold": 10.0,
            "multiple_peak_relative_threshold": 0.20,
            "multiple_peak_noise_threshold": 5.0,
            "multiple_peak_min_support_pixels": 9,
            "multiple_peak_min_separation_pixels": 3.0,
        }
        fixed_algorithm_values = {
            "background_signal_sigma_threshold": 3.0,
            "background_signal_peak_fraction": 0.10,
            "background_mask_dilation_pixels": 1,
            "background_huber_delta": 1.345,
            "background_max_iterations": 50,
            "convergence_tolerance": 1e-8,
            "localization_sigma_pixels": 1.0,
            "localization_truncate_sigma": 3.0,
        }
        if any(getattr(self, name) != expected for name, expected in {
            **fixed_quality_values,
            **fixed_algorithm_values,
        }.items()):
            raise ValueError("standard preprocessing parameters are fixed in the standard profile")
        if not self.version.strip():
            raise ValueError("preprocessing version must not be empty")


@dataclass(frozen=True)
class AnalysisModel:
    name: str = field(default_factory=lambda: _profile_default("model", "name"))
    sigma_min_pixels: float = field(default_factory=lambda: _profile_default("model", "sigma_min_pixels"))
    sigma_max_roi_fraction: float = field(default_factory=lambda: _profile_default("model", "sigma_max_roi_fraction"))
    fallback_sigma_roi_fraction: float = field(default_factory=lambda: _profile_default("model", "fallback_sigma_roi_fraction"))
    optimizer: str = field(default_factory=lambda: _profile_default("model", "optimizer"))
    optimizer_tolerance: float = field(default_factory=lambda: _profile_default("model", "optimizer_tolerance"))
    optimizer_max_evaluations: int = field(default_factory=lambda: _profile_default("model", "optimizer_max_evaluations"))
    version: str = field(default_factory=lambda: _profile_default("model", "version"))

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


def _default_detection_profile_parameters() -> dict[str, Any]:
    # Kept as a plain mapping for worker snapshots; the authoritative typed
    # validation and profile identity live in spot_analyzer.detection.
    return {
        "signal_sigma_threshold": 5.0,
        "signal_peak_fraction": 0.05,
        "isolated_hot_pixel_max_support": 1,
        "isolated_hot_pixel_sigma_threshold": 8.0,
        "minimum_candidate_support_pixels": 4,
        "minimum_candidate_separation_pixels": 4.0,
        "roi_safety_margin_pixels": 12,
        "roi_min_width_pixels": 48,
        "roi_min_height_pixels": 48,
        "background_min_width_pixels": 16,
        "background_min_height_pixels": 16,
        "background_protection_dilation_pixels": 2,
        "background_min_support_pixels": 30,
    }


@dataclass(frozen=True)
class AnalysisConfiguration:
    # ``None`` is reserved for the core's automatic proposal entry point.  A
    # resolved AnalysisRecord always contains a confirmed rectangular region.
    region: AnalysisRegion | None
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
    detection_profile_version: str = "focal-spot-detection-v1"
    detection_profile_parameters: Mapping[str, Any] = field(default_factory=_default_detection_profile_parameters)
    automatic_background: bool = False

    def __post_init__(self) -> None:
        if self.profile_validation != "provisional":
            raise ValueError("profile validation is locked to provisional until Issue #11 acceptance")
        raw_coordinates = tuple(tuple(coordinate) for coordinate in self.bad_pixel_coordinates)
        for coordinate in raw_coordinates:
            if len(coordinate) != 2 or any(not isinstance(value, (int, np.integer)) for value in coordinate):
                raise ValueError("bad_pixel_coordinates must contain integer (x, y) pairs")
        coordinates = tuple(tuple(int(value) for value in coordinate) for coordinate in raw_coordinates)
        if len(set(coordinates)) != len(coordinates):
            raise ValueError("bad_pixel_coordinates must not contain duplicates")
        if not self.bad_pixel_mask_version.strip():
            raise ValueError("bad_pixel_mask_version must not be empty")
        if not self.detection_profile_version.strip():
            raise ValueError("detection_profile_version must not be empty")
        object.__setattr__(self, "bad_pixel_coordinates", coordinates)
        # Keep a plain copied mapping so dataclasses.asdict remains usable by
        # the versioned worker snapshot seam.
        object.__setattr__(self, "detection_profile_parameters", dict(self.detection_profile_parameters))


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
    standard_corrected_intensity: np.ndarray | None = None

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
        if self.standard_corrected_intensity is not None:
            array = np.array(self.standard_corrected_intensity, dtype=np.float64, copy=True)
            array.setflags(write=False)
            object.__setattr__(self, "standard_corrected_intensity", array)
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
                            else calibration.physical_unit if calibration.is_usable else "unavailable"
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
