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
import rfc8785
from PIL import Image, ImageDraw, ImageFont

from .models import AnalysisRecord, AnalysisRecordKind


@dataclass(frozen=True)
class ReportSpecification:
    """Human report rendering and output policy.

    The positional ``(format, report_name, output_directory)`` form remains
    supported for existing callers.  Omitting values selects the Issue #81
    defaults: PNG, an input-derived Chinese name, and a UTC timestamp suffix.
    """

    format: str = "png"
    report_name: str | None = None
    output_directory: str | Path = "."
    append_timestamp: bool = True


@dataclass(frozen=True)
class ReportWorkItem:
    """Explicit batch-export eligibility seam for a formal analysis record."""

    record: AnalysisRecord | None
    is_preview: bool = False
    is_stale: bool = False
    is_formal: bool = True
    report_name: str | None = None


@dataclass(frozen=True)
class BatchExportSummary:
    outcomes: tuple["ExportOutcome", ...]

    @property
    def exported_count(self) -> int:
        return sum(outcome.flow_status == "exported" for outcome in self.outcomes)

    @property
    def failed_count(self) -> int:
        return sum(outcome.flow_status == "export_failed" for outcome in self.outcomes)

    @property
    def skipped_count(self) -> int:
        return sum(outcome.flow_status == "skipped" for outcome in self.outcomes)

    @property
    def results(self) -> tuple["ExportOutcome", ...]:
        """Compatibility spelling for callers that call outcomes results."""
        return self.outcomes


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
    format_name = specification.format.strip().lower().lstrip(".")
    if format_name not in {"png", "pdf"}:
        raise ValueError("report format must be png or pdf")
    return format_name


def _input_name(record: AnalysisRecord) -> str:
    metadata = dict(record.input_metadata)
    nested_metadata = metadata.get("metadata")
    if isinstance(nested_metadata, Mapping):
        metadata = {**dict(nested_metadata), **metadata}
    candidates = (
        metadata.get("file_name"),
        metadata.get("filename"),
        metadata.get("name"),
        metadata.get("uri_hint"),
    )
    for candidate in candidates:
        if not candidate:
            continue
        text = str(candidate)
        if "://" in text:
            text = text.rsplit("/", 1)[-1]
        name = Path(text).stem
        if name:
            return name
    return "焦斑图像"


def _resolved_report_name(record: AnalysisRecord, specification: ReportSpecification) -> str:
    requested = specification.report_name
    if requested is None:
        return f"{_input_name(record)} 焦斑分析报告"
    report_name = requested.strip()
    if not report_name:
        raise ValueError("report name must not be empty")
    return report_name


