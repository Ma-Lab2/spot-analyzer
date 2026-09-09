"""Analytic and grid-based expectations for synthetic validation scenes."""

from __future__ import annotations

import math
from typing import Any

import numpy as np


def gaussian_fwhm(width: float) -> float:
    return width * math.sqrt(2.0 * math.log(2.0))


def gaussian_d4sigma(width: float) -> float:
    return 2.0 * width


def gaussian_encircled_energy(radius: float, width: float) -> float:
    return 1.0 - math.exp(-2.0 * radius * radius / (width * width))


def circular_gaussian_oracle(width: float) -> dict[str, float]:
    fwhm = gaussian_fwhm(width)
    return {
        "fwhm": fwhm,
        "d4sigma": gaussian_d4sigma(width),
        "ee50": width * math.sqrt(math.log(2.0) / 2.0),
        "ee80": width * math.sqrt(-math.log(0.2) / 2.0),
        "ee_at_fwhm_half": gaussian_encircled_energy(fwhm / 2.0, width),
    }


def radial_encircled_energy(
    image: np.ndarray,
    center_xy: tuple[float, float],
    target: float,
) -> float | None:
    """Calculate the discrete radial oracle using pixel-center inclusion."""

    array = np.maximum(np.asarray(image, dtype=np.float64), 0.0)
    y, x = np.indices(array.shape, dtype=np.float64)
    radii = np.hypot(x - center_xy[0], y - center_xy[1]).ravel()
    values = array.ravel()
    order = np.argsort(radii, kind="mergesort")
    radii = radii[order]
    values = values[order]
    total = float(values.sum())
    if total <= 0:
        return None
    cumulative = np.cumsum(values) / total
    index = int(np.searchsorted(cumulative, target, side="left"))
    if index == 0:
        return float(radii[0])
    if index >= len(radii):
        return None
    previous = cumulative[index - 1]
    current = cumulative[index]
    if current <= previous:
        return float(radii[index])
    fraction = (target - previous) / (current - previous)
    return float(radii[index - 1] + fraction * (radii[index] - radii[index - 1]))


def scene_oracle(scene_id: str, parameters: dict[str, Any], reference: np.ndarray, center_xy: tuple[float, float]) -> dict[str, Any]:
    if scene_id == "gaussian_circular":
        return circular_gaussian_oracle(float(parameters["width"]))
    if scene_id == "gaussian_elliptical_rotated":
        wx = float(parameters["width_x"])
        wy = float(parameters["width_y"])
        return {
            "fwhm_major": gaussian_fwhm(max(wx, wy)),
            "fwhm_minor": gaussian_fwhm(min(wx, wy)),
            "d4sigma_major": gaussian_d4sigma(max(wx, wy)),
            "d4sigma_minor": gaussian_d4sigma(min(wx, wy)),
            "angle": float(parameters["angle_degrees"]),
            "ee50": radial_encircled_energy(reference - float(parameters["background"]), center_xy, 0.5),
            "ee80": radial_encircled_energy(reference - float(parameters["background"]), center_xy, 0.8),
        }
    return {
        "ee50": radial_encircled_energy(reference, center_xy, 0.5),
        "ee80": radial_encircled_energy(reference, center_xy, 0.8),
    }
