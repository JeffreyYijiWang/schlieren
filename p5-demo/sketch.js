/* p5.js renders the live view; the local Python server runs the real OpenCV models. */
(() => {
  "use strict";
  const $ = (id) => document.getElementById(id);
  const modes = ["reference", "mog", "mog2", "gmg"];
  const views = ["compare", "difference", "mask", "raw", "reference"];
  let state = null, cameras = [], bitmap = null, bitmapGeneration = -1;
  let view = "compare", alive = true, noticeTimer, frameFailures = 0;
  let commandChain = Promise.resolve(), pendingTransition = null, transitionRevision = 0;
  const debounces = new Map();
  let captureDirty = false, captureKey = "", captureSource = null, formatKey = "";

  function notice(message) {
    $("notice").textContent = message;
    $("notice").hidden = false;
    clearTimeout(noticeTimer);
    noticeTimer = setTimeout(() => { $("notice").hidden = true; }, 6500);
  }

  async function api(path, command) {
    const response = await fetch(path, command ? {
      method: "POST", headers: { "Content-Type": "application/json", "X-Schlieren": "1" },
      body: JSON.stringify(command), signal: AbortSignal.timeout(8000),
    } : { cache: "no-store", signal: AbortSignal.timeout(8000) });
    const value = await response.json();
    if (!response.ok) throw new Error(value.error || `Server error ${response.status}`);
    return value;
  }

  function send(command) {
    // Preserve click/key order even when camera switching and sliders overlap.
    commandChain = commandChain.then(() => api("/api/command", command))
      .catch((error) => { notice(error.message); return null; });
    return commandChain;
  }

  async function transition(command) {
    const revision = ++transitionRevision;
    pendingTransition = Infinity;
    clearBitmap();
    const result = await send(command);
    if (revision === transitionRevision) pendingTransition = result?.command ?? null;
  }

  function switchSource(id) {
    clearBitmap();
    $("empty-state").hidden = false;
    $("empty-state").querySelector("h1").textContent = "Switching source";
    $("empty-state").querySelector("p").textContent = "The previous camera is released before the next one opens.";
    transition({ action: "source", id });
  }

  function clearBitmap() {
    bitmap?.close();
    bitmap = null;
    bitmapGeneration = -1;
  }

  async function refreshCameras() {
    $("refresh-cameras").disabled = true;
    try {
      const result = await api("/api/cameras");
      cameras = result.cameras;
      $("camera").replaceChildren(new Option("Synthetic demo", "demo"));
      $("camera-list").replaceChildren();
      for (const camera of cameras) {
        const label = `Camera ${camera.number} · ${camera.name}`;
        $("camera").add(new Option(label, camera.id));
        const button = document.createElement("button");
        button.className = "camera-item";
        button.dataset.camera = camera.id;
        button.setAttribute("aria-label", label);
        const key = document.createElement("kbd");
        key.textContent = camera.number;
        const name = document.createElement("span");
        name.textContent = camera.name;
        button.append(key, name);
        button.onclick = () => switchSource(camera.id);
        $("camera-list").append(button);
      }
      $("camera").add(new Option("No camera / stopped", "stop"));
      if (state) $("camera").value = state.source_id;
      if (!cameras.length) $("camera-list").textContent = "No cameras detected. Connect a webcam and refresh.";
    } catch (error) { notice(error.message); }
    finally { $("refresh-cameras").disabled = false; }
  }

  function showEmpty(title, description) {
    $("empty-state").hidden = false;
    $("empty-state").querySelector("h1").textContent = title;
    $("empty-state").querySelector("p").textContent = description;
  }

  function updateCaptureUI() {
    if (!state.capture) {
      $("capture-modes-note").textContent = "Restart start-demo.ps1 to load the new camera controls.";
      $("capture-controls").querySelectorAll("button, input, select").forEach((el) => { el.disabled = true; });
      return;
    }
    const available = (state.formats?.modes || []).filter((mode) => mode.selectable);
    const nextFormatKey = JSON.stringify([state.source_id, available]);
    if (nextFormatKey !== formatKey) {
      formatKey = nextFormatKey;
      const sizes = new Map();
      for (const mode of available) sizes.set(`${mode.width}x${mode.height}`, `${mode.width} × ${mode.height}`);
      const requestSize = `${state.capture.width}x${state.capture.height}`;
      sizes.set(requestSize, `${state.capture.width} × ${state.capture.height}`);
      $("capture-resolution").replaceChildren(...[...sizes].map(([value, label]) => new Option(label, value)), new Option("Custom size…", "custom"));
      captureKey = "";
    }
    const sourceChanged = captureSource !== state.source_id;
    if (sourceChanged) captureDirty = false;
    captureSource = state.source_id;
    const nextKey = JSON.stringify(state.capture);
    if (!captureDirty && (sourceChanged || captureKey !== nextKey) && pendingTransition === null) {
      captureKey = nextKey;
      $("capture-width").value = state.capture.width;
      $("capture-height").value = state.capture.height;
      const size = `${state.capture.width}x${state.capture.height}`;
      $("capture-resolution").value = [...$("capture-resolution").options].some((option) => option.value === size) ? size : "custom";
      $("custom-size").hidden = $("capture-resolution").value !== "custom";
      $("capture-fps").value = state.capture.fps;
      $("capture-format").value = state.capture.fourcc;
      $("processing-width").value = state.capture.processing_width;
    }
    const disabled = state.source_id === "stop" || pendingTransition !== null;
    $("capture-controls").querySelectorAll("button, input, select").forEach((el) => { el.disabled = disabled; });
    $("max-fps").disabled = $("max-resolution").disabled = disabled || !available.length;
    $("rescan-modes").disabled = disabled || state.source_id === "demo";
    if (available.length) {
      const speed = [...available].sort((a, b) => b.fps - a.fps || b.width * b.height - a.width * a.height)[0];
      const size = [...available].sort((a, b) => b.width * b.height - a.width * a.height || b.fps - a.fps)[0];
      $("capture-presets-hint").textContent = `Speed: ${speed.width} × ${speed.height} / ${speed.fps} FPS. Detail: ${size.width} × ${size.height} / ${size.fps} FPS.`;
    } else $("capture-presets-hint").textContent = "Custom resolution and FPS requests are available below.";
    const actual = state.actual_capture;
    const processed = state.processing_shape;
    $("capture-actual").textContent = actual
      ? `Delivered: ${actual.width} × ${actual.height}\nDriver: ${actual.fps ?? "unknown"} FPS · ${actual.fourcc}${processed ? `\nProcessing: ${processed[1]} × ${processed[0]}` : ""}`
      : state.source_id === "demo" ? "Synthetic input · no hardware frame rate" : "Waiting for a camera frame";
    $("capture-warning").hidden = !actual?.warning;
    $("capture-warning").textContent = actual?.warning || "";
    $("capture-modes-note").textContent = state.formats?.note || "";
  }

  function changeCapture(extra) {
    if (!state || state.source_id === "stop") return;
    captureDirty = false;
    captureKey = "";
    showEmpty("Applying camera settings", "Reopening the selected camera. Press Space to capture a new background after the image settles.");
    transition({ action: "capture", id: state.source_id, ...extra });
  }

  function updateUI(next) {
    const previous = state;
    state = next;
    if (pendingTransition !== null && state.applied_command >= pendingTransition) pendingTransition = null;
    if (bitmap && bitmapGeneration !== state.generation) clearBitmap();
    $("connection").innerHTML = "<i></i> LOCAL SESSION";
    $("source-title").textContent = state.source_name;
    $("source-meta").textContent = state.frame_shape
      ? `${state.frame_shape[1]} × ${state.frame_shape[0]} · ${state.source_id === "demo" ? "Synthetic intensity changes" : "Live camera"}`
      : "Select a source, then press Space to train";
    if (document.activeElement !== $("camera")) $("camera").value = state.source_id;
    document.querySelectorAll("[data-camera]").forEach((button) => {
      button.classList.toggle("active", button.dataset.camera === state.source_id);
    });
    document.querySelectorAll("[data-mode]").forEach((button) => {
      button.setAttribute("aria-pressed", String(button.dataset.mode === state.settings.mode));
    });
    $("status").textContent = state.status;
    $("status-dot").style.background = state.error ? "#f6ac83" : state.training ? "#dfc783" : "#c8e896";
    $("fps").textContent = `Processing ${state.fps.toFixed(1)} FPS`;
    updateCaptureUI();
    const percent = state.ready ? 100 : Math.min(100, 100 * state.training_count / state.training_total);
    $("training-progress").style.width = `${percent}%`;
    document.querySelector("[role=progressbar]").setAttribute("aria-valuenow", Math.round(percent));
    $("training-label").textContent = state.training ? `Training ${state.training_count} / ${state.training_total}`
      : state.ready ? (state.adaptive ? "Model adapting" : "Background frozen") : "Press Space to train";
    $("retrain").disabled = !state.connected;
    $("learning").disabled = !state.ready || state.settings.mode === "reference";
    $("learning").textContent = state.adaptive ? "Freeze model" : "Enable learning";
    $("threshold").disabled = state.settings.mode !== "reference";
    $("mog2-controls").hidden = state.settings.mode !== "mog2";
    $("threshold-hint").textContent = state.settings.mode === "reference"
      ? "Threshold controls the reference mask; gain controls the difference image."
      : "This model determines the foreground mask. Gain still controls the difference image.";
    for (const [id, key, suffix] of [["gain", "gain", "×"], ["threshold", "threshold", ""], ["mog2-threshold", "mog2_threshold", ""]]) {
      if (document.activeElement !== $(id) && !debounces.has(id)) {
        $(id).value = state.settings[key];
        $(`${id}-value`).textContent = `${state.settings[key]}${suffix}`;
      }
    }
    for (const [id, key] of [["signed", "signed"], ["cleanup", "cleanup"], ["light", "compensate_light"]]) {
      if (document.activeElement !== $(id)) $(id).checked = state.settings[key];
    }
    if (state.error) {
      clearBitmap();
      showEmpty("Camera needs attention", state.error);
    } else if (!state.connected) {
      clearBitmap();
      showEmpty("Choose your camera", "Select a numbered camera, or choose Synthetic demo to explore the controls.");
    } else if (bitmap) $("empty-state").hidden = true;
    if (state.error && state.error !== previous?.error) notice(state.error);
  }

  async function pollState() {
    if (!alive) return;
    try { updateUI(await api("/api/state")); }
    catch (error) {
      clearBitmap();
      $("connection").textContent = "OFFLINE";
      $("status").textContent = "Local server disconnected";
      showEmpty("Start the local server", "Run start-demo.ps1 in the project folder, then keep its terminal open.");
    }
    setTimeout(pollState, 200);
  }

  async function pollFrame() {
    if (!alive) return;
    try {
      const response = await fetch("/api/frame.jpg", { cache: "no-store", signal: AbortSignal.timeout(5000) });
      if (response.status === 200) {
        const generation = Number(response.headers.get("X-Generation"));
        const image = await createImageBitmap(await response.blob());
        if (state && pendingTransition === null && generation === state.generation && !state.error && state.connected) {
          bitmap?.close();
          bitmap = image;
          bitmapGeneration = generation;
          $("empty-state").hidden = true;
        } else image.close();
      } else if (response.status === 204) clearBitmap();
      frameFailures = 0;
    } catch (_) { frameFailures++; if (frameFailures > 3) clearBitmap(); }
    setTimeout(pollFrame, frameFailures ? 500 : 8);
  }

  function retrain() { transition({ action: "train" }); }
  function nextMode() { transition({ action: "mode", value: modes[(modes.indexOf(state?.settings.mode) + 1) % modes.length] }); }
  function setView(value) { view = value; $("view").value = value; }
  async function fullscreen() {
    const leaving = document.body.classList.contains("immersive");
    document.body.classList.toggle("immersive", !leaving);
    try {
      if (leaving && document.fullscreenElement) await document.exitFullscreen();
      else if (!leaving) await document.documentElement.requestFullscreen();
    } catch (_) { if (!leaving) notice("Full-window view is enabled. For browser fullscreen, open this page in Chrome or Edge."); }
  }
  function toggleControls() { document.body.classList.toggle("controls-hidden"); }

  $("refresh-cameras").onclick = refreshCameras;
  $("capture-form").oninput = () => { captureDirty = true; };
  $("capture-resolution").onchange = () => {
    captureDirty = true;
    const value = $("capture-resolution").value;
    $("custom-size").hidden = value !== "custom";
    if (value !== "custom") {
      const [width, height] = value.split("x").map(Number);
      $("capture-width").value = width;
      $("capture-height").value = height;
    }
  };
  $("capture-form").onsubmit = (event) => {
    event.preventDefault();
    changeCapture({ settings: {
      width: Number($("capture-width").value), height: Number($("capture-height").value),
      fps: Number($("capture-fps").value), fourcc: $("capture-format").value,
      processing_width: Number($("processing-width").value),
    } });
  };
  $("max-fps").onclick = () => changeCapture({ preset: "max_fps" });
  $("max-resolution").onclick = () => changeCapture({ preset: "max_resolution" });
  $("rescan-modes").onclick = () => changeCapture({ rescan: true });
  $("camera").onchange = (event) => switchSource(event.target.value);
  $("stop-camera").onclick = () => switchSource("stop");
  $("retrain").onclick = retrain;
  $("learning").onclick = () => send({ action: "learning" });
  $("fullscreen").onclick = fullscreen;
  $("exit-fullscreen").onclick = fullscreen;
  document.addEventListener("fullscreenchange", () => {
    if (!document.fullscreenElement) document.body.classList.remove("immersive");
  });
  $("hide-controls").onclick = toggleControls;
  $("view").onchange = (event) => setView(event.target.value);
  document.querySelectorAll("[data-mode]").forEach((button) => {
    button.onclick = () => transition({ action: "mode", value: button.dataset.mode });
  });
  for (const [id, key, suffix] of [["gain", "gain", "×"], ["threshold", "threshold", ""], ["mog2-threshold", "mog2_threshold", ""]]) {
    $(id).oninput = () => {
      $(`${id}-value`).textContent = `${$(id).value}${suffix}`;
      clearTimeout(debounces.get(id));
      debounces.set(id, setTimeout(() => {
        debounces.delete(id);
        send({ action: "settings", [key]: Number($(id).value) });
      }, 90));
    };
  }
  for (const [id, key] of [["signed", "signed"], ["cleanup", "cleanup"], ["light", "compensate_light"]]) {
    $(id).onchange = () => send({ action: "settings", [key]: $(id).checked });
  }

  if (window.innerWidth < 700) document.body.classList.add("controls-hidden");
  if (!window.p5) {
    showEmpty("p5.js did not load", "Check that p5-demo/vendor/p5.min.js is present, then reload this page.");
    return;
  }

  const sketch = new p5((p) => {
    const labels = ["LIVE INPUT", "CAPTURED BACKGROUND", "AMPLIFIED DIFFERENCE", "FOREGROUND MASK"];
    p.setup = () => {
      const stage = $("stage");
      p.createCanvas(stage.clientWidth, stage.clientHeight).parent(stage);
      p.pixelDensity(1);
      p.frameRate(60);
      p.textFont("Segoe UI");
      new ResizeObserver(() => p.resizeCanvas(stage.clientWidth, stage.clientHeight)).observe(stage);
    };

    function tile(index, x, y, width, height) {
      p.noStroke(); p.fill("#14181b"); p.rect(x, y, width, height);
      const header = 32, sw = bitmap.width / 2, sh = bitmap.height / 2;
      const scale = Math.min(width / sw, Math.max(1, height - header) / sh);
      const dw = sw * scale, dh = sh * scale;
      const dx = x + (width - dw) / 2, dy = y + header + (height - header - dh) / 2;
      p.drawingContext.drawImage(bitmap, (index % 2) * sw, Math.floor(index / 2) * sh, sw, sh, dx, dy, dw, dh);
      p.fill("#1d2327"); p.rect(x, y, width, header);
      p.fill(index === 2 ? "#c8e896" : "#c3cdd2"); p.textSize(width < 350 ? 9 : 11);
      p.textAlign(p.LEFT, p.CENTER);
      let label = `${String(index + 1).padStart(2, "0")}  ${labels[index]}`;
      if (index === 2 && width > 360) label += `  /  ${state.settings.gain}× ${state.settings.signed ? "SIGNED" : "ABSOLUTE"}`;
      if (index === 3 && width > 360) label += `  /  ${state.settings.mode.toUpperCase()}`;
      p.text(label, x + 12, y + header / 2);
      if (!state.ready && index > 0) {
        p.fill(10, 15, 18, 170); p.rect(x, y + header, width, height - header);
        p.fill("#c8d0d3"); p.textAlign(p.CENTER, p.CENTER); p.textSize(width < 300 ? 10 : 13);
        p.text(state.training ? `Training ${state.training_count} / ${state.training_total}` : "Press Space to train", x + width / 2, y + height / 2);
      }
    }

    p.draw = () => {
      p.background("#0b0e10");
      if (!bitmap || !state || bitmapGeneration !== state.generation) return;
      const gap = 8;
      if (view === "compare") {
        const w = (p.width - gap) / 2, h = (p.height - gap) / 2;
        for (let index = 0; index < 4; index++) tile(index, (index % 2) * (w + gap), Math.floor(index / 2) * (h + gap), w, h);
      } else {
        const index = { raw: 0, reference: 1, difference: 2, mask: 3 }[view];
        tile(index, 0, 0, p.width, p.height);
      }
    };
  });

  function saveImage() {
    if (!bitmap) return notice("Wait for a live frame before saving a snapshot.");
    sketch.saveCanvas(`schlieren-${view}-${Date.now()}`, "png");
  }
  $("save-image").onclick = saveImage;
  document.addEventListener("keydown", (event) => {
    if (event.repeat || event.ctrlKey || event.metaKey || event.altKey || event.isComposing) return;
    const element = event.target;
    if (event.code === "Space" && element.tagName === "INPUT" && element.type === "number") {
      event.preventDefault(); retrain(); return;
    }
    if (element.isContentEditable || element.tagName === "TEXTAREA" || (element.tagName === "INPUT" && element.type !== "range" && element.type !== "checkbox")) return;
    if (event.code === "Space") { event.preventDefault(); retrain(); return; }
    if (event.key === "Escape" && document.body.classList.contains("immersive")) { event.preventDefault(); fullscreen(); return; }
    const key = event.key.toLowerCase();
    if (/^[1-9]$/.test(key)) {
      event.preventDefault();
      const camera = cameras.find((item) => item.number === Number(key));
      if (camera) switchSource(camera.id);
      else notice(`Camera ${key} is not connected. Refresh the camera list.`);
    } else if (key === "m") { event.preventDefault(); nextMode(); }
    else if (key === "v") { event.preventDefault(); setView(views[(views.indexOf(view) + 1) % views.length]); }
    else if (key === "f") { event.preventDefault(); fullscreen(); }
    else if (key === "h") { event.preventDefault(); toggleControls(); }
    else if (key === "s") { event.preventDefault(); saveImage(); }
  });
  window.addEventListener("pagehide", () => { alive = false; clearBitmap(); });
  refreshCameras();
  pollState();
  pollFrame();
})();