def _jsonable(value: Any) -> Any:
    if isinstance(value, Mapping):
        return {str(key): _jsonable(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_jsonable(item) for item in value]
    if isinstance(value, np.ndarray):
        return [_jsonable(item) for item in value.tolist()]
    if isinstance(value, np.generic):
        return _jsonable(value.item())
    return value


def _parameter_snapshot_hash(record: AnalysisRecord) -> str:
    canonical = rfc8785.dumps(_jsonable(asdict(record.configuration)))
    return "sha256-" + hashlib.sha256(canonical).hexdigest()


def _report_diagnostics(record: AnalysisRecord) -> dict[str, Any]:
    diagnostics = dict(record.diagnostics)
    fit = dict(diagnostics.get("fit", {}))
    for internal_key in (
        "fit_initial_parameters",
        "fit_cost",
        "fit_nfev",
        "fit_covariance",
        "fit_parameters",
        "fit_active_mask",
        "fit_optimality",
    ):
        fit.pop(internal_key, None)
    diagnostics["fit"] = fit
    advanced = dict(diagnostics.get("preprocessing_advanced_branch", {}))
    for internal_key in (
        "standard_fwhm_estimate",
        "advanced_fwhm_estimate",
        "sensitivity_fraction",
        "sensitivity",
    ):
        advanced.pop(internal_key, None)
    diagnostics["preprocessing_advanced_branch"] = advanced
    return diagnostics


def prepare_report(record: AnalysisRecord, specification: ReportSpecification) -> ReportPackage:
    if record.record_kind == AnalysisRecordKind.PREVIEW:
        raise ValueError("preview analysis records cannot be exported")
    format_name = _format_name(specification)
    report_name = _resolved_report_name(record, specification)
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
            "record_kind": record.record_kind.value,
            "measurement_semantics": record.measurement_semantics,
            "measurement_semantics_confirmed": record.measurement_semantics_confirmed,
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
            "parameter_snapshot_hash": _parameter_snapshot_hash(record),
            "export_contract": "report-package-v1",
            "software": {
                "name": "spot-analyzer",
                "version": "0.1.0",
                "build": "prototype",
            },
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
    # Keep Unicode letters (including Chinese) while replacing path/control
    # characters.  This is safe on Windows and keeps the human report name.
    normalized = "".join(
        character if (character.isalnum() or character in "._-" or character == " ") else "_"
        for character in name
    )
    normalized = re.sub(r"\s+", "_", normalized)
    normalized = re.sub(r"_+", "_", normalized).strip("._")
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


def _chart(
    x_values: np.ndarray,
    values: np.ndarray,
    size: tuple[int, int],
    secondary: np.ndarray | None = None,
) -> Image.Image:
    panel = Image.new("RGB", size, "white")
    draw = ImageDraw.Draw(panel)
    x_array = np.asarray(x_values, dtype=np.float64)
    series = [np.asarray(values, dtype=np.float64)]
    if secondary is not None:
        series.append(np.asarray(secondary, dtype=np.float64))
    finite_values = np.concatenate(
        [item[np.isfinite(item)] for item in series if np.any(np.isfinite(item))]
    )
    finite_x = x_array[np.isfinite(x_array)]
    if finite_values.size < 2 or finite_x.size < 2:
        draw.text((8, 8), "N/A", fill="gray")
        return panel
    lower_x = float(np.min(finite_x))
    span_x = max(float(np.max(finite_x)) - lower_x, np.finfo(float).eps)
    lower = float(np.min(finite_values))
    span = max(float(np.max(finite_values)) - lower, np.finfo(float).eps)
    draw.rectangle((7, 7, size[0] - 8, size[1] - 8), outline="gray")
    for index, current in enumerate(series):
        finite = np.isfinite(x_array) & np.isfinite(current)
        if np.count_nonzero(finite) < 2:
            continue
        current_x = x_array[finite]
        current_values = current[finite]
        points = [
            (
                8 + (size[0] - 16) * (float(x) - lower_x) / span_x,
                size[1] - 8 - (size[1] - 16) * (float(value) - lower) / span,
            )
            for x, value in zip(current_x, current_values)
        ]
        draw.line(points, fill=("navy" if index == 0 else "crimson"), width=2)
    return panel


def _report_curves(package: ReportPackage) -> Mapping[str, Any]:
    curves = package.sections["diagnostics"].get("report_curves")
    if not isinstance(curves, Mapping):
        raise ValueError("analysis record does not contain report curves")
    return curves


_STATUS_LABELS = {
    "valid": "有效",
    "caution": "需复核",
    "warning": "需复核",
    "invalid": "无效",
    "unavailable": "不可用",
    "not_applicable": "不适用",
    "computed": "计算完成",
    "exported": "已导出",
}

_REASON_LABELS = {
    "calibration_missing": "缺少空间标定",
    "calibration_provisional": "空间标定尚未确认",
    "core_clipped": "核心区域被边界截断",
    "low_snr": "信噪比偏低",
    "rref_missing": "缺少独立参考半径",
    "window_truncated": "分析窗口截断信号",
}

_SECTION_LABELS = {
    "summary": "总体摘要",
    "input": "输入图像",
    "calibration": "空间标定",
    "configuration": "分析配置",
    "preprocessing": "预处理",
    "analysis_model": "分析模型",
    "diagnostics": "质量诊断",
    "metrics": "测量指标",
    "provenance": "复现信息",
}


def _status_text(status: Any) -> str:
    code = str(status)
    return f"{_STATUS_LABELS.get(code, code)} ({code})"


def _reason_text(codes: Any) -> str:
    if not isinstance(codes, (list, tuple)):
        codes = [] if codes is None else [codes]
    return ", ".join(
        f"{code}（{_REASON_LABELS.get(str(code), '质量原因')}）"
        for code in codes
    ) or "无"


def _collect_reason_codes(value: Any) -> tuple[str, ...]:
    found: set[str] = set()
    if isinstance(value, Mapping):
        for key, child in value.items():
            if key in {"reason_codes", "quality_reason_codes", "reasons"}:
                if isinstance(child, (list, tuple)):
                    found.update(str(item) for item in child)
                elif child:
                    found.add(str(child))
            found.update(_collect_reason_codes(child))
    elif isinstance(value, (list, tuple)):
        for child in value:
            found.update(_collect_reason_codes(child))
    return tuple(sorted(found))


def _display_value(value: Any) -> str:
    if value is None:
        return "N/A"
    if isinstance(value, np.ndarray):
        return f"数组(shape={list(value.shape)})"
    if isinstance(value, (list, tuple)):
        return ", ".join(_display_value(item) for item in value)
    if isinstance(value, Mapping):
        return "{" + ", ".join(f"{key}: {_display_value(item)}" for key, item in value.items()) + "}"
    return str(value)


def _metric_text_lines(metrics: Mapping[str, Any]) -> list[str]:
    lines: list[str] = []
    for name, metric in metrics.items():
        if not isinstance(metric, Mapping):
            lines.append(f"{name}: N/A; 状态：不可用 (unavailable); 原因码：report_metric_malformed")
            continue
        domains = metric.get("domains")
        entries = domains.items() if isinstance(domains, Mapping) else ((None, metric),)
        for domain, entry in entries:
            if not isinstance(entry, Mapping):
                entry = {}
            status = str(entry.get("status", "unavailable"))
            value = entry.get("value") if status in {"valid", "caution", "warning"} else None
            label = f"{name} ({domain})" if domain else str(name)
            lines.append(
                f"{label}: {_display_value(value)} {entry.get('unit', '')}; "
                f"状态：{_status_text(status)}; 原因码：{_reason_text(entry.get('reason_codes', ())) }"
            )
    return lines


def report_text(package: ReportPackage) -> str:
    """Return the human-facing text model used by both visual formats."""
    summary = package.sections["summary"]
    lines = [
        f"焦斑分析报告 — {package.report_name}",
        f"记录编号：{package.record_id}",
        f"生成时间：{package.generated_at}",
        f"总体流程状态：{_status_text(summary.get('flow_status'))}",
        f"总体测量有效性：{_status_text(summary.get('summary_status'))}",
        f"全部原因码：{_reason_text(_collect_reason_codes(package.sections))}",
    ]
    for section, values in package.sections.items():
        lines.extend(("", f"【{_SECTION_LABELS.get(section, section)}】"))
        if section == "metrics" and isinstance(values, Mapping):
            lines.extend(_metric_text_lines(values))
        elif isinstance(values, Mapping):
            for key, value in values.items():
                lines.append(f"{key}：{_display_value(value)}")
        else:
            lines.append(_display_value(values))
    return "\n".join(lines)


def _report_font(size: int) -> ImageFont.FreeTypeFont | ImageFont.ImageFont:
    # Prefer a CJK-capable font so Chinese labels are rendered, not replaced by
    # tofu glyphs.  The fallback keeps headless/non-Windows environments usable.
    candidates = (
        r"C:\\Windows\\Fonts\\msyh.ttc",
        r"C:\\Windows\\Fonts\\NotoSansSC-VF.ttf",
        "/usr/share/fonts/opentype/noto/NotoSansCJK-Regular.ttc",
        "/usr/share/fonts/truetype/noto/NotoSansCJK-Regular.ttc",
    )
    for candidate in candidates:
        try:
            return ImageFont.truetype(candidate, size)
        except OSError:
            continue
    return ImageFont.load_default()


def _render(package: ReportPackage) -> Image.Image:
    lines = report_text(package).splitlines()
    font = _report_font(16)
    panel_size = (300, 190)
    visual_entries: list[tuple[str, Image.Image]] = [
        (name, _annotated_preview(package, name, array, panel_size))
        for name, array in package.visuals.items()
    ]
    curves = _report_curves(package)
    profile_x = np.asarray(curves["profile_x_pixels"], dtype=np.float64)
    profile = np.asarray(curves["profile"], dtype=np.float64)
    fitted_profile = np.asarray(curves["fitted_profile"], dtype=np.float64)
    energy_radius = np.asarray(curves["energy_radius"], dtype=np.float64)
    energy_fraction = np.asarray(curves["energy_fraction"], dtype=np.float64)
    visual_entries.extend(
        (
            ("中心剖面（实际+拟合）", _chart(profile_x, profile, panel_size, fitted_profile)),
            (
                f"能量曲线（半径/{curves['energy_radius_unit']}）",
                _chart(energy_radius, energy_fraction, panel_size),
            ),
        )
    )
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
        draw.text((left, top), name, fill="black", font=font)
        image.paste(panel.convert("RGB"), (left, top + 24))
    for index, line in enumerate(lines):
        draw.text((24, visual_height + 28 * index), line, fill="black", font=font)
    return image


def _reserve_target(destination: Path, stem: str, extension: str) -> Path:
    """Return an available candidate without creating a visible empty report."""
    index = 1
    while True:
        suffix = "" if index == 1 else f"-{index}"
        target = destination / f"{stem}{suffix}{extension}"
        if not target.exists():
            return target
        index += 1


def write_report(package: ReportPackage, specification: ReportSpecification) -> ExportOutcome:
    format_name = _format_name(specification)
    report_name = package.report_name
    requested_name = specification.report_name
    if requested_name is not None and requested_name.strip() != report_name:
        raise ValueError("report package does not match export specification")
    # A single immutable package supplies every file representation. The
    # requested output format is a rendering choice, not a new measurement.
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
    published = False
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
        candidate = target
        index = 1
        while True:
            try:
                # Same-directory rename is atomic; Windows refuses to replace
                # an existing destination, so a race advances to a new suffix.
                os.rename(temporary_path, candidate)
                target = candidate
                published = True
                break
            except FileExistsError:
                index += 1
                candidate = destination / f"{stem}-{index}{extension}"
        return ExportOutcome(target, package.record_id, "exported", digest)
    except Exception as error:
        if temporary_path is not None:
            try:
                temporary_path.unlink(missing_ok=True)
            except OSError:
                pass
        if target is not None and not published:
            # The candidate was never published; never remove another export
            # that won a collision race.
            try:
                if target.exists() and target.stat().st_size == 0:
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


def _skip_outcome(item: ReportWorkItem, code: str, message: str) -> ExportOutcome:
    record_id = item.record.record_id if item.record is not None else ""
    return ExportOutcome(None, record_id, "skipped", None, ({"code": code, "message": message},))


def is_report_eligible(item: ReportWorkItem | AnalysisRecord) -> bool:
    """Apply only export eligibility gates; analysis owns measurement states."""
    if isinstance(item, AnalysisRecord):
        item = ReportWorkItem(item)
    return (
        item.record is not None
        and item.record.record_kind == AnalysisRecordKind.FORMAL
        and item.is_formal
        and not item.is_preview
        and not item.is_stale
        and item.record.flow_status.value == "computed"
    )


def _coerce_work_item(value: Any) -> ReportWorkItem:
    if isinstance(value, ReportWorkItem):
        return value
    if isinstance(value, AnalysisRecord):
        return ReportWorkItem(value)
    if isinstance(value, Mapping):
        record = value.get("record", value.get("analysis_record"))
        return ReportWorkItem(
            record if isinstance(record, AnalysisRecord) else None,
            is_preview=bool(value.get("is_preview", value.get("preview", False))),
            is_stale=bool(value.get("is_stale", value.get("stale", False))),
            is_formal=bool(value.get("is_formal", value.get("formal", True))),
            report_name=value.get("report_name"),
        )
    return ReportWorkItem(None)


def batch_export_reports(
    work_items: Any,
    specification: ReportSpecification | None = None,
) -> BatchExportSummary:
    """Export eligible formal records independently and return an aggregate.

    Each item is prepared and written in isolation.  A malformed item or one
    failed output does not prevent subsequent records from being attempted.
    ``AnalysisRecord`` values are accepted as a compatibility shorthand for
    formal, current work items; callers that have preview/stale state should
    use ``ReportWorkItem`` explicitly.
    """
    base_specification = specification or ReportSpecification()
    outcomes: list[ExportOutcome] = []
    for value in work_items:
        item = _coerce_work_item(value)
        if item.record is None:
            outcomes.append(_skip_outcome(item, "report_record_missing", "No formal analysis record is available."))
            continue
        if item.record.record_kind != AnalysisRecordKind.FORMAL or not item.is_formal:
            outcomes.append(_skip_outcome(item, "report_item_not_formal", "Only formal analysis records can be exported."))
            continue
        if item.is_preview:
            outcomes.append(_skip_outcome(item, "report_item_preview", "Preview records cannot be exported."))
            continue
        if item.is_stale:
            outcomes.append(_skip_outcome(item, "report_item_stale", "Stale records must be recalculated before export."))
            continue
        if item.record.flow_status.value != "computed":
            outcomes.append(_skip_outcome(item, "report_record_not_computed", "Only computed formal records can be exported."))
            continue
        item_specification = base_specification
        if item.report_name is not None:
            item_specification = replace(base_specification, report_name=item.report_name)
        try:
            package = prepare_report(item.record, item_specification)
            outcomes.append(write_report(package, item_specification))
        except Exception as error:
            outcomes.append(
                ExportOutcome(
                    None,
                    item.record.record_id,
                    "export_failed",
                    None,
                    ({"code": "report_prepare_failed", "message": str(error)},),
                )
            )
    return BatchExportSummary(tuple(outcomes))


# Descriptive aliases keep the batch seam discoverable without introducing a
# second workflow/state machine.
export_reports_batch = batch_export_reports
export_batch_reports = batch_export_reports
