"""Versioned, UI-independent analysis core prototype."""

from __future__ import annotations

from dataclasses import asdict, replace
import hashlib
import math
import uuid
from typing import Any, Mapping

import numpy as np
import rfc8785
from scipy.ndimage import binary_dilation, gaussian_filter, label, maximum_filter
from scipy.optimize import least_squares

from .models import (
    AnalysisConfiguration,
    AnalysisOutcome,
    AnalysisRecord,
    AnalysisRegion,
    FlowStatus,
    InputImage,
    MeasurementStatus,
    Metric,
)


_METHOD = "analysis-core-v1"
_MASK_VERSION = "measurement-mask-v1"
_CANONICALIZER_VERSION = "rfc8785-python-0.1.4"


def _reason_tuple(reasons: set[str]) -> tuple[str, ...]:
    return tuple(sorted(reasons))


def _cap_status(
    status: MeasurementStatus,
    configuration: AnalysisConfiguration,
    reasons: set[str],
) -> MeasurementStatus:
    if status == MeasurementStatus.VALID and configuration.profile_validation == "provisional":
        reasons.add("provisional_profile")
        return MeasurementStatus.CAUTION
    return status


def _metric(
    value: Any,
    unit: str,
    status: MeasurementStatus,
    reasons: set[str],
    configuration: AnalysisConfiguration,
    method_version: str = _METHOD,
    physical_value: Any = None,
) -> Metric:
    local_reasons = set(reasons)
    status = _cap_status(status, configuration, local_reasons)
    return Metric(value, unit, status, _reason_tuple(local_reasons), method_version, physical_value)


def _invalid_metric(unit: str, reason: str, configuration: AnalysisConfiguration) -> Metric:
    return _metric(None, unit, MeasurementStatus.INVALID, {reason}, configuration)


def _region_slice(region: AnalysisRegion) -> tuple[slice, slice]:
    return slice(region.y, region.y + region.height), slice(region.x, region.x + region.width)


def _regions_overlap(first: AnalysisRegion, second: AnalysisRegion) -> bool:
    first_x0, first_y0, first_x1, first_y1 = first.bounds()
    second_x0, second_y0, second_x1, second_y1 = second.bounds()
    return not (
        first_x1 <= second_x0
        or second_x1 <= first_x0
        or first_y1 <= second_y0
        or second_y1 <= first_y0
    )


def _mask_hash(mask: np.ndarray) -> str:
    """Hash a canonical C-order boolean mask for audit and repeatability."""

    canonical = np.ascontiguousarray(np.asarray(mask, dtype=np.uint8))
    return "sha256-" + hashlib.sha256(canonical.tobytes()).hexdigest()


def _bad_pixel_mask(
    shape: tuple[int, int],
    configuration: AnalysisConfiguration,
) -> tuple[np.ndarray, tuple[tuple[int, int], ...]]:
    mask = np.zeros(shape, dtype=bool)
    out_of_bounds: list[tuple[int, int]] = []
    height, width = shape
    for x, y in configuration.bad_pixel_coordinates:
        coordinate = (int(x), int(y))
        if 0 <= coordinate[0] < width and 0 <= coordinate[1] < height:
            mask[coordinate[1], coordinate[0]] = True
        else:
            out_of_bounds.append(coordinate)
    return mask, tuple(out_of_bounds)


def _subpixel_peak(image: np.ndarray, valid_mask: np.ndarray | None = None) -> tuple[float, float] | None:
    """Find a deterministic smoothed maximum and fit a quadratic in each axis."""

    finite = np.isfinite(image)
    if valid_mask is not None:
        finite &= valid_mask
    if not np.any(finite):
        return None
    values = image[finite]
    floor = float(np.min(values)) - max(1.0, float(np.ptp(values)) + 1.0)
    safe = np.where(finite, image, floor)
    smoothed = gaussian_filter(safe, sigma=1.0, radius=3, mode="nearest")
    peak_y, peak_x = np.unravel_index(int(np.argmax(smoothed)), smoothed.shape)
    x_offset = 0.0
    y_offset = 0.0
    if 0 < peak_x < image.shape[1] - 1:
        left, middle, right = smoothed[peak_y, peak_x - 1 : peak_x + 2]
        denominator = left - 2.0 * middle + right
        if denominator != 0:
            x_offset = 0.5 * (left - right) / denominator
    if 0 < peak_y < image.shape[0] - 1:
        top, middle, bottom = smoothed[peak_y - 1 : peak_y + 2, peak_x]
        denominator = top - 2.0 * middle + bottom
        if denominator != 0:
            y_offset = 0.5 * (top - bottom) / denominator
    return float(peak_x + x_offset), float(peak_y + y_offset)


def _plane_design(
    shape: tuple[int, int],
    region: AnalysisRegion,
) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    height, width = shape
    y, x = np.indices((region.height, region.width), dtype=np.float64)
    global_x = x + region.x
    global_y = y + region.y
    normalized_x = global_x / max(width - 1, 1) * 2.0 - 1.0
    normalized_y = global_y / max(height - 1, 1) * 2.0 - 1.0
    design = np.column_stack(
        (np.ones(normalized_x.size), normalized_x.ravel(), normalized_y.ravel())
    )
    return design, normalized_x, normalized_y


def _matching_background(
    image: InputImage,
    region: AnalysisRegion,
    background_frame: InputImage | None,
) -> tuple[np.ndarray | None, dict[str, Any]]:
    """Use a background frame only when every required acquisition field matches."""
    if background_frame is None:
        return None, {}
    required = ("exposure", "gain", "temperature", "optical_path", "focal_length", "acquisition_batch", "roi")
    fields = {
        "exposure": image.metadata.get("exposure"),
        "gain": image.metadata.get("gain"),
        "temperature": image.metadata.get("temperature"),
        "optical_path": image.metadata.get("optical_path"),
        "focal_length": image.metadata.get("focal_length"),
        "acquisition_batch": image.metadata.get("acquisition_batch"),
        "roi": image.metadata.get("roi", {"x": region.x, "y": region.y, "width": region.width, "height": region.height}),
    }
    background_fields = {key: background_frame.metadata.get(key) for key in required}
    missing = [key for key in required if fields[key] is None or background_fields[key] is None]
    mismatched = [key for key in required if key not in missing and fields[key] != background_fields[key]]
    if image.background_match_unverified:
        mismatched.append("explicit_user_override")
    shape_match = background_frame.data.shape == image.data.shape
    format_match = (
        background_frame.channels == image.channels
        and background_frame.bit_depth == image.bit_depth
    )
    diagnostics = {
        "background_frame_present": True,
        "background_frame_shape_match": shape_match,
        "background_frame_format_match": format_match,
        "background_match_required_fields": list(required),
        "background_match_missing_fields": missing,
        "background_match_mismatched_fields": mismatched,
    }
    if missing or mismatched or not shape_match or not format_match:
        diagnostics["background_match_status"] = "unverified"
        diagnostics["background_match_reason"] = "background_match_unverified"
        return None, diagnostics
    diagnostics["background_match_status"] = "matched"
    diagnostics["background_source"] = "matched_frame"
    frame = np.asarray(background_frame.data, dtype=np.float64)
    finite = np.isfinite(frame)
    if not np.any(finite):
        diagnostics["background_match_status"] = "unverified"
        diagnostics["background_match_reason"] = "background_frame_unavailable"
        return None, diagnostics
    residual = frame[finite] - float(np.median(frame[finite]))
    scale = 1.4826 * float(np.median(np.abs(residual - np.median(residual))))
    diagnostics.update({
        "background_fit_status": "converged",
        "background_valid_pixels": int(np.count_nonzero(finite)),
        "background_excluded_pixels": int(np.count_nonzero(~finite)),
        "background_noise_sigma": scale,
        "background_residual_rms": float(np.sqrt(np.mean(residual**2))),
        "background_normalized_rms": float(np.sqrt(np.mean(residual**2)) / scale) if scale > 1e-8 else 0.0,
        "background_mask_hash": _mask_hash(finite),
    })
    return frame, diagnostics


