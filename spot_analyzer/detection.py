"""Versioned automatic focal-spot, ROI, and background proposal logic.

This module is deliberately independent of presentation code.  It returns a
small immutable proposal which the analysis core can resolve into its normal
single-ROI configuration.
"""

from __future__ import annotations

from dataclasses import dataclass, field, replace
import math
from typing import Any, Mapping

import numpy as np
from scipy.ndimage import binary_dilation, label

from .models import AnalysisRegion, InputImage


@dataclass(frozen=True)
class FocalSpotDetectionProfile:
    """The science defaults for automatic localization, ROI, and background."""

    version: str = "focal-spot-detection-v1"
    signal_sigma_threshold: float = 5.0
    signal_peak_fraction: float = 0.05
    isolated_hot_pixel_max_support: int = 1
    isolated_hot_pixel_sigma_threshold: float = 8.0
    minimum_candidate_support_pixels: int = 4
    minimum_candidate_separation_pixels: float = 4.0
    roi_safety_margin_pixels: int = 12
    roi_min_width_pixels: int = 48
    roi_min_height_pixels: int = 48
    background_min_width_pixels: int = 16
    background_min_height_pixels: int = 16
    background_protection_dilation_pixels: int = 2
    background_min_support_pixels: int = 30

    def __post_init__(self) -> None:
        if not self.version.strip():
            raise ValueError("detection profile version must not be empty")
        if not math.isfinite(self.signal_sigma_threshold) or self.signal_sigma_threshold <= 0:
            raise ValueError("signal sigma threshold must be positive")
        if not 0 <= self.signal_peak_fraction <= 1:
            raise ValueError("signal peak fraction must be between zero and one")
        integer_fields = (
            "isolated_hot_pixel_max_support", "minimum_candidate_support_pixels",
            "roi_safety_margin_pixels", "roi_min_width_pixels", "roi_min_height_pixels",
            "background_min_width_pixels", "background_min_height_pixels",
            "background_protection_dilation_pixels", "background_min_support_pixels",
        )
        if any(isinstance(getattr(self, name), bool) or not isinstance(getattr(self, name), int) for name in integer_fields):
            raise ValueError("detection profile counts must be integers")
        if any(getattr(self, name) < 0 for name in integer_fields):
            raise ValueError("detection profile counts must be nonnegative")
        if self.minimum_candidate_support_pixels <= 0 or self.background_min_support_pixels <= 0:
            raise ValueError("detection profile support limits must be positive")
        if not math.isfinite(self.isolated_hot_pixel_sigma_threshold) or self.isolated_hot_pixel_sigma_threshold <= 0:
            raise ValueError("hot pixel threshold must be positive")
        if not math.isfinite(self.minimum_candidate_separation_pixels) or self.minimum_candidate_separation_pixels < 0:
            raise ValueError("candidate separation must be nonnegative")


DEFAULT_DETECTION_PROFILE = FocalSpotDetectionProfile()


@dataclass(frozen=True)
class FocalSpotCandidate:
    x: float
    y: float
    energy: float
    peak: float
    support_pixels: int
    bounds: AnalysisRegion
    touches_boundary: bool = False
    isolated_hot_pixels_excluded: int = 0

    def to_dict(self) -> dict[str, Any]:
        return {
            "x": self.x, "y": self.y, "energy": self.energy, "peak": self.peak,
            "support_pixels": self.support_pixels,
            "bounds": {
                "x": self.bounds.x, "y": self.bounds.y,
                "width": self.bounds.width, "height": self.bounds.height,
            },
            "touches_boundary": self.touches_boundary,
            "isolated_hot_pixels_excluded": self.isolated_hot_pixels_excluded,
        }


@dataclass(frozen=True)
class AutomaticAnalysisProposal:
    profile_version: str
    candidate: FocalSpotCandidate | None
    candidates: tuple[FocalSpotCandidate, ...]
    region: AnalysisRegion
    background_region: AnalysisRegion | None
    diagnostics: Mapping[str, Any] = field(default_factory=dict)

    @property
    def fallback_used(self) -> bool:
        return self.candidate is None

    @property
    def roi(self) -> AnalysisRegion:
        return self.region


def _robust_scale(values: np.ndarray) -> float:
    if values.size == 0:
        return 0.0
    center = float(np.median(values))
    return 1.4826 * float(np.median(np.abs(values - center)))


