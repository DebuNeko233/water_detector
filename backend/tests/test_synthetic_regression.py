"""CI-safe pixel-for-pixel regression that needs no customer video fixture."""
from __future__ import annotations
import importlib.util
import sys
from pathlib import Path
import numpy as np
from water_detector.detector.water import WaterDetector
from water_detector.config.schema import DetectorConfig, Calibration


def test_synthetic_frames_match_original_main_py():
    old_path = Path(__file__).resolve().parents[2] / 'legacy' / 'main.py'
    spec = importlib.util.spec_from_file_location('original_water_main', old_path)
    assert spec and spec.loader
    old = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = old
    spec.loader.exec_module(old)
    size = (120, 140)
    fps = 30
    cx, cy, radius = 69, 60, 48
    original = old.WaterDetector(size, fps, old.Calibration(cx, cy, radius), old.DemoConfig())
    modern = WaterDetector(size, fps, Calibration(cx=cx, cy=cy, radius=radius,
                         reference_width=size[1], reference_height=size[0]), DetectorConfig())
    yy, xx = np.indices(size)
    for t in range(80):
        # Deterministic rotating stripes, a localized moving patch, and exposure drift.
        intensity = 95 + 35 * np.sin((xx * 0.22 + yy * 0.14 + t * 0.34))
        intensity += 16 * np.sin(t * 0.19)
        region = (xx - (cx + 30)) ** 2 + (yy - cy) ** 2 < 14 ** 2
        intensity += region * (20 if (t % 6) < 3 else -8)
        gray = np.clip(intensity, 0, 255).astype(np.uint8)
        frame = np.repeat(gray[:, :, None], 3, axis=2)
        expected, actual = original.process(frame), modern.process(frame)
        assert expected['status'] == actual['status'], f'frame {t}'
        assert expected['s_water'] == actual['s_water'], f'frame {t}'
        assert expected['sector_scores'] == actual['sector_scores'], f'frame {t}'