def _background(
    image: np.ndarray,
    region: AnalysisRegion,
    background_region: AnalysisRegion | None,
    bad_pixel_mask: np.ndarray,
    saturated_mask: np.ndarray,
) -> tuple[np.ndarray, dict[str, Any]]:
    """Fit a robust affine background after explicit protected-region exclusions."""

    height, width = image.shape
    if background_region is None:
        border = max(8, min(region.width, region.height) // 8)
        background_region = AnalysisRegion(region.x, region.y, region.width, border)
    y_slice, x_slice = _region_slice(background_region)
    sample = image[y_slice, x_slice]
    design, normalized_x, normalized_y = _plane_design(image.shape, background_region)
    values = sample.ravel()
    bad_values = bad_pixel_mask[y_slice, x_slice].ravel()
    saturated_values = saturated_mask[y_slice, x_slice].ravel()
    finite = np.isfinite(values)
    base_valid = finite & ~bad_values & ~saturated_values
    diagnostics: dict[str, Any] = {
        "background_source": "protected_region",
        "background_mask_version": _MASK_VERSION,
        "background_total_pixels": int(values.size),
        "background_bad_pixel_count": int(np.count_nonzero(bad_values & finite)),
        "background_saturated_pixel_count": int(np.count_nonzero(saturated_values & finite)),
        "background_nonfinite_pixel_count": int(np.count_nonzero(~finite)),
        "background_x_coverage": int(np.unique(normalized_x.ravel()[base_valid]).size),
        "background_y_coverage": int(np.unique(normalized_y.ravel()[base_valid]).size),
    }

    def insufficient(valid: np.ndarray, reason: str) -> tuple[np.ndarray, dict[str, Any]]:
        full_background = np.zeros_like(image, dtype=np.float64)
        diagnostics.update(
            {
                "background_valid_pixels": int(np.count_nonzero(valid)),
                "background_excluded_pixels": int(values.size - np.count_nonzero(valid)),
                "background_signal_excluded_pixels": int(np.count_nonzero(base_valid & ~valid)),
                "background_fit_status": "insufficient_support",
                "background_reason": reason,
                "background_mask_hash": _mask_hash(valid.reshape(sample.shape)),
            }
        )
        return full_background, diagnostics

    if (
        int(np.count_nonzero(base_valid)) < 30
        or np.unique(normalized_x.ravel()[base_valid]).size < 3
        or np.unique(normalized_y.ravel()[base_valid]).size < 3
    ):
        return insufficient(base_valid, "background_support_insufficient")

    coefficients, *_ = np.linalg.lstsq(design[base_valid], values[base_valid], rcond=None)
    initial_residual = values - design @ coefficients
    initial_scale = 1.4826 * float(
        np.median(
            np.abs(
                initial_residual[base_valid]
                - np.median(initial_residual[base_valid])
            )
        )
    )
    roi_y, roi_x = _region_slice(region)
    roi_design, _, _ = _plane_design(image.shape, region)
    roi_values = image[roi_y, roi_x].ravel()
    roi_valid = (
        np.isfinite(roi_values)
        & ~bad_pixel_mask[roi_y, roi_x].ravel()
        & ~saturated_mask[roi_y, roi_x].ravel()
    )
    roi_residual = roi_values - roi_design @ coefficients
    roi_positive = roi_residual[roi_valid]
    roi_peak = float(np.max(roi_positive)) if roi_positive.size else 0.0
    noise_threshold = 3.0 * initial_scale
    relative_peak_threshold = 0.10 * max(roi_peak, 0.0)
    signal_threshold = min(noise_threshold, relative_peak_threshold)
    signal_candidates = base_valid & (
        (initial_residual > noise_threshold)
        | (initial_residual > relative_peak_threshold)
    )
    signal_excluded = binary_dilation(
        signal_candidates.reshape(sample.shape),
        structure=np.ones((3, 3), dtype=bool),
        iterations=1,
        border_value=0,
    ).ravel() & base_valid
    valid = base_valid & ~signal_excluded
    diagnostics.update(
        {
            "background_signal_threshold": float(signal_threshold),
            "background_noise_threshold": float(noise_threshold),
            "background_relative_peak_threshold": float(relative_peak_threshold),
            "background_signal_excluded_pixels": int(np.count_nonzero(signal_excluded)),
            "background_initial_noise_sigma": float(initial_scale),
        }
    )
    if (
        int(np.count_nonzero(valid)) < 30
        or np.unique(normalized_x.ravel()[valid]).size < 3
        or np.unique(normalized_y.ravel()[valid]).size < 3
    ):
        return insufficient(valid, "background_support_insufficient")

    converged = False
    iterations = 0
    for iterations in range(1, 51):
        residual = values - design @ coefficients
        center = float(np.median(residual[valid]))
        scale = 1.4826 * float(np.median(np.abs(residual[valid] - center)))
        if scale <= np.finfo(float).eps:
            converged = True
            break
        weights = np.minimum(
            1.0,
            1.345 * scale / np.maximum(np.abs(residual), np.finfo(float).eps),
        )
        weighted_design = design[valid] * weights[valid, None]
        weighted_values = values[valid] * weights[valid]
        updated, *_ = np.linalg.lstsq(weighted_design, weighted_values, rcond=None)
        parameter_delta = float(np.max(np.abs(updated - coefficients)))
        objective_delta = float(
            abs(np.sum((residual[valid]) ** 2) - np.sum((values[valid] - design[valid] @ updated) ** 2))
        )
        coefficients = updated
        if parameter_delta < 1e-8 and objective_delta < 1e-8:
            converged = True
            break
    if not converged:
        return insufficient(valid, "background_fit_not_converged")

    full_y, full_x = np.indices(image.shape, dtype=np.float64)
    full_x = full_x / max(width - 1, 1) * 2.0 - 1.0
    full_y = full_y / max(height - 1, 1) * 2.0 - 1.0
    fitted = coefficients[0] + coefficients[1] * full_x + coefficients[2] * full_y
    residual = values - design @ coefficients
    median_residual = float(np.median(residual[valid]))
    mad = 1.4826 * float(np.median(np.abs(residual[valid] - median_residual)))
    rms = float(np.sqrt(np.mean(residual[valid] ** 2)))
    normalized_rms = rms / mad if mad > 1e-8 else (0.0 if rms <= 1e-6 else math.inf)
    diagnostics.update(
        {
            "background_coefficients": [float(value) for value in coefficients],
            "background_valid_pixels": int(np.count_nonzero(valid)),
            "background_excluded_pixels": int(values.size - np.count_nonzero(valid)),
            "background_noise_sigma": float(mad),
            "background_residual_rms": rms,
            "background_normalized_rms": normalized_rms,
            "background_x_coverage": int(np.unique(normalized_x.ravel()[valid]).size),
            "background_y_coverage": int(np.unique(normalized_y.ravel()[valid]).size),
            "background_fit_iterations": iterations,
            "background_fit_status": "converged",
            "background_mask_hash": _mask_hash(valid.reshape(sample.shape)),
        }
    )
    return fitted, diagnostics


def _advanced_preprocess(
    corrected: np.ndarray,
    bad_pixel_mask: np.ndarray,
    configuration: AnalysisConfiguration,
) -> tuple[np.ndarray, dict[str, Any]]:
    """Apply the explicitly configured exploratory branch without replacing standard data."""
    advanced = np.array(corrected, dtype=np.float64, copy=True)
    steps: list[str] = []
    if configuration.bad_pixel_policy == "interpolate":
        invalid = bad_pixel_mask | ~np.isfinite(advanced)
        if np.any(invalid):
            replacement = gaussian_filter(np.where(invalid, 0.0, advanced), sigma=1.0, radius=2)
            support = gaussian_filter((~invalid).astype(float), sigma=1.0, radius=2)
            advanced[invalid] = replacement[invalid] / np.maximum(support[invalid], 1e-12)
        steps.append("bad_pixel_interpolation-v1")
    if configuration.filtering == "gaussian":
        advanced = gaussian_filter(advanced, sigma=1.0, radius=3, mode="nearest")
        steps.append("gaussian_filter-v1")
    if configuration.dpc == "gradient":
        smooth = gaussian_filter(advanced, sigma=2.0, radius=6, mode="nearest")
        advanced = advanced - smooth + float(np.mean(smooth))
        steps.append("dpc_gradient-v1")
    return advanced, {"enabled": True, "steps": steps, "version": "advanced-preprocessing-v1"}


def _moment_metrics(
    positive: np.ndarray,
    center_xy: tuple[float, float],
    valid_mask: np.ndarray | None = None,
) -> tuple[float, float, float, float]:
    y, x = np.indices(positive.shape, dtype=np.float64)
    if valid_mask is None:
        valid_mask = np.isfinite(positive)
    valid = valid_mask & np.isfinite(positive)
    weights = np.where(valid, positive, 0.0).ravel()
    total = float(weights.sum())
    if total <= 0:
        raise ValueError("no positive signal")
    x_flat, y_flat = x.ravel(), y.ravel()
    dx = x_flat - center_xy[0]
    dy = y_flat - center_xy[1]
    covariance = np.array(
        [
            [np.sum(weights * dx * dx), np.sum(weights * dx * dy)],
            [np.sum(weights * dx * dy), np.sum(weights * dy * dy)],
        ]
    ) / total
    eigenvalues, eigenvectors = np.linalg.eigh(covariance)
    order = np.argsort(eigenvalues)[::-1]
    eigenvalues = np.maximum(eigenvalues[order], 0.0)
    vector = eigenvectors[:, order[0]]
    angle = math.degrees(math.atan2(vector[1], vector[0])) % 180.0
    centroid = (
        float(np.sum(weights * x_flat) / total),
        float(np.sum(weights * y_flat) / total),
    )
    return (
        4.0 * math.sqrt(float(eigenvalues[0])),
        4.0 * math.sqrt(float(eigenvalues[1])),
        angle,
        math.hypot(centroid[0] - center_xy[0], centroid[1] - center_xy[1]),
    )


def _encircled_radius(
    positive: np.ndarray,
    center_xy: tuple[float, float],
    target: float,
    valid_mask: np.ndarray | None = None,
    x_scale: float = 1.0,
    y_scale: float = 1.0,
) -> float | None:
    y, x = np.indices(positive.shape, dtype=np.float64)
    if valid_mask is None:
        valid_mask = np.isfinite(positive)
    valid = valid_mask & np.isfinite(positive)
    radii = np.hypot(
        (x - center_xy[0]) * x_scale,
        (y - center_xy[1]) * y_scale,
    ).ravel()[valid.ravel()]
    values = positive.ravel()[valid.ravel()]
    order = np.argsort(radii, kind="mergesort")
    radii, values = radii[order], values[order]
    total = float(values.sum())
    if total <= 0:
        return None
    cumulative = np.cumsum(values) / total
    index = int(np.searchsorted(cumulative, target, side="left"))
    if index >= len(radii):
        return None
    if index == 0:
        return float(radii[0])
    before = cumulative[index - 1]
    span = cumulative[index] - before
    fraction = 0.0 if span <= 0 else (target - before) / span
    return float(radii[index - 1] + fraction * (radii[index] - radii[index - 1]))


def _half_height_sigma(
    profile: np.ndarray,
    valid: np.ndarray,
    center_index: int,
    peak: float,
) -> float | None:
    if peak <= 0 or not (0 <= center_index < profile.size) or not valid[center_index]:
        return None
    half_height = 0.5 * peak
    if profile[center_index] < half_height:
        return None
    left = center_index
    while left > 0 and valid[left - 1] and profile[left - 1] >= half_height:
        left -= 1
    right = center_index
    while right < profile.size - 1 and valid[right + 1] and profile[right + 1] >= half_height:
        right += 1
    if left == 0 or right == profile.size - 1 or not valid[left - 1] or not valid[right + 1]:
        return None

    def crossing(lower_index: int, upper_index: int) -> float:
        lower_value = float(profile[lower_index])
        upper_value = float(profile[upper_index])
        if upper_value == lower_value:
            return float(lower_index)
        return float(lower_index) + (half_height - lower_value) / (upper_value - lower_value)

    left_crossing = crossing(left - 1, left)
    right_crossing = crossing(right, right + 1)
    fwhm = right_crossing - left_crossing
    if not np.isfinite(fwhm) or fwhm <= 0:
        return None
    return max(0.5, fwhm / math.sqrt(8.0 * math.log(2.0)))


def _gaussian_initial_widths(
    positive: np.ndarray,
    valid_mask: np.ndarray,
    center_xy: tuple[float, float],
) -> tuple[float, float, tuple[str, ...]]:
    height, width = positive.shape
    center_x = min(width - 1, max(0, int(math.floor(center_xy[0] + 0.5))))
    center_y = min(height - 1, max(0, int(math.floor(center_xy[1] + 0.5))))
    neighborhood = positive[
        max(0, center_y - 1) : min(height, center_y + 2),
        max(0, center_x - 1) : min(width, center_x + 2),
    ]
    peak = float(np.max(neighborhood)) if neighborhood.size else 0.0
    sigma_x = _half_height_sigma(positive[center_y, :], valid_mask[center_y, :], center_x, peak)
    sigma_y = _half_height_sigma(positive[:, center_x], valid_mask[:, center_x], center_y, peak)
    reasons: list[str] = []
    fallback = max(0.5, min(width, height) / 6.0)
    if sigma_x is None:
        sigma_x = fallback
        reasons.append("gaussian_initial_width_x_fallback")
    if sigma_y is None:
        sigma_y = fallback
        reasons.append("gaussian_initial_width_y_fallback")
    upper = min(width, height) / 2.0
    return min(sigma_x, upper), min(sigma_y, upper), tuple(reasons)


def _gaussian_fit(
    corrected: np.ndarray,
    center_xy: tuple[float, float],
    background_at_center: float,
    valid_mask: np.ndarray,
    positive: np.ndarray,
) -> tuple[dict[str, float], np.ndarray, dict[str, Any]]:
    """Fit signed corrected intensity over the shared fit/residual valid domain."""

    height, width = corrected.shape
    y, x = np.indices(corrected.shape, dtype=np.float64)
    fit_valid = valid_mask & np.isfinite(corrected)
    if int(np.count_nonzero(fit_valid)) < 7:
        raise RuntimeError("gaussian fit has insufficient valid pixels")
    fit_values = corrected[fit_valid]
    peak = float(np.max(positive[fit_valid])) if np.any(fit_valid) else 0.0
    sigma_x, sigma_y, initialization_reasons = _gaussian_initial_widths(
        positive,
        fit_valid,
        center_xy,
    )
    upper_sigma = min(width, height) / 2.0
    initial = np.array(
        [
            max(peak, 1e-6),
            background_at_center,
            center_xy[0],
            center_xy[1],
            sigma_x,
            sigma_y,
            0.0,
        ]
    )
    lower = np.array([0.0, -np.inf, 0.0, 0.0, 0.5, 0.5, -math.pi])
    upper = np.array([np.inf, np.inf, width - 1.0, height - 1.0, upper_sigma, upper_sigma, math.pi])

    fit_x = x[fit_valid]
    fit_y = y[fit_valid]

    def model(parameters: np.ndarray, model_x: np.ndarray = x, model_y: np.ndarray = y) -> np.ndarray:
        amplitude, offset, cx, cy, sx, sy, theta = parameters
        u = np.cos(theta) * (model_x - cx) + np.sin(theta) * (model_y - cy)
        v = -np.sin(theta) * (model_x - cx) + np.cos(theta) * (model_y - cy)
        return offset + amplitude * np.exp(-0.5 * ((u / sx) ** 2 + (v / sy) ** 2))

    try:
        result = least_squares(
            lambda parameters: (model(parameters, fit_x, fit_y) - fit_values),
            initial,
            bounds=(lower, upper),
            method="trf",
            xtol=1e-12,
            ftol=1e-12,
            gtol=1e-12,
            max_nfev=1000,
        )
        fitted = result.x
        fitted_image = model(fitted)
        degrees_of_freedom = max(int(fit_values.size - fitted.size), 1)
        try:
            covariance = np.linalg.pinv(result.jac.T @ result.jac) * (2.0 * result.cost / degrees_of_freedom)
            parameter_uncertainty = np.sqrt(np.maximum(np.diag(covariance), 0.0))
            covariance_payload: list[list[float]] | None = covariance.tolist()
            uncertainty_payload: list[float] | None = parameter_uncertainty.tolist()
        except np.linalg.LinAlgError:
            covariance_payload = None
            uncertainty_payload = None
        diagnostics = {
            "fit_converged": bool(result.success),
            "fit_nfev": int(result.nfev),
            "fit_cost": float(result.cost),
            "fit_valid_pixels": int(np.count_nonzero(fit_valid)),
            "fit_mask_hash": _mask_hash(fit_valid),
            "fit_initial_parameters": [float(value) for value in initial],
            "fit_initialization_method": "local-half-height-v1",
            "fit_initialization_reasons": list(initialization_reasons),
            "fit_covariance": covariance_payload,
            "fit_parameter_uncertainty": uncertainty_payload,
            "fit_degrees_of_freedom": degrees_of_freedom,
            "fit_optimizer": "bounded-least-squares-trf-v1",
            "fit_termination_message": str(result.message),
            "fit_optimality": float(result.optimality),
            "fit_active_mask": [int(value) for value in result.active_mask],
            "fit_boundary_hit": bool(
                np.any(np.isclose(fitted, lower, atol=1e-7))
                or np.any(np.isclose(fitted, upper, atol=1e-7))
            ),
        }
    except Exception as error:
        raise RuntimeError(f"gaussian fit failed: {error}") from error
    sx, sy = float(fitted[4]), float(fitted[5])
    theta = float(fitted[6])
    if sy > sx:
        sx, sy = sy, sx
        theta += math.pi / 2.0
    metrics = {
        "fwhm_major": math.sqrt(8.0 * math.log(2.0)) * sx,
        "fwhm_minor": math.sqrt(8.0 * math.log(2.0)) * sy,
        "angle": math.degrees(theta) % 180.0,
        "fit_center_x": float(fitted[2]),
        "fit_center_y": float(fitted[3]),
        "fit_amplitude": float(fitted[0]),
        "fit_background": float(fitted[1]),
    }
    return metrics, fitted_image, diagnostics


def _jsonable(value: Any) -> Any:
    if isinstance(value, dict):
        return {str(key): _jsonable(item) for key, item in value.items()}
    if isinstance(value, Mapping):
        return {str(key): _jsonable(item) for key, item in value.items()}
    if isinstance(value, (tuple, list)):
        return [_jsonable(item) for item in value]
    return value


def _analysis_fingerprint(image: InputImage, configuration: AnalysisConfiguration) -> str:
    input_hash = image.sha256 or hashlib.sha256(np.ascontiguousarray(image.data).tobytes()).hexdigest()
    payload = {
        "input_sha256": input_hash,
        "input_shape": list(image.data.shape),
        "input_metadata": {
            "dtype": image.dtype,
            "bit_depth": image.bit_depth,
            "channels": image.channels,
            "channels_identical": image.channels_identical,
            "encoding_semantic": image.encoding_semantic,
            "encoding_semantic_confirmed": image.encoding_semantic_confirmed,
            "byte_order": image.byte_order,
            "metadata": dict(image.metadata),
            "background_frame": {
                "sha256": image.background_frame.sha256 if image.background_frame is not None else None,
                "shape": list(image.background_frame.data.shape) if image.background_frame is not None else None,
                "metadata": dict(image.background_frame.metadata) if image.background_frame is not None else None,
                "match_unverified": image.background_match_unverified,
            },
        },
        "configuration": asdict(configuration),
        "analysis_contract": configuration.analysis_contract,
        "standard_profile": configuration.standard_profile,
        "quality_profile": configuration.quality_profile,
        "profile_validation": "provisional",
        "algorithm_version": configuration.algorithm_version,
        "canonicalizer_version": _CANONICALIZER_VERSION,
    }
    canonical = rfc8785.dumps(_jsonable(payload))
    return "sha256-" + hashlib.sha256(canonical).hexdigest()


def _transform_axis_pair(
    major: float,
    minor: float,
    angle_degrees: float,
    x_scale: float,
    y_scale: float,
) -> tuple[float, float, float]:
    theta = math.radians(angle_degrees)
    rotation = np.array(
        [[math.cos(theta), -math.sin(theta)], [math.sin(theta), math.cos(theta)]],
        dtype=np.float64,
    )
    pixel_shape = rotation @ np.diag([major**2, minor**2]) @ rotation.T
    scaling = np.diag([x_scale, y_scale])
    physical_shape = scaling @ pixel_shape @ scaling
    eigenvalues, eigenvectors = np.linalg.eigh(physical_shape)
    order = np.argsort(eigenvalues)[::-1]
    lengths = np.sqrt(np.maximum(eigenvalues[order], 0.0))
    vector = eigenvectors[:, order[0]]
    physical_angle = math.degrees(math.atan2(vector[1], vector[0])) % 180.0
    return float(lengths[0]), float(lengths[1]), physical_angle


def _attach_physical_values(
    metrics: dict[str, Metric],
    configuration: AnalysisConfiguration,
    *,
    ee50_physical: float | None,
    ee80_physical: float | None,
    centroid_delta: tuple[float, float] | None,
) -> None:
    calibration = configuration.calibration
    if not calibration.is_usable:
        return
    x_scale = float(calibration.x_unit_per_pixel)
    y_scale = float(calibration.y_unit_per_pixel)
    center = metrics.get("peak_center")
    if center is not None and center.value is not None:
        metrics["peak_center"] = replace(
            center,
            physical_value={
                "x": float(center.value["x"]) * x_scale,
                "y": float(center.value["y"]) * y_scale,
            },
        )
    for prefix, angle_key in (("gaussian_fwhm", "gaussian_angle"), ("moment_d4sigma", "moment_angle")):
        major_metric = metrics.get(f"{prefix}_major")
        minor_metric = metrics.get(f"{prefix}_minor")
        angle_metric = metrics.get(angle_key)
        if (
            major_metric is None
            or minor_metric is None
            or angle_metric is None
            or major_metric.value is None
            or minor_metric.value is None
            or angle_metric.value is None
        ):
            continue
        physical_major, physical_minor, physical_angle = _transform_axis_pair(
            float(major_metric.value),
            float(minor_metric.value),
            float(angle_metric.value),
            x_scale,
            y_scale,
        )
        metrics[f"{prefix}_major"] = replace(major_metric, physical_value=physical_major)
        metrics[f"{prefix}_minor"] = replace(minor_metric, physical_value=physical_minor)
        metrics[angle_key] = replace(angle_metric, physical_value=physical_angle)
        if prefix == "gaussian_fwhm":
            ellipticity = metrics.get("gaussian_ellipticity")
            if ellipticity is not None:
                metrics["gaussian_ellipticity"] = replace(
                    ellipticity,
                    physical_value=physical_major / physical_minor,
                )
    for key, value in (("ee50", ee50_physical), ("ee80", ee80_physical)):
        metric = metrics.get(key)
        if metric is not None and value is not None:
            metrics[key] = replace(metric, physical_value=value)
    centroid = metrics.get("centroid_offset")
    if centroid is not None and centroid_delta is not None:
        dx, dy = centroid_delta
        metrics["centroid_offset"] = replace(
            centroid,
            physical_value=math.hypot(dx * x_scale, dy * y_scale),
        )


def _empty_metrics(configuration: AnalysisConfiguration, reason: str) -> dict[str, Metric]:
    metrics: dict[str, Metric] = {}
    for key, unit in (
        ("peak_center", "px"),
        ("gaussian_fwhm_major", "px"),
        ("gaussian_fwhm_minor", "px"),
        ("gaussian_ellipticity", "fraction"),
        ("gaussian_angle", "deg"),
        ("moment_d4sigma_major", "px"),
        ("moment_d4sigma_minor", "px"),
        ("moment_angle", "deg"),
        ("ee50", "px"),
        ("ee80", "px"),
        ("centroid_offset", "px"),
        ("fit_residual", "code_value"),
    ):
        metrics[key] = _invalid_metric(unit, reason, configuration)
    return metrics


def analyze(image: InputImage, configuration: AnalysisConfiguration) -> AnalysisOutcome:
    """Run one deterministic analysis using a confirmed rectangular region."""

    shape = image.data.shape
    region = configuration.region
    if (
        (image.channels != 1 and not image.channels_identical)
        or image.encoding_semantic != "relative_intensity_code"
        or not image.encoding_semantic_confirmed
    ):
        return AnalysisOutcome(FlowStatus.INPUT_INVALID, None, ({"code": "input_semantic_invalid"},))
    if not region.confirmed or region.width <= 0 or region.height <= 0:
        return AnalysisOutcome(FlowStatus.PARAMETER_INVALID, None, ({"code": "analysis_region_unconfirmed"},))
    if region.x < 0 or region.y < 0 or region.x + region.width > shape[1] or region.y + region.height > shape[0]:
        return AnalysisOutcome(FlowStatus.PARAMETER_INVALID, None, ({"code": "analysis_region_out_of_bounds"},))
    background_region = configuration.background_region
    matched_frame = image.background_frame if configuration.preprocessing.background_source == "matched_frame" else None
    if matched_frame is None and (background_region is None or not background_region.confirmed):
        return AnalysisOutcome(FlowStatus.PARAMETER_INVALID, None, ({"code": "background_region_unconfirmed"},))
    if background_region is not None:
        if (
            background_region.x < 0
            or background_region.y < 0
            or background_region.width <= 0
            or background_region.height <= 0
            or background_region.x + background_region.width > shape[1]
            or background_region.y + background_region.height > shape[0]
        ):
            return AnalysisOutcome(FlowStatus.PARAMETER_INVALID, None, ({"code": "background_region_out_of_bounds"},))
        if _regions_overlap(region, background_region):
            return AnalysisOutcome(
                FlowStatus.PARAMETER_INVALID,
                None,
                ({"code": "background_region_overlaps_analysis_region"},),
            )
    bad_pixel_mask, out_of_bounds_bad_pixels = _bad_pixel_mask(shape, configuration)
    if out_of_bounds_bad_pixels:
        return AnalysisOutcome(
            FlowStatus.PARAMETER_INVALID,
            None,
            ({"code": "bad_pixel_out_of_bounds", "coordinates": [list(item) for item in out_of_bounds_bad_pixels]},),
        )
    if configuration.rref_pixels is not None and configuration.rref_pixels <= 0:
        return AnalysisOutcome(FlowStatus.PARAMETER_INVALID, None, ({"code": "rref_invalid"},))

    data = image.data
    if image.bit_depth is not None and image.bit_depth not in {8, 16}:
        return AnalysisOutcome(FlowStatus.INPUT_INVALID, None, ({"code": "bit_depth_unsupported"},))
    saturated_mask = (
        data >= 2**image.bit_depth - 1
        if image.bit_depth is not None
        else np.zeros(shape, dtype=bool)
    )
    finite_mask = np.isfinite(data)
    measurement_valid = finite_mask & ~bad_pixel_mask & ~saturated_mask
    roi_slice = _region_slice(region)
    roi = data[roi_slice]
    roi_valid = measurement_valid[roi_slice]
    matched_background, match_diagnostics = _matching_background(image, region, matched_frame)
    if matched_background is not None:
        background = matched_background
        background_diagnostics = match_diagnostics
    else:
        if matched_frame is not None and background_region is None:
            return AnalysisOutcome(
                FlowStatus.PARAMETER_INVALID,
                None,
                ({"code": "background_region_unconfirmed", "reason": "background_match_unverified"},),
            )
        background, background_diagnostics = _background(
            data,
            region,
            background_region,
            bad_pixel_mask,
            saturated_mask,
        )
        background_diagnostics = {**match_diagnostics, **background_diagnostics}
    corrected = data - background
    standard_corrected = corrected
    advanced_diagnostics: dict[str, Any] = {"enabled": False, "steps": []}
    if configuration.preprocessing.advanced_processing_enabled:
        corrected, advanced_diagnostics = _advanced_preprocess(
            corrected, bad_pixel_mask, configuration.preprocessing
        )
    positive = np.maximum(corrected, 0.0)
    roi_corrected = corrected[roi_slice]
    roi_positive = positive[roi_slice]
    roi_positive_valid = np.where(roi_valid, roi_positive, 0.0)
    localization = np.where(measurement_valid, corrected, np.nan)
    center_xy = _subpixel_peak(localization)
    reasons: set[str] = set()
    if background_diagnostics.get("background_match_status") == "unverified":
        reasons.add("background_match_unverified")
    bad_count = int(np.count_nonzero(bad_pixel_mask))
    if bad_count:
        reasons.add("bad_pixels_present")
    if image.bit_depth is not None and np.any(data >= (2**image.bit_depth - 1)):
        reasons.add("possible_saturation")
    if background_diagnostics.get("background_fit_status") != "converged":
        reasons.add(str(background_diagnostics.get("background_reason", "background_unavailable")))
    normalized_background_rms = background_diagnostics.get("background_normalized_rms")
    if isinstance(normalized_background_rms, (float, int)):
        if normalized_background_rms > 2.5:
            reasons.add("background_residual_high")
        elif normalized_background_rms > 1.5:
            reasons.add("background_residual_caution")
    if center_xy is None:
        center_xy = (float(region.x), float(region.y))
        reasons.add("no_finite_signal")
    center_x, center_y = center_xy
    if not (region.x <= center_x < region.x + region.width and region.y <= center_y < region.y + region.height):
        reasons.add("center_outside_region")
    local_center = (center_x - region.x, center_y - region.y)
    center_in_region = 0 <= local_center[0] < region.width and 0 <= local_center[1] < region.height
    neighborhood = np.zeros_like(roi_valid)
    center_pixel_x = 0
    center_pixel_y = 0
    if center_in_region:
        center_pixel_x = min(region.width - 1, max(0, int(math.floor(local_center[0] + 0.5))))
        center_pixel_y = min(region.height - 1, max(0, int(math.floor(local_center[1] + 0.5))))
        neighborhood[
            max(0, center_pixel_y - 1) : min(region.height, center_pixel_y + 2),
            max(0, center_pixel_x - 1) : min(region.width, center_pixel_x + 2),
        ] = True
    center_peak_values = roi_positive[neighborhood & roi_valid]
    core_peak = float(np.max(center_peak_values)) if center_peak_values.size else 0.0
    core_candidate = (
        roi_positive >= 0.5 * core_peak
        if core_peak > 0
        else np.zeros_like(roi_positive, dtype=bool)
    )
    core_mask = np.zeros_like(core_candidate, dtype=bool)
    if np.any(core_candidate) and center_in_region:
        components, component_count = label(core_candidate, structure=np.ones((3, 3), dtype=int))
        center_component = int(components[center_pixel_y, center_pixel_x]) if component_count else 0
        if center_component:
            core_mask = components == center_component
    boundary_mask = np.zeros_like(core_mask, dtype=bool)
    if boundary_mask.size:
        boundary_mask[[0, -1], :] = True
        boundary_mask[:, [0, -1]] = True
    boundary_core_pixels = int(np.count_nonzero(core_mask & boundary_mask))
    core_touches_boundary = boundary_core_pixels > 0 if core_mask.size else True
    truncation_fraction = (
        boundary_core_pixels / int(np.count_nonzero(core_mask))
        if np.any(core_mask)
        else 1.0
    )
    if core_touches_boundary:
        reasons.add("window_truncated")
    core_bad = bad_pixel_mask[roi_slice] & core_mask
    core_saturated = saturated_mask[roi_slice] & core_mask
    core_valid_fraction = (
        float(np.count_nonzero(roi_valid & core_mask) / np.count_nonzero(core_mask))
        if np.any(core_mask)
        else 0.0
    )
    if np.any(core_bad):
        reasons.add("bad_pixel_in_core")
    if np.any(core_mask):
        if core_valid_fraction < 0.8:
            reasons.add("core_support_insufficient")
        elif core_valid_fraction < 0.95:
            reasons.add("core_support_caution")
    saturated_components, saturated_component_count = label(
        core_saturated,
        structure=np.ones((3, 3), dtype=int),
    )
    saturated_plateau_sizes = (
        np.bincount(saturated_components.ravel())[1:]
        if saturated_component_count
        else np.array([], dtype=int)
    )
    if saturated_plateau_sizes.size and int(np.max(saturated_plateau_sizes)) >= 3:
        reasons.add("saturated_core")
    if core_peak <= 0:
        reasons.add("no_positive_signal")
    noise_value = background_diagnostics.get("background_noise_sigma")
    noise = float(noise_value) if isinstance(noise_value, (float, int)) else 0.0
    peak_neighborhood = roi[neighborhood & roi_valid]
    peak_for_snr = float(np.max(peak_neighborhood)) if peak_neighborhood.size else 0.0
    background_at_center = float(
        background[
            min(shape[0] - 1, max(0, int(math.floor(center_y + 0.5)))),
            min(shape[1] - 1, max(0, int(math.floor(center_x + 0.5)))),
        ]
    )
    if noise > np.finfo(float).eps:
        snr = (peak_for_snr - background_at_center) / noise
        if snr < 5:
            reasons.add("low_snr")
        elif snr < 10:
            reasons.add("low_snr_caution")
    else:
        snr = None
        if core_peak > 0:
            reasons.add("background_noise_unavailable")

    initial_sigma_x, initial_sigma_y, _ = _gaussian_initial_widths(
        roi_positive,
        roi_valid,
        local_center,
    )
    standard_roi_positive = np.maximum(standard_corrected[roi_slice], 0.0)
    standard_sigma_x, standard_sigma_y, _ = _gaussian_initial_widths(
        standard_roi_positive,
        roi_valid,
        local_center,
    )
    standard_fwhm = math.sqrt(8.0 * math.log(2.0)) * max(standard_sigma_x, standard_sigma_y)
    advanced_fwhm = math.sqrt(8.0 * math.log(2.0)) * max(initial_sigma_x, initial_sigma_y)
    advanced_sensitivity = abs(advanced_fwhm - standard_fwhm) / max(standard_fwhm, 1e-12)
    if configuration.preprocessing.advanced_processing_enabled and advanced_sensitivity > 0.10:
        reasons.add("advanced_sensitivity_exceeded")
    estimated_fwhm_major = advanced_fwhm
    separation_threshold = max(3.0, 0.5 * estimated_fwhm_major)
    candidate_threshold = max(0.2 * core_peak, 5.0 * noise)
    supported_signal = roi_valid & (roi_positive >= candidate_threshold) if core_peak > 0 else np.zeros_like(roi_valid)
    candidate_components, candidate_component_count = label(
        supported_signal,
        structure=np.ones((3, 3), dtype=int),
    )
    center_component = (
        int(candidate_components[center_pixel_y, center_pixel_x])
        if center_in_region and candidate_component_count
        else 0
    )
    candidate_positions: list[dict[str, float]] = []
    ring_component_detected = False
    for component_id in range(1, candidate_component_count + 1):
        if component_id == center_component:
            continue
        component = candidate_components == component_id
        support_pixels = int(np.count_nonzero(component))
        if support_pixels < 9:
            continue
        touches_boundary = bool(
            np.any(component[[0, -1], :]) or np.any(component[:, [0, -1]])
        )
        if touches_boundary:
            continue
        component_y, component_x = np.nonzero(component)
        component_values = roi_positive[component]
        maximum_index = int(np.argmax(component_values))
        candidate_y = int(component_y[maximum_index])
        candidate_x = int(component_x[maximum_index])
        distance = math.hypot(candidate_x - local_center[0], candidate_y - local_center[1])
        angles = (np.arctan2(component_y - local_center[1], component_x - local_center[0]) + 2.0 * math.pi) % (2.0 * math.pi)
        angular_bins = np.unique(np.floor(angles / (2.0 * math.pi) * 16.0).astype(int)).size
        if angular_bins >= 12:
            ring_component_detected = True
            continue
        if distance < separation_threshold:
            continue
        candidate_positions.append(
            {
                "x": float(candidate_x + region.x),
                "y": float(candidate_y + region.y),
                "distance": float(distance),
                "prominence": float(component_values[maximum_index]),
                "support_pixels": support_pixels,
                "touches_boundary": False,
                "separation_threshold": float(separation_threshold),
            }
        )
    if candidate_positions:
        reasons.add("multiple_peaks")
    local_y, local_x = np.indices(roi_positive_valid.shape, dtype=np.float64)
    local_radius = np.hypot(local_x - local_center[0], local_y - local_center[1])
    ring_candidate = ring_component_detected
    if not candidate_positions and core_peak > 0 and not ring_candidate:
        radial_bins = np.arange(1.0, float(np.max(local_radius)) + 1.0)
        radial_values = []
        for radius in radial_bins:
            annulus = roi_positive_valid[(local_radius >= radius - 0.5) & (local_radius < radius + 0.5)]
            radial_values.append(float(np.mean(annulus)) if annulus.size else 0.0)
        radial_profile = np.array(radial_values)
        if radial_profile.size >= 5:
            local_radial_max = maximum_filter(radial_profile, size=5, mode="nearest") == radial_profile
            ring_candidate = bool(
                np.any(local_radial_max[3:] & (radial_profile[3:] >= 0.01 * core_peak))
            )
    if ring_candidate:
        reasons.add("ring_candidate")

    metrics: dict[str, Metric] = {}
    fit_diagnostics: dict[str, Any] = {"fit_converged": False}
    fitted_image = np.full_like(roi_corrected, np.nan)
    fit_residual_image = np.full_like(roi_corrected, np.nan)
    ee50_physical: float | None = None
    ee80_physical: float | None = None
    centroid_delta: tuple[float, float] | None = None
    hard_invalid = {
        "saturated_core",
        "window_truncated",
        "low_snr",
        "no_positive_signal",
        "background_support_insufficient",
        "background_noise_unavailable",
        "background_residual_high",
        "core_support_insufficient",
        "advanced_sensitivity_exceeded",
    }
    if core_peak <= 0 or "center_outside_region" in reasons:
        metrics = _empty_metrics(
            configuration,
            "no_positive_signal" if core_peak <= 0 else "center_outside_region",
        )
    else:
        try:
            moment_major, moment_minor, moment_angle, centroid_offset = _moment_metrics(
                roi_positive,
                local_center,
                roi_valid,
            )
            ee50 = _encircled_radius(roi_positive, local_center, 0.5, roi_valid)
            ee80 = _encircled_radius(roi_positive, local_center, 0.8, roi_valid)
            if configuration.calibration.is_usable:
                x_scale = float(configuration.calibration.x_unit_per_pixel)
                y_scale = float(configuration.calibration.y_unit_per_pixel)
                ee50_physical = _encircled_radius(
                    roi_positive,
                    local_center,
                    0.5,
                    roi_valid,
                    x_scale,
                    y_scale,
                )
                ee80_physical = _encircled_radius(
                    roi_positive,
                    local_center,
                    0.8,
                    roi_valid,
                    x_scale,
                    y_scale,
                )
            coordinate_y, coordinate_x = np.indices(roi_positive.shape, dtype=np.float64)
            centroid_weights = np.where(roi_valid, roi_positive, 0.0)
            centroid_total = float(centroid_weights.sum())
            if centroid_total > 0:
                centroid_delta = (
                    float(np.sum(centroid_weights * coordinate_x) / centroid_total - local_center[0]),
                    float(np.sum(centroid_weights * coordinate_y) / centroid_total - local_center[1]),
                )
            fit, fitted_image, fit_diagnostics = _gaussian_fit(
                roi_corrected,
                local_center,
                0.0,
                roi_valid,
                roi_positive,
            )
            fit_reasons = set(reasons)
            fit_status = MeasurementStatus.INVALID if hard_invalid & reasons else MeasurementStatus.VALID
            if {"multiple_peaks", "ring_candidate", "bad_pixels_present", "bad_pixel_in_core", "core_support_caution", "background_residual_caution", "background_match_unverified", "low_snr_caution"} & reasons and fit_status != MeasurementStatus.INVALID:
                fit_status = MeasurementStatus.CAUTION
            if not fit_diagnostics["fit_converged"] or fit_diagnostics["fit_boundary_hit"]:
                fit_status = MeasurementStatus.INVALID
                fit_reasons.add("fit_boundary_or_not_converged")
            fit_residual = roi_corrected - fitted_image
            fit_residual_image = fit_residual
            residual_valid = roi_valid & np.isfinite(fit_residual)
            residual_values = fit_residual[residual_valid]
            residual_rms = float(np.sqrt(np.mean(residual_values**2))) if residual_values.size else math.inf
            positive_total = float(roi_positive_valid.sum())
            l1_over_total = float(np.sum(np.abs(residual_values)) / max(positive_total, np.finfo(float).eps)) if residual_values.size else math.inf
            normalized_residual = residual_rms / noise if noise > 1e-8 else (0.0 if residual_rms <= 1e-8 else math.inf)
            if normalized_residual > 4:
                fit_status = MeasurementStatus.INVALID
                fit_reasons.add("fit_residual_high")
            elif normalized_residual > 2:
                fit_status = MeasurementStatus.CAUTION
                fit_reasons.add("fit_residual_caution")
            fit_diagnostics.update(
                {
                    "residual_valid_pixels": int(np.count_nonzero(residual_valid)),
                    "residual_mask_hash": _mask_hash(residual_valid),
                    "residual_rms": residual_rms,
                    "residual_l1_over_total": l1_over_total,
                    "normalized_residual": normalized_residual,
                    "residual_unit": "code_value",
                }
            )
            metrics["gaussian_fwhm_major"] = _metric(fit["fwhm_major"], "px", fit_status, fit_reasons, configuration)
            metrics["gaussian_fwhm_minor"] = _metric(fit["fwhm_minor"], "px", fit_status, fit_reasons, configuration)
            metrics["gaussian_ellipticity"] = _metric(fit["fwhm_major"] / fit["fwhm_minor"], "fraction", fit_status, fit_reasons, configuration)
            metrics["gaussian_angle"] = _metric(fit["angle"], "deg", fit_status, fit_reasons, configuration)
            moment_status = MeasurementStatus.INVALID if hard_invalid & reasons else MeasurementStatus.VALID
            if "multiple_peaks" in reasons and moment_status != MeasurementStatus.INVALID:
                moment_status = MeasurementStatus.CAUTION
            ee_hard_invalid = hard_invalid - {"window_truncated"}
            ee_status = MeasurementStatus.INVALID if ee_hard_invalid & reasons else MeasurementStatus.VALID
            if "window_truncated" in reasons and ee_status != MeasurementStatus.INVALID:
                ee_status = MeasurementStatus.INVALID if truncation_fraction > 0.10 else MeasurementStatus.CAUTION
            metrics["moment_d4sigma_major"] = _metric(moment_major, "px", moment_status, reasons, configuration)
            metrics["moment_d4sigma_minor"] = _metric(moment_minor, "px", moment_status, reasons, configuration)
            metrics["moment_angle"] = _metric(moment_angle, "deg", MeasurementStatus.CAUTION if {"low_snr", "low_snr_caution", "bad_pixels_present", "multiple_peaks"} & reasons else moment_status, reasons, configuration)
            metrics["ee50"] = _metric(ee50, "px", ee_status if ee50 is not None else MeasurementStatus.UNAVAILABLE, reasons, configuration)
            metrics["ee80"] = _metric(ee80, "px", ee_status if ee80 is not None else MeasurementStatus.UNAVAILABLE, reasons, configuration)
            metrics["centroid_offset"] = _metric(centroid_offset, "px", moment_status, reasons, configuration)
            metrics["fit_residual"] = _metric({"rms": residual_rms, "l1_over_total": l1_over_total}, "code_value", fit_status, fit_reasons, configuration)
        except (ValueError, RuntimeError) as error:
            return AnalysisOutcome(
                FlowStatus.ANALYSIS_FAILED,
                None,
                ({"code": "analysis_failed", "message": str(error)},),
            )

    center_status = (
        MeasurementStatus.INVALID
        if {"no_positive_signal", "center_outside_region", "no_finite_signal"} & reasons
        else MeasurementStatus.CAUTION if reasons else MeasurementStatus.VALID
    )
    metrics["peak_center"] = _metric(
        {"x": center_x, "y": center_y} if center_status != MeasurementStatus.INVALID else None,
        "px",
        center_status,
        reasons,
        configuration,
    )
    if configuration.rref_pixels is not None and core_peak > 0:
        total = float(roi_positive_valid.sum())
        inside = np.hypot(local_x - local_center[0], local_y - local_center[1]) <= configuration.rref_pixels
        concentration_hard_invalid = hard_invalid - {"window_truncated"}
        concentration_status = MeasurementStatus.INVALID if concentration_hard_invalid & reasons else MeasurementStatus.VALID
        if "window_truncated" in reasons and concentration_status != MeasurementStatus.INVALID:
            concentration_status = MeasurementStatus.INVALID if truncation_fraction > 0.10 else MeasurementStatus.CAUTION
        metrics["concentration_rref"] = _metric(
            float(roi_positive_valid[inside].sum() / total) if total > 0 else None,
            "fraction",
            concentration_status,
            reasons,
            configuration,
        )
    else:
        metrics["concentration_rref"] = _metric(None, "fraction", MeasurementStatus.UNAVAILABLE, {"rref_missing"}, configuration)

    _attach_physical_values(
        metrics,
        configuration,
        ee50_physical=ee50_physical,
        ee80_physical=ee80_physical,
        centroid_delta=centroid_delta,
    )

    if image.bit_depth is not None:
        max_code = 2**image.bit_depth - 1
        saturated_count = int(np.count_nonzero(roi >= max_code))
    else:
        saturated_count = 0
    fit_is_invalid = (
        not fit_diagnostics.get("fit_converged", False)
        or fit_diagnostics.get("fit_boundary_hit", False)
        or fit_diagnostics.get("normalized_residual", 0.0) > 4
    )
    quality_before_cap = (
        MeasurementStatus.INVALID
        if hard_invalid & reasons or fit_is_invalid
        else MeasurementStatus.CAUTION
        if reasons or fit_diagnostics.get("normalized_residual", 0.0) > 2
        else MeasurementStatus.VALID
    )
    metrics["quality_diagnostics"] = _metric(
        {
            "bad_pixel_count": bad_count,
            "saturated_pixel_count": saturated_count,
            "core_pixels": int(np.count_nonzero(core_mask)),
            "core_valid_pixels": int(np.count_nonzero(roi_valid & core_mask)),
            "measurement_valid_pixels": int(np.count_nonzero(measurement_valid)),
        },
        "count",
        quality_before_cap,
        reasons,
        configuration,
    )
    parameter_uncertainty = fit_diagnostics.get("fit_parameter_uncertainty")
    fit_uncertainty: dict[str, Any] = {"available": False, "reason": "fit_uncertainty_unavailable"}
    if isinstance(parameter_uncertainty, (list, tuple)) and len(parameter_uncertainty) >= 6:
        sigma_factor = math.sqrt(8.0 * math.log(2.0))
        pixel_major = sigma_factor * max(float(parameter_uncertainty[4]), float(parameter_uncertainty[5]))
        pixel_minor = sigma_factor * min(float(parameter_uncertainty[4]), float(parameter_uncertainty[5]))
        fit_uncertainty = {
            "available": True,
            "gaussian_fwhm_major": pixel_major,
            "gaussian_fwhm_minor": pixel_minor,
            "unit": "px",
        }
        if configuration.calibration.is_usable:
            x_scale = float(configuration.calibration.x_unit_per_pixel)
            y_scale = float(configuration.calibration.y_unit_per_pixel)
            fit_uncertainty["physical"] = {
                "gaussian_fwhm_major": math.hypot(pixel_major * x_scale, pixel_major * y_scale) / math.sqrt(2.0),
                "gaussian_fwhm_minor": math.hypot(pixel_minor * x_scale, pixel_minor * y_scale) / math.sqrt(2.0),
                "unit": configuration.calibration.physical_unit,
            }
    diagnostics: dict[str, Any] = {
        **background_diagnostics,
        "mask_version": _MASK_VERSION,
        "measurement_mask_hash": _mask_hash(measurement_valid),
        "measurement_valid_pixels": int(np.count_nonzero(measurement_valid)),
        "measurement_invalid_pixels": int(np.count_nonzero(~measurement_valid)),
        "bad_pixel_mask_version": configuration.bad_pixel_mask_version,
        "bad_pixel_coordinates": [list(item) for item in configuration.bad_pixel_coordinates],
        "bad_pixel_count": bad_count,
        "snr": snr,
        "center_xy": {"x": center_x, "y": center_y},
        "core_pixels": int(np.count_nonzero(core_mask)),
        "core_valid_pixels": int(np.count_nonzero(roi_valid & core_mask)),
        "core_masked_pixels": int(np.count_nonzero(core_mask & ~roi_valid)),
        "core_valid_fraction": core_valid_fraction,
        "core_mask_hash": _mask_hash(core_mask),
        "core_touches_boundary": core_touches_boundary,
        "core_boundary_pixels": boundary_core_pixels,
        "window_truncation_fraction": truncation_fraction,
        "window_truncation_policy": "boundary-core-fraction-v1",
        "negative_pixel_fraction": float(np.mean(roi_corrected < 0)),
        "saturated_pixel_count": saturated_count,
        "multiple_peak_candidates": candidate_positions,
        "reasons": list(_reason_tuple(reasons)),
        "fit": fit_diagnostics,
        "fit_uncertainty": fit_uncertainty,
        "profile_validation": configuration.profile_validation,
        "canonicalizer_version": _CANONICALIZER_VERSION,
        "quality_status_before_profile_cap": quality_before_cap.value,
        "preprocessing_standard_branch": {"retained": True, "version": configuration.preprocessing.version},
        "preprocessing_advanced_branch": {
            **advanced_diagnostics,
            "standard_fwhm_estimate": standard_fwhm,
            "advanced_fwhm_estimate": advanced_fwhm,
            "sensitivity_fraction": advanced_sensitivity,
            "sensitivity_threshold": 0.10,
            "sensitivity_gate": "failed" if "advanced_sensitivity_exceeded" in reasons else "passed",
        },
    }
    statuses = [metric.status for metric in metrics.values() if metric.status != MeasurementStatus.UNAVAILABLE]
    summary = (
        MeasurementStatus.INVALID
        if any(status == MeasurementStatus.INVALID for status in statuses)
        else MeasurementStatus.CAUTION
        if any(status == MeasurementStatus.CAUTION for status in statuses)
        else MeasurementStatus.VALID
    )
    record = AnalysisRecord(
        record_id="record-" + uuid.uuid4().hex,
        analysis_fingerprint=_analysis_fingerprint(image, configuration),
        flow_status=FlowStatus.COMPUTED,
        summary_status=summary,
        metrics=metrics,
        diagnostics=diagnostics,
        configuration=configuration,
        input_shape=shape,
        input_intensity=data,
        corrected_intensity=corrected,
        positive_intensity=positive,
        fitted_intensity=fitted_image,
        fit_residual_intensity=fit_residual_image,
        measurement_mask=measurement_valid,
        standard_corrected_intensity=standard_corrected,
        core_mask=core_mask,
        input_metadata={
            "asset_id": image.asset_id,
            "uri_hint": image.uri_hint,
            "sha256": image.sha256,
            "dtype": image.dtype,
            "bit_depth": image.bit_depth,
            "byte_order": image.byte_order,
            "channels": image.channels,
            "channels_identical": image.channels_identical,
            "encoding_semantic": image.encoding_semantic,
            "encoding_semantic_confirmed": image.encoding_semantic_confirmed,
            "metadata": dict(image.metadata),
            "read_status": image.read_status,
            "background_frame": {
                "asset_id": image.background_frame.asset_id,
                "sha256": image.background_frame.sha256,
                "uri_hint": image.background_frame.uri_hint,
                "metadata": dict(image.background_frame.metadata),
            } if image.background_frame is not None else None,
            "background_match_unverified_requested": image.background_match_unverified,
        },
    )
    return AnalysisOutcome(FlowStatus.COMPUTED, record)
