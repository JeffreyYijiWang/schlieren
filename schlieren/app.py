"""Desktop OpenCV viewer and command-line entry point."""

import argparse
from dataclasses import asdict
from datetime import datetime
import json
from pathlib import Path
import sys
import time

import cv2
import numpy as np

from .cameras import BACKENDS, default_backend, list_cameras, open_camera, print_cameras, select_camera
from .demo import DemoCamera
from .processing import MODES, Processor, Settings

WINDOW = "Schlieren | live background subtraction"


def parse_args(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    sources = parser.add_mutually_exclusive_group()
    sources.add_argument("--camera", type=int, help="Camera number from --list-cameras, starting at 1")
    sources.add_argument("--camera-index", type=int, help="Raw OpenCV index, starting at 0")
    sources.add_argument("--camera-name", help="Unique part of your USB webcam's name")
    sources.add_argument("--input", type=Path, help="Process a local video instead of a camera")
    sources.add_argument("--demo", action="store_true", help="Synthetic input; no camera needed")
    parser.add_argument("--list-cameras", action="store_true")
    parser.add_argument("--backend", choices=BACKENDS, default=default_backend())
    parser.add_argument("--mode", choices=MODES, default="reference")
    parser.add_argument("--width", type=int, default=1280)
    parser.add_argument("--height", type=int, default=720)
    parser.add_argument("--fps", type=float, default=30)
    parser.add_argument("--mjpg", action="store_true", help="Request MJPEG camera transport")
    parser.add_argument("--reference-frames", type=int, default=30)
    parser.add_argument("--gmg-frames", type=int, default=120)
    parser.add_argument("--gain", type=int, default=8, help="Difference display gain, 1..64")
    parser.add_argument("--threshold", type=int, default=3, help="Reference mask threshold, 0..255")
    parser.add_argument("--blur", type=int, default=3, help="Odd Gaussian kernel size; 1 disables")
    parser.add_argument("--cleanup", action="store_true", help="3x3 morphological opening on mask")
    parser.add_argument("--absolute", action="store_true", help="Show absolute instead of signed difference")
    parser.add_argument("--compensate-light", action="store_true", help="Subtract the global median intensity drift")
    parser.add_argument("--learning-rate", type=float, default=0.001)
    parser.add_argument("--mog2-threshold", type=float, default=16)
    parser.add_argument("--shadows", action="store_true", help="MOG2 shadow detection; shadows are gray")
    parser.add_argument("--auto-reference", action="store_true", help="Calibrate immediately; scene must already be quiet")
    parser.add_argument("--record", action="store_true", help="Record the dashboard from startup")
    parser.add_argument("--output-dir", type=Path, default=Path("captures"))
    parser.add_argument("--headless", action="store_true", help="Process without a display")
    parser.add_argument("--max-frames", type=int, default=0, help="Stop after N frames; 0 is unlimited")
    parser.add_argument("--save-last", action="store_true", help="Save final frame, reference, and results")
    args = parser.parse_args(argv)
    if args.width < 1 or args.height < 1 or not np.isfinite(args.fps) or args.fps <= 0:
        parser.error("Width, height and fps must be positive and finite.")
    if args.max_frames < 0:
        parser.error("--max-frames must be nonnegative.")
    if args.headless and not args.list_cameras:
        if not args.auto_reference and not args.demo:
            parser.error("--headless requires --auto-reference (or --demo).")
        if args.input is None and args.max_frames == 0:
            parser.error("Use --max-frames to bound a headless camera/demo run.")
    return args


def text(image, label, x, y, color=(220, 226, 232), scale=0.5):
    label = label.encode("ascii", errors="replace").decode("ascii")
    cv2.putText(image, label, (x, y), cv2.FONT_HERSHEY_SIMPLEX, scale, color, 1, cv2.LINE_AA)


def panel(image, label):
    tile = np.full((343, 560, 3), (20, 23, 28), np.uint8)
    text(tile, label, 14, 20, (220, 226, 232))
    image = np.clip(image, 0, 255).astype(np.uint8)
    if image.ndim == 2:
        image = cv2.cvtColor(image, cv2.COLOR_GRAY2BGR)
    h, w = image.shape[:2]
    scale = min(560 / w, 315 / h)
    resized = cv2.resize(image, (max(1, round(w * scale)), max(1, round(h * scale))),
                         interpolation=cv2.INTER_AREA if scale < 1 else cv2.INTER_LINEAR)
    rh, rw = resized.shape[:2]
    x, y = (560 - rw) // 2, 28 + (315 - rh) // 2
    tile[y:y + rh, x:x + rw] = resized
    return tile


def dashboard(frame, result, processor, source, fps, recording=False):
    s = processor.settings
    canvas = np.full((860, 1120, 3), (14, 17, 21), np.uint8)
    text(canvas, "SCHLIEREN  /  BACKGROUND SUBTRACTION", 16, 25, (215, 222, 230), 0.65)
    text(canvas, source[:110], 16, 47, (150, 169, 180))
    reference_label = "02  CAPTURED BACKGROUND" if result.ready else "02  BACKGROUND / CALIBRATION"
    difference_label = "SIGNED DIFFERENCE / gray = no change" if s.signed else "ABSOLUTE DIFFERENCE"
    images = [
        panel(frame, "01  LIVE INPUT"),
        panel(result.reference, reference_label),
        panel(result.enhanced, f"03  {difference_label} / {s.gain}x"),
        panel(result.mask, f"04  {s.mode.upper()} FOREGROUND MASK"),
    ]
    canvas[58:401] = np.hstack(images[:2])
    canvas[401:744] = np.hstack(images[2:])
    color = (115, 221, 158) if result.ready else (100, 198, 248)
    text(canvas, processor.status, 16, 768, color, 0.57)
    text(canvas, f"{fps:.1f} processed fps | Threshold {s.threshold} (reference mask) | "
         f"Cleanup {'on' if s.cleanup else 'off'} | Light correction {'on' if s.compensate_light else 'off'}",
         16, 791, (170, 182, 195))
    text(canvas, "Space / B retrain | 1-9 switch camera | M next mode | F freeze/adapt | D signed/absolute", 16, 815)
    text(canvas, "C cleanup | L light correction | S snapshot | R record | Q / Esc quit", 16, 838)
    if recording:
        text(canvas, "REC", 1060, 25, (80, 80, 255), 0.65)
    return canvas


def unique_path(output_dir, prefix, suffix=""):
    output_dir.mkdir(parents=True, exist_ok=True)
    return output_dir / f"{prefix}_{datetime.now():%Y%m%d_%H%M%S_%f}{suffix}"


def save_snapshot(output_dir, frame, result, view, processor, source):
    folder = unique_path(output_dir, "snapshot")
    folder.mkdir()
    images = {"input": frame, "reference": np.clip(result.reference, 0, 255).astype(np.uint8),
              "difference": result.enhanced, "mask": result.mask, "dashboard": view}
    for name, image in images.items():
        if not cv2.imwrite(str(folder / f"{name}.png"), image):
            raise RuntimeError(f"Could not save {name}.png to {folder}")
    np.save(folder / "reference_float32.npy", result.reference)
    metadata = {"source": source, "settings": asdict(processor.settings), "ready": result.ready,
                "adaptive": processor.adaptive, "calibration_frames": processor.count,
                "frame_shape": list(frame.shape), "captured_at": datetime.now().isoformat()}
    (folder / "settings.json").write_text(json.dumps(metadata, indent=2), encoding="utf-8")
    print(f"Snapshot saved: {folder.resolve()}")
    return folder


def start_recording(output_dir, view, fps):
    path = unique_path(output_dir, "recording", ".avi")
    h, w = view.shape[:2]
    writer = cv2.VideoWriter(str(path), cv2.VideoWriter_fourcc(*"MJPG"), fps, (w, h))
    if not writer.isOpened():
        writer.release()
        raise RuntimeError(f"Cannot open MJPEG recording: {path}")
    print(f"Recording dashboard at {fps:g} playback fps: {path.resolve()}")
    return writer


def run(args):
    if args.list_cameras:
        print_cameras(list_cameras(args.backend), args.backend)
        return 0
    settings = Settings(
        mode=args.mode, reference_frames=args.reference_frames, gmg_frames=args.gmg_frames,
        gain=args.gain, threshold=args.threshold, blur=args.blur, cleanup=args.cleanup,
        signed=not args.absolute, compensate_light=args.compensate_light,
        learning_rate=args.learning_rate, mog2_threshold=args.mog2_threshold, shadows=args.shadows,
    )
    processor = Processor(settings)
    cap, writer = None, None
    have_window = False
    frame_count = 0
    last = None
    try:
        record_fps = args.fps
        if args.demo:
            cap = DemoCamera(args.width, args.height, processor.training_frames + 10)
            source = "Synthetic demo - intensity changes, not a physical flow simulation"
        elif args.input is not None:
            if not args.input.is_file():
                raise RuntimeError(f"Video does not exist: {args.input}")
            cap = cv2.VideoCapture(str(args.input))
            if not cap.isOpened():
                raise RuntimeError(f"Cannot open video: {args.input}")
            source = f"Video: {args.input.name}"
            actual_fps = cap.get(cv2.CAP_PROP_FPS)
            if np.isfinite(actual_fps) and actual_fps > 0:
                record_fps = actual_fps
        else:
            cameras = list_cameras(args.backend)
            print_cameras(cameras, args.backend)
            camera = select_camera(cameras, args.camera_index, args.camera_name, args.camera)
            cap = open_camera(camera, args.width, args.height, args.fps, args.mjpg)
            source = f"Camera: {camera.name} | index {camera.index} | {args.backend}"
            actual_fps = cap.get(cv2.CAP_PROP_FPS)
            if np.isfinite(actual_fps) and actual_fps > 0:
                record_fps = actual_fps
        print(source)
        if args.demo or args.auto_reference:
            processor.reset(capture=True)
        if not args.headless:
            cv2.namedWindow(WINDOW, cv2.WINDOW_NORMAL)
            have_window = True
            cv2.resizeWindow(WINDOW, 1120, 920)
            cv2.createTrackbar("Gain", WINDOW, settings.gain, 64, lambda _: None)
            cv2.createTrackbar("Reference threshold", WINDOW, settings.threshold, 255, lambda _: None)
        print("Space / B: retrain. 1-9: camera. M: next mode. Q: quit.")
        recording = args.record
        previous = time.perf_counter()
        measured_fps = 0.0
        while True:
            loop_start = time.perf_counter()
            ok, frame = cap.read()
            if not ok or frame is None or frame.size == 0:
                if args.input is not None and frame_count > 0:
                    print("End of video.")
                    break
                raise RuntimeError("Camera stopped delivering frames. Check the USB connection and close other camera apps.")
            if frame_count == 0:
                print(f"Input: {frame.shape[1]}x{frame.shape[0]}, nominal {record_fps:g} fps")
            if not args.headless:
                settings.gain = max(1, cv2.getTrackbarPos("Gain", WINDOW))
                settings.threshold = cv2.getTrackbarPos("Reference threshold", WINDOW)
            result = processor.process(frame)
            now = time.perf_counter()
            current_fps = 1 / max(now - previous, 1e-6)
            measured_fps = current_fps if frame_count == 0 else 0.9 * measured_fps + 0.1 * current_fps
            previous = now
            view = dashboard(frame, result, processor, source, measured_fps, recording)
            if recording:
                if writer is None:
                    writer = start_recording(args.output_dir, view, record_fps)
                writer.write(view)
            last = (frame, result, view)
            frame_count += 1
            if args.max_frames and frame_count >= args.max_frames:
                break
            if args.headless:
                continue
            cv2.imshow(WINDOW, view)
            # A real camera paces capture itself; files and demo need playback pacing.
            delay = max(1, round((1 / record_fps - (time.perf_counter() - loop_start)) * 1000)) \
                if args.demo or args.input is not None else 1
            key = cv2.waitKey(delay) & 0xFF
            if key in (27, ord("q")) or cv2.getWindowProperty(WINDOW, cv2.WND_PROP_VISIBLE) < 1:
                break
            if key in (ord("b"), ord(" ")):
                processor.reset(capture=True)
                if args.demo:
                    cap.count = 0
                    cap.quiet_frames = processor.training_frames + 10
            elif key == ord("m"):
                settings.mode = MODES[(MODES.index(settings.mode) + 1) % len(MODES)]
                processor.reset()
                print(f"Mode: {settings.mode}. Press Space to capture a new background.")
            elif key in tuple(map(ord, "123456789")):
                choices = list_cameras(args.backend)
                try:
                    chosen = select_camera(choices, number=key - ord("0"))
                except RuntimeError as exc:
                    print(exc)
                    continue
                # Release before switching: many USB drivers allow only one stream.
                cap.release()
                if writer is not None:
                    writer.release()
                    writer = None
                recording = False
                processor.reset()
                cap = open_camera(chosen, args.width, args.height, args.fps, args.mjpg)
                source = f"Camera {key - ord('0')}: {chosen.name} | {args.backend}"
                args.demo, args.input = False, None
                actual_fps = cap.get(cv2.CAP_PROP_FPS)
                record_fps = actual_fps if np.isfinite(actual_fps) and actual_fps > 0 else args.fps
                print(f"{source}. Press Space to capture its background.")
            elif key == ord("f"):
                processor.toggle_learning()
            elif key == ord("d"):
                settings.signed = not settings.signed
            elif key == ord("c"):
                settings.cleanup = not settings.cleanup
            elif key == ord("l"):
                settings.compensate_light = not settings.compensate_light
            elif key == ord("s"):
                save_snapshot(args.output_dir, frame, result, view, processor, source)
            elif key == ord("r"):
                recording = not recording
                if not recording and writer is not None:
                    writer.release()
                    writer = None
                    print("Recording stopped.")
        if args.save_last and last is not None:
            save_snapshot(args.output_dir, *last, processor, source)
        print(f"Processed {frame_count} frames.")
        if not processor.ready:
            print("Background calibration was not completed. Capture a reference before interpreting masks.")
        return 0
    finally:
        if writer is not None:
            writer.release()
        if cap is not None:
            cap.release()
        if have_window:
            cv2.destroyAllWindows()


def main(argv=None):
    args = parse_args(argv)
    try:
        return run(args)
    except KeyboardInterrupt:
        return 0
    except (RuntimeError, ValueError, OSError, cv2.error) as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return 1
