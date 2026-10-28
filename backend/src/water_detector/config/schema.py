"""Validated, serializable detection and acquisition settings."""
from __future__ import annotations
from typing import Literal
from pydantic import BaseModel, Field, model_validator


class DetectorConfig(BaseModel):
    """Preserve main.py defaults and their numeric meaning."""
    inner_radius_ratio: float = Field(0.65, ge=0.05, le=1.5)
    outer_radius_ratio: float = Field(0.95, ge=0.05, le=1.5)
    diff_threshold: float = Field(8.0, ge=0.0, le=255.0)
    history_seconds: float = Field(0.50, gt=0.0, le=10.0)
    persistence_threshold: float = Field(0.50, ge=0.0, le=1.0)
    s_water_threshold: float = Field(0.010, ge=0.0, le=1.0)
    ng_hold_seconds: float = Field(0.50, ge=0.0, le=30.0)
    recover_hold_seconds: float = Field(0.30, ge=0.0, le=30.0)
    sector_count: int = Field(24, ge=4, le=180)
    sector_threshold: float = Field(0.05, ge=0.0, le=1.0)
    blur_ksize: int = Field(3, ge=0, le=15)

    @model_validator(mode="after")
    def validate_geometry(self):
        if self.inner_radius_ratio >= self.outer_radius_ratio:
            raise ValueError("inner_radius_ratio must be smaller than outer_radius_ratio")
        if self.blur_ksize > 1 and self.blur_ksize % 2 == 0:
            raise ValueError("blur_ksize must be odd or <= 1")
        return self


class Calibration(BaseModel):
    cx: float
    cy: float
    radius: float = Field(gt=10.0)
    reference_width: int = Field(ge=1)
    reference_height: int = Field(ge=1)


class SourceConfig(BaseModel):
    kind: Literal["file", "usb", "rtsp", "industrial"] = "file"
    address: str = ""
    device_index: int = Field(0, ge=0)


class AppConfig(BaseModel):
    version: int = 1
    detector: DetectorConfig = Field(default_factory=DetectorConfig)
    calibrations: dict[str, Calibration] = Field(default_factory=dict)
    source: SourceConfig = Field(default_factory=SourceConfig)
    # Auto selects among three distinct calibrated wafer ROI locations.
    stage_mode: Literal["single", "auto"] = "single"
    selected_stage: Literal["P1", "P2", "P3"] = "P1"
    # Kept to read pre-migration config files. Geometry-based auto ignores it.
    position_references: dict[str, str] = Field(default_factory=dict)
