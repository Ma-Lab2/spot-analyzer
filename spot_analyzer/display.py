"""Read-only display projections for an :class:`AnalysisRecord`.

Display settings change presentation only; this module never mutates or
recomputes measurement arrays and never contributes to an analysis fingerprint.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Mapping

import numpy as np


LAYER_NAMES = (
    "input_image",
    "corrected_intensity",
    "positive_signal",
    "fitted_intensity",
    "fit_residual",
    "measurement_mask",
    "core_mask",
)


@dataclass(frozen=True)
class DisplayUnavailable:
    reason_code: str
    message: str


@dataclass(frozen=True)
class DisplayLayer:
    name: str
    record_id: str
    data: np.ndarray | DisplayUnavailable


@dataclass(frozen=True)
class DisplayCurves:
    record_id: str
    profile_x: Any
    profile_x_actual: Any
    profile_x_fitted: Any
    profile_y: Any
    profile_y_actual: Any
    profile_y_fitted: Any
    energy_radius: Any
    energy_fraction: Any
    profile_axis_unit: str | None
    energy_radius_unit: str | None
    energy_fraction_unit: str | None

    @property
    def actual_profile(self) -> Any:
        return self.profile_x_actual

    @property
    def fitted_profile(self) -> Any:
        return self.profile_x_fitted


def _curve(value: Any, code: str, message: str) -> Any:
    if value is None:
        return _unavailable(code, message)
    if isinstance(value, DisplayUnavailable):
        return value
    return np.array(value, dtype=float, copy=True)


@dataclass(frozen=True)
class DisplayProjection:
    record_id: str
    analysis_fingerprint: str
    layers: tuple[DisplayLayer, ...]
    curves: DisplayCurves
    center_pixel: Mapping[str, Any] | DisplayUnavailable
    roi: Mapping[str, Any] | DisplayUnavailable
    axis_units: tuple[str, str]


def _unavailable(code: str, message: str) -> DisplayUnavailable:
    return DisplayUnavailable(code, message)


def _array(record: Any, name: str) -> np.ndarray | DisplayUnavailable:
    value = getattr(record, name, None)
    if value is None:
        return _unavailable("layer_unavailable", f"{name} is unavailable")
    return np.array(value, copy=True)


def _layer_array(record: Any, layer_name: str) -> np.ndarray | DisplayUnavailable:
    attribute = {
        "input_image": "input_intensity",
        "corrected_intensity": "corrected_intensity",
        "positive_signal": "positive_intensity",
        "fitted_intensity": "fitted_intensity",
        "fit_residual": "fit_residual_intensity",
        "measurement_mask": "measurement_mask",
        "core_mask": "core_mask",
    }[layer_name]
    return _array(record, attribute)


def project(record: Any) -> DisplayProjection:
    """Create a read-only projection tied to one analysis record.

    The returned arrays are copies, so display transforms cannot alter the
    record's measurement source. Missing diagnostics are explicit N/A values.
    """
    record_id = record.record_id
    layers = tuple(DisplayLayer(name, record_id, _layer_array(record, name)) for name in LAYER_NAMES)
    diagnostics = record.diagnostics
    curves_data = diagnostics.get("report_curves", {}) if isinstance(diagnostics, Mapping) else {}
    if not isinstance(curves_data, Mapping):
        curves_data = {}
    curves = DisplayCurves(
        record_id,
        _curve(curves_data.get("profile_x"), "curve_unavailable", "X profile axis unavailable"),
        _curve(curves_data.get("profile_x_actual", curves_data.get("profile")), "curve_unavailable", "X actual profile unavailable"),
        _curve(curves_data.get("profile_x_fitted", curves_data.get("fitted_profile")), "curve_unavailable", "X fitted profile unavailable"),
        _curve(curves_data.get("profile_y"), "curve_unavailable", "Y profile axis unavailable"),
        _curve(curves_data.get("profile_y_actual"), "curve_unavailable", "Y actual profile unavailable"),
        _curve(curves_data.get("profile_y_fitted"), "curve_unavailable", "Y fitted profile unavailable"),
        _curve(curves_data.get("energy_radius"), "curve_unavailable", "cumulative energy radius unavailable"),
        _curve(curves_data.get("energy_fraction"), "curve_unavailable", "cumulative energy fraction unavailable"),
        curves_data.get("profile_axis_unit", "px"),
        curves_data.get("energy_radius_unit"),
        curves_data.get("energy_fraction_unit", "fraction"),
    )
    center = curves_data.get("center_pixel", _unavailable("center_unavailable", "analysis center unavailable"))
    configuration = record.configuration
    roi = getattr(configuration, "analysis_region", None)
    roi = roi if roi is not None else _unavailable("roi_unavailable", "analysis region unavailable")
    calibration = getattr(configuration, "spatial_calibration", None)
    units = getattr(calibration, "units", None) or "px"
    return DisplayProjection(
        record_id,
        record.analysis_fingerprint,
        layers,
        curves,
        center,
        roi,
        (units, units),
    )
