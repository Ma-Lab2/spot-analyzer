"""Auditable PDF/PNG report seam backed by one immutable analysis record."""

from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import datetime, timezone
import hashlib
import math
import os
from pathlib import Path
import re
import tempfile
from types import MappingProxyType
from typing import Any, Mapping

import numpy as np
from PIL import Image, ImageDraw

from .models import AnalysisRecord


@dataclass(frozen=True)
class ReportSpecification:
    format: str
    report_name: str
    output_directory: str | Path
    append_timestamp: bool = False


@dataclass(frozen=True)
class ReportPackage:
    record_id: str
    analysis_fingerprint: str
    report_name: str
    format: str
    generated_at: str
    sections: Mapping[str, Any]
    visuals: Mapping[str, np.ndarray]


@dataclass(frozen=True)
class ExportOutcome:
    path: Path | None
    record_id: str
    flow_status: str
    sha256: str | None = None
    diagnostics: tuple[dict[str, Any], ...] = ()


def _freeze(value: Any) -> Any:
    if hasattr(value, "shape") and hasattr(value, "dtype"):
        array = np.array(value, copy=True)
        array.setflags(write=False)
        return array
    if isinstance(value, Mapping):
        return MappingProxyType({key: _freeze(item) for key, item in value.items()})
    if isinstance(value, list):
        return tuple(_freeze(item) for item in value)
    if isinstance(value, tuple):
        return tuple(_freeze(item) for item in value)
    return value


def _format_name(specification: ReportSpecification) -> str:
    format_name = specification.format.lower().lstrip(".")
    if format_name not in {"png", "pdf"}:
        raise ValueError("report format must be png or pdf")
    return format_name


def _report_diagnostics(record: AnalysisRecord) -> dict[str, Any]:
    diagnostics = dict(record.diagnostics)
    fit = dict(diagnostics.get("fit", {}))
    for internal_key in ("fit_initial_parameters", "fit_cost", "fit_nfev"):
        fit.pop(internal_key, None)
    diagnostics["fit"] = fit
    return diagnostics


def prepare_report(record: AnalysisRecord, specification: ReportSpecification) -> ReportPackage:
    format_name = _format_name(specification)
    report_name = specification.report_name.strip()
    if not report_name:
        raise ValueError("report name must not be empty")
    configuration = asdict(record.configuration)
    background_source = record.diagnostics.get("background_source")
    background_step = (
        "matched_frame_background"
        if background_source == "matched_frame"
        else "affine_background"
    )
    advanced_steps = tuple(
        record.diagnostics.get("preprocessing_advanced_branch", {}).get("steps", ())
    )
    sections = {
        "summary": {
            "record_id": record.record_id,
            "analysis_fingerprint": record.analysis_fingerprint,
            "flow_status": record.flow_status.value,
            "summary_status": record.summary_status.value,
        },
        "input": {
            **dict(record.input_metadata),
            "shape": list(record.input_shape),
        },
        "calibration": asdict(record.configuration.calibration),
        "configuration": configuration,
        "preprocessing": {
            **asdict(record.configuration.preprocessing),
            "branch": "advanced" if record.configuration.preprocessing.advanced_processing_enabled else "standard",
            "standard_branch_retained": bool(record.diagnostics.get("preprocessing_standard_branch", {}).get("retained", True)),
            "steps": (
                "decode",
                "bad_pixel_mask",
                background_step,
                "signed_correction",
                *advanced_steps,
            ),
        },
        "analysis_model": asdict(record.configuration.model),
        "diagnostics": _report_diagnostics(record),
        "metrics": record.reportable_metrics(),
        "provenance": {
            "analysis_contract": record.configuration.analysis_contract,
            "standard_profile": record.configuration.standard_profile,
            "quality_profile": record.configuration.quality_profile,
            "profile_validation": record.configuration.profile_validation,
            "algorithm_version": record.configuration.algorithm_version,
            "canonicalizer_version": record.diagnostics.get("canonicalizer_version"),
            "report_schema": "report-package-v1",
        },
    }
    visuals = {
        "输入图像": record.input_intensity,
        "校正强度图": record.corrected_intensity,
        "正信号图": record.positive_intensity,
        "高斯拟合": record.fitted_intensity,
        "拟合残差": record.fit_residual_intensity,
        "测量有效 mask": record.measurement_mask,
        "核心 mask": record.core_mask,
    }
    generated_at = datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")
    return ReportPackage(
        record.record_id,
        record.analysis_fingerprint,
        report_name,
        format_name,
        generated_at,
        _freeze(sections),
        _freeze(visuals),
    )


