"""Enumerate and open only the camera the user selects."""

import sys

import cv2
from cv2_enumerate_cameras import enumerate_cameras

BACKENDS = {
    "dshow": cv2.CAP_DSHOW,
    "msmf": cv2.CAP_MSMF,
    "v4l2": cv2.CAP_V4L2,
    "avfoundation": cv2.CAP_AVFOUNDATION,
}


def default_backend():
    if sys.platform == "win32":
        return "dshow"
    return "avfoundation" if sys.platform == "darwin" else "v4l2"


def list_cameras(backend):
    try:
        return list(enumerate_cameras(BACKENDS[backend]))
    except Exception as exc:
        raise RuntimeError(f"Cannot enumerate cameras with {backend}: {exc}") from exc


def print_cameras(cameras, backend):
    print(f"Cameras ({backend}):")
    for number, camera in enumerate(cameras, start=1):
        print(f"  Camera {number}: {camera.name}  (OpenCV index {camera.index})")
    if not cameras:
        print("  No cameras found. Connect the USB webcam and try again.")
    print("Use --camera NUMBER (1, 2, 3, ...). Identify your external camera by its name.")


def select_camera(cameras, index=None, name=None, number=None):
    if number is not None:
        if number < 1 or number > len(cameras):
            raise RuntimeError("Camera number is not in the list. Run list_cameras.py again.")
        return cameras[number - 1]
    if index is not None:
        matches = [camera for camera in cameras if camera.index == index]
    elif name is not None:
        matches = [camera for camera in cameras if name.casefold() in camera.name.casefold()]
    else:
        if not cameras:
            raise RuntimeError("No camera is available.")
        if not sys.stdin.isatty():
            raise RuntimeError("Select the external camera with --camera NUMBER or --camera-name NAME.")
        try:
            selected = int(input("Enter the camera number (1, 2, 3, ...): ").strip())
        except (ValueError, EOFError) as exc:
            raise RuntimeError("Enter a camera number from the list.") from exc
        return select_camera(cameras, number=selected)
    if not matches:
        raise RuntimeError("Selected camera was not found. Run --list-cameras again.")
    if len(matches) != 1:
        raise RuntimeError("Multiple cameras match that name. Use --camera with an exact number.")
    return matches[0]


def open_camera(camera, width, height, fps, mjpg=False):
    cap = cv2.VideoCapture(camera.index, camera.backend)
    if not cap.isOpened():
        cap.release()
        if sys.platform == "win32" and "windowsapps" in sys.base_prefix.lower():
            raise RuntimeError(
                f"Cannot open {camera.name} with Microsoft Store Python. "
                "Run start-demo.ps1 using the standard Python environment (.venv-camera)."
            )
        alternative = "msmf" if camera.backend == cv2.CAP_DSHOW else "dshow"
        backend_hint = f"or try --backend {alternative}." if sys.platform == "win32" else "and check the device driver."
        raise RuntimeError(
            f"Cannot open {camera.name}. Close other camera apps, check camera permissions, "
            + backend_hint
        )
    if mjpg:
        cap.set(cv2.CAP_PROP_FOURCC, cv2.VideoWriter_fourcc(*"MJPG"))
    cap.set(cv2.CAP_PROP_FRAME_WIDTH, width)
    cap.set(cv2.CAP_PROP_FRAME_HEIGHT, height)
    cap.set(cv2.CAP_PROP_FPS, fps)
    return cap