def _clamp_region(x: int, y: int, width: int, height: int, shape: tuple[int, int]) -> AnalysisRegion:
    image_height, image_width = shape
    width = min(image_width, max(1, width))
    height = min(image_height, max(1, height))
    x = min(max(0, x), image_width - width)
    y = min(max(0, y), image_height - height)
    return AnalysisRegion(int(x), int(y), int(width), int(height), confirmed=True)


def _centered_region(shape: tuple[int, int], profile: FocalSpotDetectionProfile) -> AnalysisRegion:
    height, width = shape
    return _clamp_region(
        (width - min(width, profile.roi_min_width_pixels)) // 2,
        (height - min(height, profile.roi_min_height_pixels)) // 2,
        min(width, profile.roi_min_width_pixels),
        min(height, profile.roi_min_height_pixels),
        shape,
    )


def _candidate_mask(
    image: InputImage,
    bad_pixel_coordinates: tuple[tuple[int, int], ...],
    profile: FocalSpotDetectionProfile,
) -> tuple[np.ndarray, np.ndarray, dict[str, Any]]:
    data = np.asarray(image.data, dtype=np.float64)
    finite = np.isfinite(data)
    bad = np.zeros(data.shape, dtype=bool)
    for x, y in bad_pixel_coordinates:
        if 0 <= x < data.shape[1] and 0 <= y < data.shape[0]:
            bad[y, x] = True
    saturated = np.zeros(data.shape, dtype=bool)
    if image.bit_depth is not None:
        saturated = data >= 2**image.bit_depth - 1
    usable = finite & ~bad & ~saturated
    values = data[usable]
    baseline = float(np.median(values)) if values.size else 0.0
    noise = _robust_scale(values - baseline)
    if noise <= np.finfo(float).eps:
        nonzero = np.abs(values - baseline)
        noise = float(np.percentile(nonzero, 75) / 0.6745) if nonzero.size else 0.0
    excess = np.maximum(data - baseline, 0.0)
    # Do not let a single extreme pixel set the threshold for the real spot.
    # The high percentile is deterministic and robust to a sparse hot pixel.
    robust_peak = float(np.percentile(excess[usable], 99.9)) if np.any(usable) else 0.0
    initial_threshold = max(profile.signal_sigma_threshold * noise, profile.signal_peak_fraction * robust_peak)
    initial_signal = usable & (excess >= initial_threshold) if robust_peak > 0 and initial_threshold > 0 else np.zeros(data.shape, dtype=bool)

    # A one-pixel high excursion is not allowed to become the focal spot.  The
    # exclusion is based on the unmodified image, and is recorded explicitly.
    components, count = label(initial_signal, structure=np.ones((3, 3), dtype=int))
    isolated = np.zeros(data.shape, dtype=bool)
    for component_id in range(1, count + 1):
        component = components == component_id
        if int(np.count_nonzero(component)) > profile.isolated_hot_pixel_max_support:
            continue
        ys, xs = np.nonzero(component)
        for y, x in zip(ys, xs):
            neighbours = data[max(0, y - 1):min(data.shape[0], y + 2), max(0, x - 1):min(data.shape[1], x + 2)]
            neighbour_mask = np.ones(neighbours.shape, dtype=bool)
            neighbour_mask[y - max(0, y - 1), x - max(0, x - 1)] = False
            neighbour_values = neighbours[neighbour_mask & np.isfinite(neighbours)]
            neighbour_center = float(np.median(neighbour_values)) if neighbour_values.size else baseline
            if data[y, x] - neighbour_center >= profile.isolated_hot_pixel_sigma_threshold * max(noise, 1e-12):
                isolated[y, x] = True
    signal = initial_signal & ~isolated
    peak = float(np.max(excess[signal])) if np.any(signal) else 0.0
    return signal, excess, {
        "baseline": baseline,
        "noise_sigma": noise,
        "peak_excess": peak,
        "signal_threshold": initial_threshold,
        "signal_pixels": int(np.count_nonzero(signal)),
        "isolated_hot_pixel_count": int(np.count_nonzero(isolated)),
        "saturated_pixels_excluded": int(np.count_nonzero(saturated)),
        "bad_pixels_excluded": int(np.count_nonzero(bad)),
    }