def _safe_name(name: str) -> str:
    normalized = re.sub(r"[^A-Za-z0-9._-]+", "_", name).strip("._")
    return normalized or "analysis-report"


def _array_preview(array: np.ndarray, size: tuple[int, int]) -> Image.Image:
    values = np.asarray(array, dtype=np.float64)
    finite = np.isfinite(values)
    if not np.any(finite):
        return Image.new("L", size, 128)
    finite_values = values[finite]
    lower, upper = np.percentile(finite_values, (1.0, 99.0))
    if upper <= lower:
        normalized = np.zeros(values.shape, dtype=np.uint8)
    else:
        normalized = np.clip((values - lower) / (upper - lower), 0.0, 1.0)
        normalized = np.where(finite, normalized, 0.0)
        normalized = np.asarray(np.round(normalized * 255.0), dtype=np.uint8)
    resampling = Image.Resampling.NEAREST if values.dtype == bool else Image.Resampling.BILINEAR
    return Image.fromarray(normalized, mode="L").resize(size, resampling)


def _annotated_preview(
    package: ReportPackage,
    name: str,
    array: np.ndarray,
    size: tuple[int, int],
) -> Image.Image:
    panel = _array_preview(array, size).convert("RGB")
    draw = ImageDraw.Draw(panel)
    height, width = np.asarray(array).shape
    draw.text((6, size[1] - 18), f"x:0..{width - 1} px  y:0..{height - 1} px", fill="yellow")
    region = package.sections["configuration"]["region"]
    center = package.sections["diagnostics"].get("center_xy", {})
    full_shape = tuple(package.sections["input"]["shape"])
    is_full_image = (height, width) == full_shape
    if is_full_image:
        left = float(region["x"]) / width * size[0]
        top = float(region["y"]) / height * size[1]
        right = float(region["x"] + region["width"]) / width * size[0]
        bottom = float(region["y"] + region["height"]) / height * size[1]
        draw.rectangle((left, top, right, bottom), outline="lime", width=2)
        center_x = float(center.get("x", 0.0)) / width * size[0]
        center_y = float(center.get("y", 0.0)) / height * size[1]
    else:
        center_x = (float(center.get("x", 0.0)) - float(region["x"])) / max(width, 1) * size[0]
        center_y = (float(center.get("y", 0.0)) - float(region["y"])) / max(height, 1) * size[1]
        draw.rectangle((0, 0, size[0] - 1, size[1] - 1), outline="lime", width=2)
    if -5 <= center_x <= size[0] + 5 and -5 <= center_y <= size[1] + 5:
        draw.line((center_x - 6, center_y, center_x + 6, center_y), fill="red", width=2)
        draw.line((center_x, center_y - 6, center_x, center_y + 6), fill="red", width=2)
    return panel


def _chart(values: np.ndarray, size: tuple[int, int]) -> Image.Image:
    panel = Image.new("RGB", size, "white")
    draw = ImageDraw.Draw(panel)
    array = np.asarray(values, dtype=np.float64)
    finite = np.isfinite(array)
    if np.count_nonzero(finite) < 2:
        draw.text((8, 8), "N/A", fill="gray")
        return panel
    array = array[finite]
    lower = float(np.min(array))
    upper = float(np.max(array))
    span = max(upper - lower, np.finfo(float).eps)
    points = []
    for index, value in enumerate(array):
        x = 8 + (size[0] - 16) * index / max(array.size - 1, 1)
        y = size[1] - 8 - (size[1] - 16) * (float(value) - lower) / span
        points.append((x, y))
    draw.rectangle((7, 7, size[0] - 8, size[1] - 8), outline="gray")
    draw.line(points, fill="navy", width=2)
    return panel


