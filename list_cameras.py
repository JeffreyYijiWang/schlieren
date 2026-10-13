"""List connected cameras without opening streams: python list_cameras.py."""

import argparse
import json

from schlieren.cameras import BACKENDS, default_backend, list_cameras, print_cameras
from schlieren.capture_modes import discover_modes


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--backend", choices=BACKENDS, default=default_backend())
    parser.add_argument("--json", action="store_true", help="Print a machine-readable camera list")
    parser.add_argument("--modes", type=int, metavar="CAMERA", help="List driver resolution/FPS modes for this camera number (Windows + FFmpeg)")
    args = parser.parse_args()
    cameras = list_cameras(args.backend)
    if args.modes is not None:
        if not 1 <= args.modes <= len(cameras):
            parser.error("Choose a camera number from the current list.")
        camera = cameras[args.modes - 1]
        duplicate = sum(c.name == camera.name for c in cameras[:args.modes - 1])
        result = discover_modes(camera, duplicate)
        if args.json:
            print(json.dumps({"camera": args.modes, "name": camera.name, **result}, indent=2))
        else:
            print(f"Camera {args.modes}: {camera.name}")
            for mode in result["modes"]:
                suffix = "" if mode["selectable"] else " (not selectable in this viewer)"
                print(f"  {mode['width']} x {mode['height']}  up to {mode['fps']:g} FPS  {mode['format']}{suffix}")
            print(result["note"])
        return
    if args.json:
        print(json.dumps([{"camera": n, "name": c.name, "opencv_index": c.index,
                           "backend": args.backend} for n, c in enumerate(cameras, 1)], indent=2))
    else:
        print_cameras(cameras, args.backend)


if __name__ == "__main__":
    main()
