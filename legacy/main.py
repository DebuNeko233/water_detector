#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
晶圆洗片水流/积水检测 Demo（传统视觉版）

核心思路：
1) 手工标定晶圆中心与半径（Demo 阶段最稳妥）
2) 只分析晶圆外缘环形 ROI
3) 相邻帧差 + 全局亮度补偿，得到 water activity mask
4) 最近一段时间内统计每个像素的持续扰动比例
5) 计算 S_water（持续扰动面积占外缘 ROI 的比例）
6) 将圆周分扇区，输出异常角度
7) 用持续时间状态机给出 OK / NG

依赖：
    pip install opencv-python numpy

示例：
    python wafer_water_demo.py --video 好的洗片.mp4

无 GUI 批处理（必须给中心和半径）：
    python wafer_water_demo.py --video 好的洗片.mp4 --cx 255 --cy 400 --r 150 --no-display
"""

from __future__ import annotations

import argparse
import csv
import math
import os
from collections import deque
from dataclasses import dataclass
from pathlib import Path
from typing import Deque, List, Optional, Sequence, Tuple

import cv2
import numpy as np


@dataclass
class DemoConfig:
    # 环形 ROI：只看晶圆外缘
    inner_radius_ratio: float = 0.65
    outer_radius_ratio: float = 0.95

    # 相邻帧差阈值（8-bit 灰度）
    diff_threshold: float = 8.0

    # 时序驻留参数
    history_seconds: float = 0.50
    persistence_threshold: float = 0.50

    # 整体异常阈值：persistent water area / ring ROI area
    s_water_threshold: float = 0.010

    # 连续异常多久才进入 NG
    ng_hold_seconds: float = 0.50

    # 连续恢复多久才从 NG 回到 OK
    recover_hold_seconds: float = 0.30

    # 扇区
    sector_count: int = 24
    sector_threshold: float = 0.05

    # 预处理
    blur_ksize: int = 3

    # 可视化
    panel_width: int = 330
    graph_seconds: float = 5.0


@dataclass
class Calibration:
    cx: float
    cy: float
    radius: float


class WaterDetector:
    def __init__(self, shape_hw: Tuple[int, int], fps: float, calib: Calibration, cfg: DemoConfig):
        self.h, self.w = shape_hw
        self.fps = fps if fps > 1e-6 else 30.0
        self.calib = calib
        self.cfg = cfg

        self.history_frames = max(2, int(round(cfg.history_seconds * self.fps)))
        self.activity_history: Deque[np.ndarray] = deque(maxlen=self.history_frames)
        self.score_history: Deque[float] = deque(maxlen=max(10, int(round(cfg.graph_seconds * self.fps))))

        self.prev_gray: Optional[np.ndarray] = None
        self.abnormal_run_s = 0.0
        self.recover_run_s = 0.0
        self.status = "WARMUP"

        self._build_geometry()

    def _build_geometry(self) -> None:
        yy, xx = np.mgrid[0:self.h, 0:self.w]
        dx = xx.astype(np.float32) - float(self.calib.cx)
        dy = yy.astype(np.float32) - float(self.calib.cy)

        r = np.sqrt(dx * dx + dy * dy)
        self.phi = (np.arctan2(dy, dx) + 2.0 * np.pi) % (2.0 * np.pi)

        inner = self.cfg.inner_radius_ratio * self.calib.radius
        outer = self.cfg.outer_radius_ratio * self.calib.radius
        self.roi = (r >= inner) & (r <= outer)
        self.roi_count = int(np.count_nonzero(self.roi))
        if self.roi_count < 100:
            raise ValueError("ROI 太小，请检查晶圆中心/半径标定。")

        self.sector_masks: List[np.ndarray] = []
        self.sector_counts: List[int] = []
        n = self.cfg.sector_count
        for k in range(n):
            a0 = 2.0 * np.pi * k / n
            a1 = 2.0 * np.pi * (k + 1) / n
            m = self.roi & (self.phi >= a0) & (self.phi < a1)
            self.sector_masks.append(m)
            self.sector_counts.append(max(1, int(np.count_nonzero(m))))

    def _prep_gray(self, frame: np.ndarray) -> np.ndarray:
        gray = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)
        k = int(self.cfg.blur_ksize)
        if k >= 3:
            if k % 2 == 0:
                k += 1
            gray = cv2.GaussianBlur(gray, (k, k), 0)
        return gray

    def process(self, frame: np.ndarray) -> dict:
        gray = self._prep_gray(frame)
        dt = 1.0 / self.fps

        # 第一帧：没有帧差
        if self.prev_gray is None:
            self.prev_gray = gray
            self.score_history.append(0.0)
            return self._empty_result()

        cur = gray.astype(np.float32)
        pre = self.prev_gray.astype(np.float32)
        signed_diff = cur - pre

        # 去掉 ROI 内整体亮度的同步漂移，降低自动曝光/环境亮度小变化的影响
        global_change = float(np.median(signed_diff[self.roi]))
        diff = np.abs(signed_diff - global_change)

        active = (diff > self.cfg.diff_threshold) & self.roi
        self.activity_history.append(active)
        self.prev_gray = gray

        # history 未填满时先 warmup，避免刚开始就误判
        if len(self.activity_history) < self.history_frames:
            self.score_history.append(0.0)
            return self._empty_result(active=active, diff=diff)

        # [T,H,W] -> [H,W]，表示最近 history 中每个像素有多少比例的时间在变化
        stack = np.stack(self.activity_history, axis=0)
        persistence = stack.mean(axis=0)
        persistent = (persistence >= self.cfg.persistence_threshold) & self.roi

        s_water = float(np.count_nonzero(persistent) / self.roi_count)

        sector_scores: List[float] = []
        for m, cnt in zip(self.sector_masks, self.sector_counts):
            score = float(np.count_nonzero(persistent & m) / cnt)
            sector_scores.append(score)

        max_sector_idx = int(np.argmax(sector_scores)) if sector_scores else -1
        max_sector_score = sector_scores[max_sector_idx] if max_sector_idx >= 0 else 0.0

        instant_abnormal = s_water >= self.cfg.s_water_threshold

        # 状态机：进入 NG 与恢复使用不同的持续时间，减少抖动
        if instant_abnormal:
            self.abnormal_run_s += dt
            self.recover_run_s = 0.0
        else:
            self.recover_run_s += dt
            if self.status != "NG":
                self.abnormal_run_s = 0.0

        if self.status in ("WARMUP", "OK"):
            if self.abnormal_run_s >= self.cfg.ng_hold_seconds:
                self.status = "NG"
            else:
                self.status = "OK"
        elif self.status == "NG":
            if instant_abnormal:
                self.recover_run_s = 0.0
            elif self.recover_run_s >= self.cfg.recover_hold_seconds:
                self.status = "OK"
                self.abnormal_run_s = 0.0
                self.recover_run_s = 0.0

        self.score_history.append(s_water)

        return {
            "status": self.status,
            "s_water": s_water,
            "active": active,
            "persistent": persistent,
            "persistence": persistence,
            "diff": diff,
            "global_change": global_change,
            "sector_scores": sector_scores,
            "max_sector_idx": max_sector_idx,
            "max_sector_score": max_sector_score,
            "abnormal_run_s": self.abnormal_run_s,
            "history_ready": True,
        }

    def _empty_result(self, active: Optional[np.ndarray] = None, diff: Optional[np.ndarray] = None) -> dict:
        if self.status == "WARMUP" and len(self.activity_history) >= self.history_frames:
            self.status = "OK"
        return {
            "status": "WARMUP" if len(self.activity_history) < self.history_frames else self.status,
            "s_water": 0.0,
            "active": active,
            "persistent": None,
            "persistence": None,
            "diff": diff,
            "global_change": 0.0,
            "sector_scores": [0.0] * self.cfg.sector_count,
            "max_sector_idx": -1,
            "max_sector_score": 0.0,
            "abnormal_run_s": self.abnormal_run_s,
            "history_ready": False,
        }


class Calibrator:
    """鼠标点击两次：第 1 次中心，第 2 次晶圆边缘。"""

    def __init__(self, frame: np.ndarray):
        self.frame = frame
        self.points: List[Tuple[int, int]] = []
        self.window = "Wafer calibration"

    def _mouse(self, event, x, y, flags, param):
        if event == cv2.EVENT_LBUTTONDOWN:
            if len(self.points) >= 2:
                self.points.clear()
            self.points.append((x, y))

    def run(self) -> Calibration:
        cv2.namedWindow(self.window, cv2.WINDOW_NORMAL)
        cv2.setMouseCallback(self.window, self._mouse)

        while True:
            canvas = self.frame.copy()
            cv2.putText(canvas, "Click 1: wafer center", (20, 32), cv2.FONT_HERSHEY_SIMPLEX, 0.8, (0, 255, 255), 2)
            cv2.putText(canvas, "Click 2: wafer edge", (20, 64), cv2.FONT_HERSHEY_SIMPLEX, 0.8, (0, 255, 255), 2)
            cv2.putText(canvas, "ENTER: confirm   R: reset   ESC: cancel", (20, 96), cv2.FONT_HERSHEY_SIMPLEX, 0.65, (255, 255, 255), 2)

            if len(self.points) >= 1:
                cv2.drawMarker(canvas, self.points[0], (0, 255, 255), cv2.MARKER_CROSS, 24, 2)
            if len(self.points) >= 2:
                c = self.points[0]
                e = self.points[1]
                radius = int(round(math.hypot(e[0] - c[0], e[1] - c[1])))
                cv2.circle(canvas, c, radius, (0, 255, 0), 2)
                cv2.line(canvas, c, e, (0, 255, 255), 2)
                cv2.putText(canvas, f"R={radius}px", (20, 128), cv2.FONT_HERSHEY_SIMPLEX, 0.75, (0, 255, 0), 2)

            cv2.imshow(self.window, canvas)
            key = cv2.waitKey(30) & 0xFF
            if key in (13, 10) and len(self.points) == 2:  # Enter
                c, e = self.points
                radius = math.hypot(e[0] - c[0], e[1] - c[1])
                cv2.destroyWindow(self.window)
                if radius < 20:
                    raise RuntimeError("标定半径太小，请重新运行。")
                return Calibration(float(c[0]), float(c[1]), float(radius))
            if key in (ord('r'), ord('R')):
                self.points.clear()
            if key == 27:
                cv2.destroyWindow(self.window)
                raise KeyboardInterrupt("用户取消标定")


def draw_ring_and_sectors(frame: np.ndarray, det: WaterDetector, result: dict) -> np.ndarray:
    out = frame.copy()
    cfg = det.cfg
    c = (int(round(det.calib.cx)), int(round(det.calib.cy)))
    r_in = int(round(cfg.inner_radius_ratio * det.calib.radius))
    r_out = int(round(cfg.outer_radius_ratio * det.calib.radius))

    # 持续扰动区域：红色半透明叠加
    persistent = result.get("persistent")
    if persistent is not None:
        red = np.zeros_like(out)
        red[..., 2] = 255
        mask_u8 = persistent.astype(np.uint8) * 255
        blend = cv2.addWeighted(out, 0.55, red, 0.45, 0)
        out[mask_u8 > 0] = blend[mask_u8 > 0]

    cv2.circle(out, c, r_in, (255, 180, 0), 2)
    cv2.circle(out, c, r_out, (0, 255, 0), 2)

    n = cfg.sector_count
    sector_scores: Sequence[float] = result.get("sector_scores", [])
    for k in range(n):
        a = 2.0 * math.pi * k / n
        p1 = (int(round(c[0] + r_in * math.cos(a))), int(round(c[1] + r_in * math.sin(a))))
        p2 = (int(round(c[0] + r_out * math.cos(a))), int(round(c[1] + r_out * math.sin(a))))
        cv2.line(out, p1, p2, (100, 100, 100), 1, cv2.LINE_AA)

    # 高风险扇区外弧标红
    for k, score in enumerate(sector_scores):
        if score >= cfg.sector_threshold:
            start_deg = 360.0 * k / n
            end_deg = 360.0 * (k + 1) / n
            cv2.ellipse(out, c, (r_out, r_out), 0, start_deg, end_deg, (0, 0, 255), 5, cv2.LINE_AA)

    return out


def draw_panel(frame: np.ndarray, det: WaterDetector, result: dict, timestamp_s: float) -> np.ndarray:
    h, w = frame.shape[:2]
    pw = det.cfg.panel_width
    canvas = np.zeros((h, w + pw, 3), dtype=np.uint8)
    canvas[:, :w] = frame
    panel = canvas[:, w:]

    status = result["status"]
    if status == "NG":
        status_color = (0, 0, 255)
    elif status == "OK":
        status_color = (0, 210, 0)
    else:
        status_color = (0, 220, 255)

    x0 = 18
    y = 35
    cv2.putText(panel, "Wafer Water Demo", (x0, y), cv2.FONT_HERSHEY_SIMPLEX, 0.8, (230, 230, 230), 2)
    y += 48
    cv2.putText(panel, status, (x0, y), cv2.FONT_HERSHEY_SIMPLEX, 1.25, status_color, 3)
    y += 44

    cv2.putText(panel, f"time: {timestamp_s:6.2f} s", (x0, y), cv2.FONT_HERSHEY_SIMPLEX, 0.60, (220, 220, 220), 1)
    y += 28
    cv2.putText(panel, f"S_water: {result['s_water']:.4f}", (x0, y), cv2.FONT_HERSHEY_SIMPLEX, 0.68, (255, 255, 255), 2)
    y += 28
    cv2.putText(panel, f"threshold: {det.cfg.s_water_threshold:.4f}", (x0, y), cv2.FONT_HERSHEY_SIMPLEX, 0.58, (180, 180, 180), 1)
    y += 28
    cv2.putText(panel, f"abnormal run: {result['abnormal_run_s']:.2f} s", (x0, y), cv2.FONT_HERSHEY_SIMPLEX, 0.58, (220, 220, 220), 1)
    y += 32

    idx = result.get("max_sector_idx", -1)
    score = result.get("max_sector_score", 0.0)
    if idx >= 0:
        a0 = 360.0 * idx / det.cfg.sector_count
        a1 = 360.0 * (idx + 1) / det.cfg.sector_count
        cv2.putText(panel, f"max sector: {a0:.0f}-{a1:.0f} deg", (x0, y), cv2.FONT_HERSHEY_SIMPLEX, 0.56, (220, 220, 220), 1)
        y += 26
        cv2.putText(panel, f"sector score: {score:.3f}", (x0, y), cv2.FONT_HERSHEY_SIMPLEX, 0.56, (220, 220, 220), 1)
    else:
        cv2.putText(panel, "max sector: --", (x0, y), cv2.FONT_HERSHEY_SIMPLEX, 0.56, (220, 220, 220), 1)
        y += 26
        cv2.putText(panel, "sector score: --", (x0, y), cv2.FONT_HERSHEY_SIMPLEX, 0.56, (220, 220, 220), 1)

    # 右侧下半部分：最近若干秒 S_water 曲线
    gh = min(180, h - (y + 80))
    if gh >= 80:
        gx0, gy0 = 20, h - gh - 25
        gx1, gy1 = pw - 20, h - 25
        cv2.rectangle(panel, (gx0, gy0), (gx1, gy1), (80, 80, 80), 1)
        cv2.putText(panel, "S_water history", (gx0, gy0 - 8), cv2.FONT_HERSHEY_SIMPLEX, 0.52, (200, 200, 200), 1)

        scores = list(det.score_history)
        ymax = max(det.cfg.s_water_threshold * 3.0, 0.05, max(scores) * 1.15 if scores else 0.05)

        # 阈值线
        th_y = int(round(gy1 - (det.cfg.s_water_threshold / ymax) * (gy1 - gy0)))
        cv2.line(panel, (gx0, th_y), (gx1, th_y), (0, 150, 255), 1)

        if len(scores) >= 2:
            pts = []
            for i, s in enumerate(scores):
                px = gx0 + int(round(i * (gx1 - gx0) / max(1, len(scores) - 1)))
                py = gy1 - int(round(min(s, ymax) / ymax * (gy1 - gy0)))
                pts.append((px, py))
            cv2.polylines(panel, [np.array(pts, np.int32)], False, (0, 255, 255), 2, cv2.LINE_AA)

        cv2.putText(panel, f"0", (gx0, gy1 + 18), cv2.FONT_HERSHEY_SIMPLEX, 0.42, (150, 150, 150), 1)
        cv2.putText(panel, f"{ymax:.3f}", (gx0, gy0 + 14), cv2.FONT_HERSHEY_SIMPLEX, 0.42, (150, 150, 150), 1)

    return canvas


def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description="晶圆洗片积水检测 Demo")
    p.add_argument("--video", required=True, help="输入视频路径，或摄像头编号如 0")
    p.add_argument("--cx", type=float, default=None, help="晶圆中心 x")
    p.add_argument("--cy", type=float, default=None, help="晶圆中心 y")
    p.add_argument("--r", type=float, default=None, help="晶圆半径")

    p.add_argument("--output-mp4", default=None, help="输出带检测结果的视频")
    p.add_argument("--output-csv", default=None, help="输出逐帧检测结果 CSV")
    p.add_argument("--no-display", action="store_true", help="不显示 GUI；此模式必须指定 --cx --cy --r")

    p.add_argument("--inner", type=float, default=0.65, help="环形 ROI 内半径比例")
    p.add_argument("--outer", type=float, default=0.95, help="环形 ROI 外半径比例")
    p.add_argument("--diff-th", type=float, default=8.0, help="灰度帧差阈值")
    p.add_argument("--history-sec", type=float, default=0.50, help="驻留统计窗口，秒")
    p.add_argument("--persist-th", type=float, default=0.50, help="像素驻留比例阈值")
    p.add_argument("--s-water-th", type=float, default=0.010, help="整体 S_water NG 阈值")
    p.add_argument("--ng-hold-sec", type=float, default=0.50, help="持续异常多久进入 NG")
    p.add_argument("--recover-sec", type=float, default=0.30, help="持续恢复多久回到 OK")
    p.add_argument("--sectors", type=int, default=24, help="圆周扇区数量")
    p.add_argument("--sector-th", type=float, default=0.05, help="单扇区异常显示阈值")
    p.add_argument("--blur", type=int, default=3, help="高斯模糊核，0/1 表示关闭")
    return p.parse_args()


def open_source(video_arg: str):
    if video_arg.isdigit() and not os.path.exists(video_arg):
        source = int(video_arg)
    else:
        source = video_arg
    cap = cv2.VideoCapture(source)
    if not cap.isOpened():
        raise RuntimeError(f"无法打开输入：{video_arg}")
    return cap


def main() -> int:
    args = parse_args()

    cfg = DemoConfig(
        inner_radius_ratio=args.inner,
        outer_radius_ratio=args.outer,
        diff_threshold=args.diff_th,
        history_seconds=args.history_sec,
        persistence_threshold=args.persist_th,
        s_water_threshold=args.s_water_th,
        ng_hold_seconds=args.ng_hold_sec,
        recover_hold_seconds=args.recover_sec,
        sector_count=args.sectors,
        sector_threshold=args.sector_th,
        blur_ksize=args.blur,
    )

    cap = open_source(args.video)
    fps = float(cap.get(cv2.CAP_PROP_FPS))
    if fps <= 1e-3:
        fps = 30.0

    ok, first = cap.read()
    if not ok:
        cap.release()
        raise RuntimeError("无法读取第一帧")

    h, w = first.shape[:2]

    supplied = args.cx is not None and args.cy is not None and args.r is not None
    if supplied:
        calib = Calibration(args.cx, args.cy, args.r)
    else:
        if args.no_display:
            cap.release()
            raise RuntimeError("--no-display 模式必须指定 --cx --cy --r")
        calib = Calibrator(first).run()

    print(f"Calibration: cx={calib.cx:.1f}, cy={calib.cy:.1f}, r={calib.radius:.1f}")
    print(f"FPS={fps:.2f}, frame={w}x{h}")
    print(f"ROI={cfg.inner_radius_ratio:.2f}R~{cfg.outer_radius_ratio:.2f}R, S_water threshold={cfg.s_water_threshold:.4f}")

    detector = WaterDetector((h, w), fps, calib, cfg)

    # 重新从第一帧开始
    cap.set(cv2.CAP_PROP_POS_FRAMES, 0)

    input_stem = Path(str(args.video)).stem if not str(args.video).isdigit() else "camera"
    output_mp4 = args.output_mp4 or f"{input_stem}_detected.mp4"
    output_csv = args.output_csv or f"{input_stem}_detected.csv"

    out_mp4_path = Path(output_mp4)
    out_csv_path = Path(output_csv)
    out_mp4_path.parent.mkdir(parents=True, exist_ok=True)
    out_csv_path.parent.mkdir(parents=True, exist_ok=True)

    fourcc = cv2.VideoWriter_fourcc(*"mp4v")
    writer = cv2.VideoWriter(str(out_mp4_path), fourcc, fps, (w + cfg.panel_width, h))
    if not writer.isOpened():
        cap.release()
        raise RuntimeError(f"无法创建输出视频：{out_mp4_path}")

    csv_file = open(out_csv_path, "w", newline="", encoding="utf-8-sig")
    csv_writer = csv.writer(csv_file)
    csv_writer.writerow([
        "frame", "time_s", "status", "s_water", "abnormal_run_s",
        "max_sector_index", "max_sector_start_deg", "max_sector_end_deg", "max_sector_score"
    ])

    frame_idx = 0
    try:
        while True:
            ok, frame = cap.read()
            if not ok:
                break

            result = detector.process(frame)
            timestamp_s = frame_idx / fps

            vis = draw_ring_and_sectors(frame, detector, result)
            panel_img = draw_panel(vis, detector, result, timestamp_s)
            writer.write(panel_img)

            idx = result.get("max_sector_idx", -1)
            if idx >= 0:
                a0 = 360.0 * idx / cfg.sector_count
                a1 = 360.0 * (idx + 1) / cfg.sector_count
            else:
                a0 = a1 = ""

            csv_writer.writerow([
                frame_idx,
                f"{timestamp_s:.6f}",
                result["status"],
                f"{result['s_water']:.6f}",
                f"{result['abnormal_run_s']:.6f}",
                idx,
                a0,
                a1,
                f"{result.get('max_sector_score', 0.0):.6f}",
            ])

            if not args.no_display:
                cv2.imshow("Wafer Water Detection Demo", panel_img)
                key = cv2.waitKey(1) & 0xFF
                if key in (27, ord('q'), ord('Q')):
                    break

            frame_idx += 1

    finally:
        csv_file.close()
        writer.release()
        cap.release()
        cv2.destroyAllWindows()

    print(f"Done. Frames={frame_idx}")
    print(f"Video: {out_mp4_path.resolve()}")
    print(f"CSV:   {out_csv_path.resolve()}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