def _find_candidates(
    signal: np.ndarray,
    excess: np.ndarray,
    profile: FocalSpotDetectionProfile,
) -> tuple[FocalSpotCandidate, ...]:
    components, count = label(signal, structure=np.ones((3, 3), dtype=int))
    height, width = signal.shape
    candidates: list[FocalSpotCandidate] = []
    for component_id in range(1, count + 1):
        component = components == component_id
        support = int(np.count_nonzero(component))
        if support < profile.minimum_candidate_support_pixels:
            continue
        ys, xs = np.nonzero(component)
        weights = excess[component]
        energy = float(np.sum(weights))
        if energy <= 0:
            continue
        x = float(np.sum(xs * weights) / energy)
        y = float(np.sum(ys * weights) / energy)
        bounds = AnalysisRegion(int(xs.min()), int(ys.min()), int(xs.max() - xs.min() + 1), int(ys.max() - ys.min() + 1))
        touches_boundary = bool(xs.min() == 0 or ys.min() == 0 or xs.max() == width - 1 or ys.max() == height - 1)
        candidates.append(FocalSpotCandidate(x, y, energy, float(np.max(weights)), support, bounds, touches_boundary))
    return tuple(sorted(candidates, key=lambda item: (-item.energy, item.y, item.x)))


def _auto_roi(candidate: FocalSpotCandidate | None, shape: tuple[int, int], profile: FocalSpotDetectionProfile) -> AnalysisRegion:
    if candidate is None:
        return _centered_region(shape, profile)
    margin = profile.roi_safety_margin_pixels
    bounds = candidate.bounds
    x0 = bounds.x - margin
    y0 = bounds.y - margin
    x1 = bounds.x + bounds.width + margin
    y1 = bounds.y + bounds.height + margin
    width = max(x1 - x0, profile.roi_min_width_pixels)
    height = max(y1 - y0, profile.roi_min_height_pixels)
    return _clamp_region(x0, y0, width, height, shape)


def _background_region(
    data: np.ndarray,
    roi: AnalysisRegion,
    signal: np.ndarray,
    profile: FocalSpotDetectionProfile,
) -> tuple[AnalysisRegion | None, dict[str, Any]]:
    height, width = data.shape
    protected = binary_dilation(signal, iterations=profile.background_protection_dilation_pixels, structure=np.ones((3, 3), dtype=bool))
    candidates: list[tuple[tuple[int, int, int, int], AnalysisRegion, int, int]] = []
    min_w = min(width, profile.background_min_width_pixels)
    min_h = min(height, profile.background_min_height_pixels)
    # Prefer a strip immediately outside the ROI, with deterministic left/right/
    # top/bottom order.  Every candidate remains entirely in image bounds.
    proposals = (
        (roi.x - min_w, roi.y, min_w, roi.height),
        (roi.x + roi.width, roi.y, min_w, roi.height),
        (roi.x, roi.y - min_h, roi.width, min_h),
        (roi.x, roi.y + roi.height, roi.width, min_h),
    )
    for order, (x, y, w, h) in enumerate(proposals):
        if x < 0 or y < 0 or x + w > width or y + h > height or w <= 0 or h <= 0:
            continue
        region = AnalysisRegion(x, y, w, h, confirmed=True)
        view = protected[y:y + h, x:x + w]
        finite = np.isfinite(data[y:y + h, x:x + w])
        valid = finite & ~view
        support = int(np.count_nonzero(valid))
        candidates.append(((0 if support >= profile.background_min_support_pixels else 1, -support, order, x), region, support, int(np.count_nonzero(view))))
    candidates.sort(key=lambda item: item[0])
    if not candidates:
        return None, {"background_support_status": "insufficient", "background_protection_pixels": int(np.count_nonzero(protected)), "background_candidates": []}
    _, chosen, support, excluded = candidates[0]
    accepted = support >= profile.background_min_support_pixels
    diagnostics = {
        "background_support_status": "supported" if accepted else "insufficient",
        "background_protection_pixels": int(np.count_nonzero(protected)),
        "background_protected_signal_pixels": excluded,
        "background_support_pixels": support,
        "background_support_required": profile.background_min_support_pixels,
        "background_region_candidates": [
            {"x": item[1].x, "y": item[1].y, "width": item[1].width, "height": item[1].height, "support_pixels": item[2], "protected_pixels": item[3]}
            for item in candidates
        ],
    }
    return (chosen if accepted else None), diagnostics


