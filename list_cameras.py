"""List connected cameras without opening streams: python list_cameras.py."""

import argparse
import json

from schlieren.cameras import BACKENDS, default_backend, list_cameras, print_cameras


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--backend", choices=BACKENDS, default=default_backend())
    parser.add_argument("--json", action="store_true", help="Print a machine-readable camera list")
    args = parser.parse_args()
    cameras = list_cameras(args.backend)
    if args.json:
        print(json.dumps([{"camera": n, "name": c.name, "opencv_index": c.index,
                           "backend": args.backend} for n, c in enumerate(cameras, 1)], indent=2))
    else:
        print_cameras(cameras, args.backend)


if __name__ == "__main__":
    main()