def _profile_and_energy(package: ReportPackage) -> tuple[np.ndarray, np.ndarray]:
    positive = np.asarray(package.visuals["正信号图"], dtype=np.float64)
    step = max(1, int(max(positive.shape) / 256))
    sampled = positive[::step, ::step]
    center = package.sections["diagnostics"].get("center_xy", {})
    center_x = min(sampled.shape[1] - 1, max(0, int(float(center.get("x", 0.0)) / step)))
    center_y = min(sampled.shape[0] - 1, max(0, int(float(center.get("y", 0.0)) / step)))
    profile = sampled[center_y, :]
    y, x = np.indices(sampled.shape, dtype=np.float64)
    radii = np.hypot(x - center_x, y - center_y).ravel()
    values = np.maximum(sampled, 0.0).ravel()
    order = np.argsort(radii, kind="mergesort")
    total = float(values.sum())
    energy = np.cumsum(values[order]) / total if total > 0 else np.array([])
    if energy.size > 256:
        indexes = np.linspace(0, energy.size - 1, 256).astype(int)
        energy = energy[indexes]
    return profile, energy


def _render(package: ReportPackage) -> Image.Image:
    lines = [
        f"焦斑分析报告 — {package.report_name}",
        f"record_id: {package.record_id}",
        f"generated_at: {package.generated_at}",
    ]
    for section, values in package.sections.items():
        lines.append("")
        lines.append(f"[{section}]")
        if isinstance(values, Mapping):
            for key, value in values.items():
                lines.append(f"{key}: {value}")
        else:
            lines.append(str(values))
    panel_size = (300, 190)
    visual_entries: list[tuple[str, Image.Image]] = [
        (name, _annotated_preview(package, name, array, panel_size))
        for name, array in package.visuals.items()
    ]
    profile, energy = _profile_and_energy(package)
    visual_entries.extend((("中心剖面", _chart(profile, panel_size)), ("能量曲线", _chart(energy, panel_size))))
    columns = 4
    rows = math.ceil(len(visual_entries) / columns)
    visual_height = rows * 230 + 20
    image = Image.new("RGB", (1400, max(visual_height + 28 * len(lines) + 40, 800)), "white")
    draw = ImageDraw.Draw(image)
    for index, (name, panel) in enumerate(visual_entries):
        column = index % columns
        row = index // columns
        left = 24 + column * 340
        top = 20 + row * 230
        draw.text((left, top), name, fill="black")
        image.paste(panel.convert("RGB"), (left, top + 24))
    for index, line in enumerate(lines):
        draw.text((24, visual_height + 28 * index), line, fill="black")
    return image


def _reserve_target(destination: Path, stem: str, extension: str) -> Path:
    index = 1
    while True:
        suffix = "" if index == 1 else f"-{index}"
        target = destination / f"{stem}{suffix}{extension}"
        try:
            with target.open("xb"):
                pass
            return target
        except FileExistsError:
            index += 1


def write_report(package: ReportPackage, specification: ReportSpecification) -> ExportOutcome:
    format_name = _format_name(specification)
    report_name = specification.report_name.strip()
    if format_name != package.format or report_name != package.report_name:
        raise ValueError("report package does not match export specification")
    destination = Path(specification.output_directory)
    try:
        destination.mkdir(parents=True, exist_ok=True)
    except OSError as error:
        return ExportOutcome(
            None,
            package.record_id,
            "export_failed",
            None,
            ({"code": "report_directory_create_failed", "message": str(error)},),
        )
    stem = _safe_name(report_name)
    if specification.append_timestamp:
        stem += "-" + datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    extension = "." + format_name
    target: Path | None = None
    temporary_path: Path | None = None
    phase = "reserve"
    try:
        target = _reserve_target(destination, stem, extension)
        phase = "render"
        rendered = _render(package)
        phase = "write"
        with tempfile.NamedTemporaryFile(
            prefix=".report-",
            suffix=extension,
            dir=destination,
            delete=False,
        ) as temporary:
            temporary_path = Path(temporary.name)
        if format_name == "pdf":
            rendered.save(temporary_path, format="PDF", resolution=150.0)
        else:
            rendered.save(temporary_path, format="PNG")
        digest = hashlib.sha256(temporary_path.read_bytes()).hexdigest()
        os.replace(temporary_path, target)
        return ExportOutcome(target, package.record_id, "exported", digest)
    except Exception as error:
        if temporary_path is not None:
            try:
                temporary_path.unlink(missing_ok=True)
            except OSError:
                pass
        if target is not None:
            try:
                target.unlink(missing_ok=True)
            except OSError:
                pass
        code = {
            "reserve": "report_target_reserve_failed",
            "render": "report_render_failed",
            "write": "report_write_failed",
        }[phase]
        return ExportOutcome(
            None,
            package.record_id,
            "export_failed",
            None,
            ({"code": code, "message": str(error)},),
        )
