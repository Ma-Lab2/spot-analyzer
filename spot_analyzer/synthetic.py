"""Deterministic synthetic inputs for Issue #10 validation."""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
import hashlib
import json
from typing import Any

import numpy as np
from scipy.special import j1


GENERATOR_VERSION = "synthetic-scenes-v1"


@dataclass(frozen=True)
class SceneManifest:
    scene_id: str
    generator_version: str
    seed: int
    shape: tuple[int, int]
    bit_depth: int
    pixel_pitch_um: tuple[float, float]
    center_xy: tuple[float, float]
    model: str
    parameters: dict[str, Any]
    reference_sha256: str
    input_sha256: str
    expected_quality_status: str
    expected_reported_statuses: tuple[str, ...]
    required_reason_codes: tuple[str, ...] = ()
    forbidden_reason_codes: tuple[str, ...] = ()
    comparison_policy: str = "diagnostic"
    tolerances: dict[str, float] = field(default_factory=dict)
    analysis_contract: str = "analysis-contract-v1"
    standard_profile: str = "standard-profile-v1"
    quality_profile: str = "quality-profile-v1"
    profile_validation: str = "provisional"

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class SyntheticScene:
    manifest: SceneManifest
    reference: np.ndarray
    input_array: np.ndarray

    def __post_init__(self) -> None:
        for name in ("reference", "input_array"):
            array = np.asarray(getattr(self, name))
            array.setflags(write=False)
            object.__setattr__(self, name, array)


def _sha256_array(array: np.ndarray) -> str:
    return hashlib.sha256(np.ascontiguousarray(array).tobytes()).hexdigest()


def _grid(shape: tuple[int, int], center: tuple[float, float]) -> tuple[np.ndarray, np.ndarray]:
    height, width = shape
    y, x = np.indices((height, width), dtype=np.float64)
    return x - center[0], y - center[1]


def _gaussian(
    dx: np.ndarray,
    dy: np.ndarray,
    amplitude: float,
    width_x: float,
    width_y: float,
    angle_degrees: float = 0.0,
) -> np.ndarray:
    angle = np.deg2rad(angle_degrees)
    u = np.cos(angle) * dx + np.sin(angle) * dy
    v = -np.sin(angle) * dx + np.cos(angle) * dy
    return amplitude * np.exp(-2.0 * (u * u / width_x**2 + v * v / width_y**2))


def _make_input(reference: np.ndarray, seed: int, *, noise_sigma: float = 0.0, clip: float | None = None) -> np.ndarray:
    rng = np.random.default_rng(seed)
    result = np.asarray(reference, dtype=np.float64).copy()
    if noise_sigma:
        result += rng.normal(0.0, noise_sigma, size=result.shape)
    result = np.maximum(result, 0.0)
    if clip is not None:
        result = np.minimum(result, clip)
    return result.astype(np.float64)


