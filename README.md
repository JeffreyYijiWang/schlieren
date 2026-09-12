# Live schlieren background subtraction

A local Python/OpenCV app for an **external USB webcam**, with named camera selection,
a captured background, amplified intensity differences, and reference/MOG/MOG2/GMG
foreground masks. No video upload is needed.

## p5.js full-window camera lab

After installing the Python environment below, start the browser demo:

```powershell
.\start-demo.ps1
```

Open **[http://127.0.0.1:8765](http://127.0.0.1:8765)** in Chrome or Edge. Keep the
server terminal open. The demo starts with synthetic input; choose **Camera 1, 2, 3, …**
from the source list to use an attached device, including the DFK 33UP1300 and BRIO 4K
Stream Edition when connected. The number-to-name mapping is displayed in the sidebar.
Click **Refresh** after connecting or disconnecting a camera. Selection opens one camera
at a time and releases the previous one; the integrated camera is used only if selected.

The p5.js canvas fills the available browser area. Choose **Compare all four** to see
input, reference, amplified difference, and foreground mask together, or select one view
to enlarge it. **Fullscreen** fills the monitor; **H** hides the controls for more room.

| Browser control | Action |
| --- | --- |
| 1–9 / numbered source buttons | Switch to that camera |
| Space / Retrain background | Capture a new background, then freeze it |
| Reference / MOG / MOG2 / GMG buttons | Choose a model; press Space to train it |
| M | Cycle background models |
| V | Cycle comparison and single-image views |
| F | Enter/exit fullscreen (Esc also exits fullscreen) |
| H | Hide/show controls |
| S / Snapshot | Download a PNG of the displayed canvas |
| Enable learning / Freeze model | Toggle adaptive learning after statistical-model training |
| Release camera | Stop capture and release the device |

Space retrains even when a slider or button has keyboard focus. Hold the scene still
for 30 frames (GMG: at least 121). Changing cameras or models discards the old background;
images are marked as untrained until Space is pressed and training completes. A failed
camera shows an error; select another camera or Synthetic demo to recover.

This is a **p5.js frontend with a local Python/OpenCV processing server**, so all four
models use the same implementation as the desktop app. It is not a standalone p5 Web
Editor sketch. The vendored p5.js library works offline; no frames are uploaded to an
external service. The browser receives JPEG previews; use the desktop snapshot bundle
for the full-precision reference. All tabs on this server share one active camera/model.
Closing a browser tab does not stop the server: use Release camera or Ctrl+C in its terminal.

For a different port, capture size, or Windows backend:

```powershell
.\start-demo.ps1 --port 8766 --width 640 --height 480
.\start-demo.ps1 --backend msmf
# Direct Python equivalent, also usable on macOS/Linux with the appropriate Python path:
.\.venv\Scripts\python.exe serve_demo.py
```

## List connected cameras

```powershell
.\list-cameras.ps1
.\list-cameras.ps1 --backend msmf
# Python version (does not open camera streams):
.\.venv\Scripts\python.exe list_cameras.py
.\.venv\Scripts\python.exe list_cameras.py --json
```

Camera numbers start at **1** and follow the current device list; they are not permanent
hardware IDs. Both scripts and the p5.js source list show the actual camera names.
For example, a list could be Camera 1 = Integrated Webcam, Camera 2 = DFK 33UP1300,
Camera 3 = BRIO 4K Stream Edition, Camera 4 = OBS Virtual Camera. Always use your current
list. Windows backends may enumerate in a different order.

## Start on Windows

Run these commands in this repository using PowerShell (Python 3.10 or newer):

```powershell
py -3.11 -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
.\.venv\Scripts\python.exe -m schlieren
```

The desktop app lists connected cameras and asks you to enter your **USB webcam's number**.
It opens only that selection, with no automatic fallback. User-facing camera numbers
are now 1-based, so `--camera 1` selects the first listed camera. Identify yours by name.

To list devices or select one directly:

```powershell
.\.venv\Scripts\python.exe -m schlieren --list-cameras
.\.venv\Scripts\python.exe -m schlieren --camera-name "USB"
# Or use the camera number shown in your list (2 is just an example):
.\.venv\Scripts\python.exe -m schlieren --camera 2
# Advanced: select a raw, zero-based OpenCV index explicitly:
.\.venv\Scripts\python.exe -m schlieren --camera-index 1
```

If multiple cameras match a name, use their camera number. Numbers can change when devices
are reconnected. On macOS/Linux use `python3 -m venv .venv`, install using
`.venv/bin/python -m pip install -r requirements.txt`, then run `.venv/bin/python -m schlieren`.
Device enumeration selects the platform's capture backend; Windows uses DirectShow.

## Capture your experiment

1. Connect the webcam and select it. Mount it firmly and align your schlieren optical
   setup, or focus on a textured background for qualitative background-oriented imaging.
2. Let exposure settle. Where your webcam software supports it, lock exposure, gain,
   white balance, and focus. Keep illumination steady. These driver-specific controls
   are configured in the camera's software, not automatically by this app.
3. With the flow/heat source absent or inactive, press **Space** (or **B**). Keep the scene quiet until
   the status says **Ready**. The reference is an average of 30 frames by default;
   GMG uses at least 121 frames to complete its initialization.
4. Introduce the disturbance. The bottom-left panel amplifies signed brightness changes:
   gray means unchanged, brighter means an increase, darker means a decrease.
   Start with **8x gain**, adjusting the Gain slider as needed. The bottom-right panel
   shows the selected foreground mask.
5. Press **Space** to recapture if the camera, optics, background, or illumination moves.

This is **qualitative intensity-change visualization**, not a density, temperature,
or refractive-index measurement. Background subtraction also responds to camera shake,
moving objects, sensor noise, and lighting changes. A webcam alone does not supply a
schlieren optical setup. Quantitative background-oriented schlieren requires additional
displacement estimation and calibration, which this app does not implement.

## Controls

Click the OpenCV window before using keyboard shortcuts; use lowercase letters.

| Control | Action |
| --- | --- |
| Space / B | Capture/re-capture a quiet background and freeze it |
| 1–9 | Switch to a numbered camera; capture its background with Space |
| M | Cycle Reference / MOG / MOG2 / GMG; press Space after changing mode |
| Gain slider | Amplification of the difference panel (1–64x) |
| Reference threshold slider | Binary reference mask threshold, in original 8-bit intensity units |
| F | Toggle frozen/adaptive MOG, MOG2, or GMG after calibration |
| D | Toggle signed and absolute difference display |
| C | Toggle 3×3 morphological opening of the mask |
| L | Toggle compensation for global median brightness drift |
| S | Save a snapshot bundle in `captures/` |
| R | Start/stop dashboard recording in `captures/` |
| Q or Esc | Quit and release the webcam |

The difference panel always uses the captured, averaged reference. Its gain does not
change the mask threshold. The default **reference** mask thresholds the magnitude of
the intensity change, retaining both brightening and darkening.

MOG, MOG2, and GMG produce statistical foreground masks. They train during background
capture, suppress initialization masks, and freeze afterward so a persistent disturbance
isn't gradually absorbed. Press F if you want continued learning; `--learning-rate`
controls that rate. F does not modify the fixed reference or reference mode.

MOG2 uses `--mog2-threshold` for its variance threshold. `--shadows` enables gray shadow
labels (127); shadows are disabled by default. The reference threshold slider does not
tune statistical modes. GMG's internal smoothing is disabled; use C or `--cleanup` to
enable mask cleanup. Cleanup can remove faint, small structures, so it starts off.

Light compensation subtracts the median brightness difference across the whole image.
It starts off because it can also remove a real signal that fills much of the image.
Gaussian blur defaults to 3×3; use `--blur 1` to preserve maximum spatial detail.

## Other runs

```powershell
# MOG2 on the selected webcam:
.\.venv\Scripts\python.exe -m schlieren --camera-name "USB" --mode mog2

# GMG with its initialization phase:
.\.venv\Scripts\python.exe -m schlieren --camera-name "USB" --mode gmg

# Request 1920x1080 MJPEG transport (actual settings depend on the webcam):
.\.venv\Scripts\python.exe -m schlieren --camera-name "USB" --width 1920 --height 1080 --fps 30 --mjpg

# Demo with synthetic intensity changes; no camera needed:
.\.venv\Scripts\python.exe -m schlieren --demo --width 640 --height 360

# Local video: initial frames must contain a quiet reference scene:
.\.venv\Scripts\python.exe -m schlieren --input experiment.mp4 --auto-reference

# Reproducible headless demo, including a recording and final snapshot:
.\.venv\Scripts\python.exe -m schlieren --demo --headless --width 640 --height 360 --max-frames 180 --record --save-last
```

`--auto-reference` begins training immediately; use it only when the input is already
stable. Normal live use waits for Space or B. The synthetic demo automatically calibrates and
then introduces a faint wavy intensity perturbation; it is not a physical flow simulation.

Snapshots include the original frame, 8-bit reference preview, enhanced difference,
mask, dashboard, full-precision averaged reference (`reference_float32.npy`), and JSON
settings. The JSON `ready` field is false if saved before calibration completed. Model
internals are not saved. Recordings contain the annotated dashboard as MJPEG AVI at the
reported nominal camera/video FPS (or `--fps` if unavailable). If processing cannot keep
up, playback duration can differ from elapsed experiment time; the displayed processing
FPS helps identify this. Recordings are previews, not timestamped measurement data.

## Troubleshooting

- Close Camera, Zoom, Teams, OBS, or other software using the webcam. Check Windows
  **Settings → Privacy & security → Camera → Let desktop apps access your camera**.
- If DirectShow cannot open your webcam, list and select it again with `--backend msmf`.
  Indices may differ across backends.
- Lower `--width`, `--height`, or use `--mjpg` if capture is slow. The console prints
  the actual delivered dimensions; requested resolution/FPS may be ignored by a driver.
- If only the integrated camera appears, check the USB connection and webcam driver.
  The app does not guess or switch devices if your selection fails.
- Install only `opencv-contrib-python` in this environment. The contrib build includes
  MOG and GMG. Headless OpenCV packages cannot display the desktop window; other OpenCV
  packages share the same `cv2` namespace and should not be installed alongside it.
- For certificate errors during installation on Windows, retry using the system trust store:
  `.\.venv\Scripts\python.exe -m pip install --use-feature=truststore -r requirements.txt`.
- Run this locally. The `cv2.VideoCapture` code in a hosted Colab runtime cannot directly
  open a USB camera attached to your own computer.

## Validation and references

```powershell
.\.venv\Scripts\python.exe -m unittest discover -s tests -v
```

Tests use synthetic images/video to check calibration, positive and negative differences,
frozen and adaptive models, GMG initialization, camera selection, and recorded-file output.
They do not establish optical sensitivity or replace validation on your USB webcam.

Inspired by the captured-background/reset interaction in
[Kyle McDonald's BackgroundSubtraction example](https://github.com/kylemcdonald/cv-examples/tree/master/BackgroundSubtraction)
and the supplied Python MOG/MOG2/GMG snippets. This is an independent Python implementation.
See OpenCV's [background-subtraction tutorial](https://docs.opencv.org/4.x/d1/dc5/tutorial_background_subtraction.html)
and [learning-rate interface](https://docs.opencv.org/4.x/d7/df6/classcv_1_1BackgroundSubtractor.html).
Named selection uses [cv2-enumerate-cameras](https://pypi.org/project/cv2-enumerate-cameras/).
