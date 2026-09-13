"""Authoritative versioned analysis profile and settings seam.

The JSON profile is the only source of standard defaults.  Callers may submit a
complete historical snapshot, or a profile id plus advanced overrides; the
worker expands the latter before constructing an analysis configuration.
"""
from __future__ import annotations

from copy import deepcopy
from dataclasses import dataclass
import json
from pathlib import Path
from typing import Any, Mapping


_PROFILE_PATH = Path(__file__).resolve().parents[1] / "profiles" / "standard-profile-v1.json"


class ProfileValidationError(ValueError):
    """A user supplied profile/advanced setting is not supported."""

    def __init__(self, code: str, message: str, field: str | None = None) -> None:
        super().__init__(message)
        self.code = code
        self.field = field


def _load() -> dict[str, Any]:
    try:
        with _PROFILE_PATH.open(encoding="utf-8") as stream:
            profile = json.load(stream)
    except (OSError, json.JSONDecodeError) as error:
        raise RuntimeError(f"analysis profile cannot be loaded: {_PROFILE_PATH}") from error
    if profile.get("schema") != "analysis-profile-v1":
        raise RuntimeError("analysis profile schema is unsupported")
    return profile


_PROFILE = _load()


def get_analysis_profile(profile_id: str = "standard-profile-v1") -> dict[str, Any]:
    """Return a detached profile snapshot suitable for a request or record."""
    if profile_id != _PROFILE["standard_profile"]:
        raise ProfileValidationError("profile_unsupported", f"Unsupported analysis profile: {profile_id}", "standard_profile")
    return deepcopy(_PROFILE)


def default_preprocessing_values() -> dict[str, Any]:
    return deepcopy(_PROFILE["preprocessing"])


def default_model_values() -> dict[str, Any]:
    return deepcopy(_PROFILE["model"])


def _equal(left: Any, right: Any) -> bool:
    return (left == right and type(left) is type(right)) or (
        isinstance(left, (int, float))
        and isinstance(right, (int, float))
        and float(left) == float(right)
    )


def advanced_settings_status(overrides: Mapping[str, Any] | None) -> dict[str, Any]:
    """Describe whether advanced settings differ from the recommended profile."""
    values = dict(overrides or {})
    defaults = _PROFILE["preprocessing"]
    changed = sorted(
        key for key, value in values.items()
        if key in defaults and not _equal(value, defaults[key])
    )
    return {
        "is_default": not changed,
        "deviated": bool(changed),
        "changed_fields": changed,
        "label": "已偏离推荐默认值" if changed else "使用推荐默认值",
    }


def validate_advanced_settings(overrides: Mapping[str, Any] | None) -> dict[str, Any]:
    """Validate and normalize the small, explicitly supported advanced branch."""
    if overrides is None:
        return {}
    if not isinstance(overrides, Mapping):
        raise ProfileValidationError("advanced_settings_invalid", "高级设置必须是对象")
    spec = _PROFILE["advanced_settings"]
    allowed = set(spec["allowed"])
    unknown = sorted(set(overrides) - allowed)
    if unknown:
        raise ProfileValidationError("advanced_setting_unsupported", f"不支持的高级设置: {', '.join(unknown)}", unknown[0])
    choices = {
        "bad_pixel_policy": {"mask_only", "interpolate"},
        "filtering": {"none", "gaussian"},
        "dpc": {"none", "gradient"},
    }
    result: dict[str, Any] = {}
    for key, value in overrides.items():
        if key in choices:
            if not isinstance(value, str) or value not in choices[key]:
                raise ProfileValidationError("advanced_setting_invalid", f"高级设置 {key} 的值不受支持", key)
            result[key] = value
            continue
        bounds = spec["ranges"].get(key)
        if bounds is None or isinstance(value, bool) or not isinstance(value, (int, float)):
            raise ProfileValidationError("advanced_setting_invalid", f"高级设置 {key} 的值无效", key)
        numeric = float(value)
        if not numeric == numeric or numeric in (float("inf"), float("-inf")) or not bounds[0] <= numeric <= bounds[1]:
            raise ProfileValidationError("advanced_setting_out_of_range", f"高级设置 {key} 必须在 {bounds[0]} 到 {bounds[1]} 之间", key)
        if "radius" in key:
            if int(numeric) != numeric:
                raise ProfileValidationError("advanced_setting_integer_required", f"高级设置 {key} 必须是整数", key)
            result[key] = int(numeric)
        else:
            result[key] = numeric
    return result


def resolve_preprocessing(overrides: Mapping[str, Any] | None = None) -> dict[str, Any]:
    """Merge profile defaults with validated advanced options.

    A complete preprocessing snapshot is accepted for historical records, but
    new partial requests can only override the advanced-settings allow-list.
    """
    if overrides is None:
        return default_preprocessing_values()
    if not isinstance(overrides, Mapping):
        raise ProfileValidationError("preprocessing_invalid", "预处理设置必须是对象")
    if not overrides:
        raise ProfileValidationError("preprocessing_incomplete", "configuration.preprocessing snapshot is incomplete")
    defaults = default_preprocessing_values()
    keys = set(overrides)
    non_advanced = keys - set(_PROFILE["advanced_settings"]["allowed"])
    if non_advanced:
        # Explicit full snapshots preserve old contract semantics, while a
        # partial snapshot cannot silently redefine fixed quality science.
        missing = set(defaults) - keys
        if missing:
            key = sorted(non_advanced)[0]
            raise ProfileValidationError("profile_parameter_unsupported", f"标准档案参数 {key} 不可由用户修改", key)
        merged = dict(overrides)
        if set(merged) != set(defaults):
            raise ProfileValidationError("preprocessing_incomplete", "预处理快照不完整")
        return merged
    merged = defaults
    merged.update(validate_advanced_settings(overrides))
    if any(merged[key] != defaults[key] for key in ("bad_pixel_policy", "filtering", "dpc")) or any(
        key in overrides for key in set(_PROFILE["advanced_settings"]["allowed"]) - {"bad_pixel_policy", "filtering", "dpc"}
    ):
        merged["advanced_processing_enabled"] = True
    return merged


def resolve_model(overrides: Mapping[str, Any] | None = None) -> dict[str, Any]:
    """Return the profile model, rejecting partial science/model overrides."""
    if overrides is None:
        return default_model_values()
    defaults = default_model_values()
    if dict(overrides) != defaults:
        raise ProfileValidationError("model_override_unsupported", "主模型和优化器由标准档案固定", "model")
    return dict(overrides)


@dataclass(frozen=True)
class ProfileDeviation:
    deviated: bool
    changed_fields: tuple[str, ...]

    @property
    def label(self) -> str:
        return "已偏离推荐默认值" if self.deviated else "使用推荐默认值"