def generate_scene(
    scene_id: str,
    *,
    seed: int = 0,
    shape: tuple[int, int] = (256, 256),
    pixel_pitch_um: tuple[float, float] = (0.1, 0.1),
) -> SyntheticScene:
    """Generate a scene and immutable manifest without using time or paths."""

    center = (127.5, 127.5)
    dx, dy = _grid(shape, center)
    background = 10.0
    params: dict[str, Any] = {"background": background}
    required: tuple[str, ...] = ()
    model = "gaussian"
    expected = "valid"
    comparison_policy = "diagnostic"
    reference: np.ndarray
    input_array: np.ndarray

    if scene_id == "gaussian_circular":
        params.update(amplitude=200.0, width=20.0)
        reference = _gaussian(dx, dy, 200.0, 20.0, 20.0) + background
        input_array = _make_input(reference, seed)
        comparison_policy = "analytic"
    elif scene_id == "gaussian_elliptical_rotated":
        params.update(amplitude=200.0, width_x=24.0, width_y=12.0, angle_degrees=30.0)
        reference = _gaussian(dx, dy, 200.0, 24.0, 12.0, 30.0) + background
        input_array = _make_input(reference, seed)
        comparison_policy = "analytic"
    elif scene_id == "two_gaussian_multimodal":
        params.update(amplitude=200.0, width=20.0, secondary_offset=(35.0, 0.0), secondary_amplitude=70.0)
        reference = (
            _gaussian(dx, dy, 200.0, 20.0, 20.0)
            + _gaussian(dx - 35.0, dy, 70.0, 16.0, 16.0)
            + background
        )
        input_array = _make_input(reference, seed)
        expected = "caution"
        required = ("multiple_peaks",)
    elif scene_id == "airy_sidelobe":
        alpha = 0.22
        radius = np.hypot(dx, dy)
        z = alpha * radius
        airy = np.ones_like(radius)
        nonzero = z != 0
        airy[nonzero] = (2.0 * j1(z[nonzero]) / z[nonzero]) ** 2
        params.update(amplitude=200.0, alpha=alpha)
        reference = 200.0 * airy + background
        input_array = _make_input(reference, seed)
        expected = "caution"
        required = ("ring_candidate",)
        model = "airy"
    elif scene_id == "background_gradient":
        params.update(amplitude=200.0, width=20.0, bx=0.05, by=-0.03)
        gradient = background + 0.05 * dx - 0.03 * dy
        reference = _gaussian(dx, dy, 200.0, 20.0, 20.0) + gradient
        input_array = _make_input(reference, seed)
        params["background_model"] = "affine"
        expected = "caution"
    elif scene_id == "hot_dead_pixels":
        params.update(amplitude=200.0, width=20.0, hot_pixel=(135, 127), dead_pixel=(120, 120))
        reference = _gaussian(dx, dy, 200.0, 20.0, 20.0) + background
        input_array = _make_input(reference, seed)
        input_array[127, 135] = 255.0
        input_array[120, 120] = 0.0
        expected = "caution"
        required = ("bad_pixels_present",)
    elif scene_id == "low_snr_seeded":
        low_background = 50.0
        params.update(background=low_background, amplitude=20.0, width=20.0, noise_sigma=15.0)
        reference = _gaussian(dx, dy, 20.0, 20.0, 20.0) + low_background
        input_array = _make_input(reference, seed, noise_sigma=15.0)
        expected = "caution"
    elif scene_id == "saturated_core":
        params.update(amplitude=400.0, width=20.0, clip=255.0)
        reference = _gaussian(dx, dy, 400.0, 20.0, 20.0) + background
        input_array = _make_input(reference, seed, clip=255.0)
        expected = "invalid"
        required = ("saturated_core",)
    elif scene_id == "cropped_edge":
        center = (5.0, 127.5)
        dx, dy = _grid(shape, center)
        params.update(amplitude=200.0, width=20.0, center_xy=center)
        reference = _gaussian(dx, dy, 200.0, 20.0, 20.0) + background
        input_array = _make_input(reference, seed)
        expected = "caution"
        required = ("window_truncated",)
    else:
        raise ValueError(f"unknown synthetic scene: {scene_id}")

    reference = np.asarray(reference, dtype=np.float64)
    input_array = np.asarray(input_array, dtype=np.float64)
    expected_reported = (
        ("invalid",)
        if expected == "invalid"
        else ("caution", "invalid")
    )
    tolerances = (
        {
            "fwhm_relative": 0.05,
            "d4sigma_relative": 0.05,
            "ee_radius_absolute_fwhm_fraction": 0.02,
            "angle_absolute_degrees": 2.0,
            "center_absolute_pixels": 0.5,
        }
        if comparison_policy == "analytic"
        else {}
    )
    manifest = SceneManifest(
        scene_id=scene_id,
        generator_version=GENERATOR_VERSION,
        seed=seed,
        shape=shape,
        bit_depth=8,
        pixel_pitch_um=pixel_pitch_um,
        center_xy=center,
        model=model,
        parameters=params,
        reference_sha256=_sha256_array(reference),
        input_sha256=_sha256_array(input_array),
        expected_quality_status=expected,
        expected_reported_statuses=expected_reported,
        required_reason_codes=required,
        comparison_policy=comparison_policy,
        tolerances=tolerances,
    )
    return SyntheticScene(manifest, reference, input_array)


def manifest_json(scene: SyntheticScene) -> str:
    """Return canonical JSON suitable for a committed regression manifest."""

    return json.dumps(scene.manifest.to_dict(), ensure_ascii=False, sort_keys=True, separators=(",", ":"))