def propose_auto_analysis(
    image: InputImage,
    *,
    bad_pixel_coordinates: tuple[tuple[int, int], ...] = (),
    profile: FocalSpotDetectionProfile = DEFAULT_DETECTION_PROFILE,
) -> AutomaticAnalysisProposal:
    """Propose one main candidate, an in-bounds ROI, and external background."""
    signal, excess, detection = _candidate_mask(image, bad_pixel_coordinates, profile)
    candidates = _find_candidates(signal, excess, profile)
    if candidates and detection["isolated_hot_pixel_count"]:
        candidates = tuple(
            replace(item, isolated_hot_pixels_excluded=int(detection["isolated_hot_pixel_count"]))
            for item in candidates
        )
    candidate = candidates[0] if candidates else None
    region = _auto_roi(candidate, image.data.shape, profile)
    background_region, background_diagnostics = _background_region(np.asarray(image.data), region, signal, profile)
    energy_total = float(sum(item.energy for item in candidates))
    selected_ratio = float(candidate.energy / energy_total) if candidate is not None and energy_total > 0 else None
    separation = profile.minimum_candidate_separation_pixels
    multi = [item for item in candidates[1:] if candidate is None or math.hypot(item.x - candidate.x, item.y - candidate.y) >= separation]
    reasons: list[str] = []
    if candidate is None:
        reasons.append("automatic_center_fallback")
    if multi:
        reasons.append("automatic_multiple_candidates")
    if candidate is not None and candidate.touches_boundary:
        reasons.append("automatic_candidate_touches_boundary")
    if detection["isolated_hot_pixel_count"]:
        reasons.append("automatic_isolated_hot_pixels_excluded")
    if background_region is None:
        reasons.append("automatic_background_support_insufficient")
    diagnostics = {
        "profile_version": profile.version,
        "profile_parameters": {
            "signal_sigma_threshold": profile.signal_sigma_threshold,
            "signal_peak_fraction": profile.signal_peak_fraction,
            "isolated_hot_pixel_max_support": profile.isolated_hot_pixel_max_support,
            "isolated_hot_pixel_sigma_threshold": profile.isolated_hot_pixel_sigma_threshold,
            "minimum_candidate_support_pixels": profile.minimum_candidate_support_pixels,
            "minimum_candidate_separation_pixels": profile.minimum_candidate_separation_pixels,
            "roi_safety_margin_pixels": profile.roi_safety_margin_pixels,
            "roi_min_width_pixels": profile.roi_min_width_pixels,
            "roi_min_height_pixels": profile.roi_min_height_pixels,
            "background_protection_dilation_pixels": profile.background_protection_dilation_pixels,
            "background_min_width_pixels": profile.background_min_width_pixels,
            "background_min_height_pixels": profile.background_min_height_pixels,
            "background_min_support_pixels": profile.background_min_support_pixels,
        },
        "candidate_count": len(candidates),
        "candidates": [item.to_dict() for item in candidates],
        "selected_candidate": candidate.to_dict() if candidate is not None else None,
        "selected_energy_fraction": selected_ratio,
        "selected_candidate_energy_ratio": selected_ratio,
        "energy_ratio": selected_ratio,
        "selection_reason": "largest_integrated_energy" if candidate is not None else "no_supported_signal_center_fallback",
        "candidate_selection_reason": "largest_integrated_energy" if candidate is not None else "no_supported_signal_center_fallback",
        "roi": {"x": region.x, "y": region.y, "width": region.width, "height": region.height, "confirmed": region.confirmed},
        "roi_generation": "candidate_bounds_plus_safety_margin-v1" if candidate is not None else "centered_fallback-v1",
        "roi_safety_margin_pixels": profile.roi_safety_margin_pixels,
        "background_region": (
            {"x": background_region.x, "y": background_region.y, "width": background_region.width, "height": background_region.height}
            if background_region is not None else None
        ),
        "reasons": sorted(reasons),
        **detection,
        **background_diagnostics,
    }
    return AutomaticAnalysisProposal(profile.version, candidate, candidates, region, background_region, diagnostics)


# A concise public alias used by adapters and high-level tests.
detect_main_focal_spot = propose_auto_analysis
