"""Internal frame and result contracts: numpy arrays never go into JSON directly."""
from __future__ import annotations
from dataclasses import dataclass
import numpy as np


@dataclass(frozen=True)
class FramePacket:
    frame_id: int
    timestamp_s: float
    image: np.ndarray
    source_id: str
    fps: float
