// Rendering only (three.js + WebXR). Gets quaternions as plain {x, y, z, w}; no robot or pose logic here.
//
// The camera image is a window anchored in the room at the robot's MEASURED head orientation
// (rotation-only reprojection): turn your head and the image stays put until the robot has followed.
// The window has the camera's field of view, so a flat plane matches the pinhole image exactly.

import * as THREE from "three";
import { createVideoSource } from "./videosource.js";

export function createScene({ video, vfovDeg, distM, statusText, onHeadsetPose, onSelect, onEnd, onFrame, vrButtons = [], warning = () => "", windowMode = "world", log = console.log }) {
  const renderer = new THREE.WebGLRenderer({ antialias: true });
  renderer.xr.enabled = true;
  renderer.domElement.addEventListener("webglcontextlost", () => log("ERROR webgl context lost (GPU crash / out of memory)"));
  renderer.xr.setReferenceSpaceType("local");
  const scene = new THREE.Scene();
  scene.background = new THREE.Color(0x0b0d16);
  const camera = new THREE.PerspectiveCamera(70, 1, 0.05, 100);
  scene.add(camera);

  // ---- environment: a calm evening sky and a glowing floor, so the room feels stable and not like a void
  const sky = new THREE.Mesh(new THREE.SphereGeometry(40, 48, 24), new THREE.ShaderMaterial({
    side: THREE.BackSide, depthWrite: false,
    vertexShader: "varying vec3 vDir; void main() { vDir = normalize(position); gl_Position = projectionMatrix * modelViewMatrix * vec4(position, 1.0); }",
    fragmentShader: `varying vec3 vDir;
      void main() {
        float h = vDir.y;
        vec3 zenith = vec3(0.04, 0.05, 0.12), horizon = vec3(0.22, 0.13, 0.26), ground = vec3(0.025, 0.025, 0.04);
        vec3 c = h > 0.0 ? mix(horizon, zenith, pow(h, 0.5)) : mix(horizon * 0.5, ground, pow(-h, 0.35));
        c += vec3(1.0, 0.55, 0.18) * 0.25 * exp(-abs(h) * 12.0);   // warm band along the horizon
        gl_FragColor = vec4(c, 1.0);
      }`,
  }));
  scene.add(sky);
  const floor = new THREE.Mesh(new THREE.CircleGeometry(9, 96), new THREE.MeshBasicMaterial({
    transparent: true, depthWrite: false,
    map: canvasTexture(1024, 1024, (ctx, w) => {
      const r = w / 2;
      const glow = ctx.createRadialGradient(r, r, 0, r, r, r);
      glow.addColorStop(0, "rgba(255,149,0,0.28)"); glow.addColorStop(0.35, "rgba(140,90,200,0.10)"); glow.addColorStop(1, "rgba(0,0,0,0)");
      ctx.fillStyle = glow; ctx.fillRect(0, 0, w, w);
      for (let k = 1; k <= 8; k++) {   // rings every ~1.1 m, fading outwards
        ctx.strokeStyle = `rgba(255,255,255,${0.10 - k * 0.011})`; ctx.lineWidth = 2;
        ctx.beginPath(); ctx.arc(r, r, (r * k) / 8.5, 0, Math.PI * 2); ctx.stroke();
      }
    }),
  }));
  floor.rotation.x = -Math.PI / 2;
  floor.position.y = -1.4;
  scene.add(floor);

  // Camera frames: see videosource.js for why the VR window does not simply use the <video> element.
  const source = createVideoSource(video, log);
  const canvasTex = new THREE.CanvasTexture(source.canvas);   // fixed size, so texStorage2D is fine
  canvasTex.colorSpace = THREE.SRGBColorSpace;
  canvasTex.minFilter = THREE.LinearFilter;
  canvasTex.generateMipmaps = false;
  canvasTex.anisotropy = renderer.capabilities.getMaxAnisotropy();   // sharper when the window is seen at an angle
  // "direct" mode only: VideoTexture is re-allocated with texImage2D on every upload (no texStorage2D),
  // so resolution changes are fine. needsUpdate is set per XR frame below, because its
  // requestVideoFrameCallback can stop firing on Android while an immersive session hides the page.
  const videoTex = new THREE.VideoTexture(video);
  videoTex.colorSpace = THREE.SRGBColorSpace;
  videoTex.minFilter = THREE.LinearFilter;
  videoTex.generateMipmaps = false;
  videoTex.anisotropy = renderer.capabilities.getMaxAnisotropy();
  const screenH = 2 * distM * Math.tan(vfovDeg / 2 * Math.PI / 180);
  // Shown instead of the video while no camera frame has arrived, so "black" is never ambiguous.
  const noVideoTex = canvasTexture(1024, 576, (ctx, w, h) => {
    ctx.fillStyle = "#1a1a1f"; ctx.fillRect(0, 0, w, h);
    ctx.fillStyle = "#ff9500"; ctx.font = "bold 56px sans-serif"; ctx.textAlign = "center";
    ctx.fillText("No camera image yet", w / 2, h / 2 - 30);
    ctx.fillStyle = "#bbb"; ctx.font = "32px sans-serif";
    ctx.fillText("Robot Mac: camera permission for the app running the daemon?", w / 2, h / 2 + 40);
  });
  const screenMat = new THREE.MeshBasicMaterial({ map: noVideoTex });
  const screen = new THREE.Mesh(new THREE.PlaneGeometry(1, 1), screenMat);
  screen.scale.set(screenH * 16 / 9, screenH, 1);
  screen.position.set(0, 0, -distM);
  video.addEventListener("resize", () => {
    if (video.videoHeight) screen.scale.x = screenH * video.videoWidth / video.videoHeight;
  });
  // Dark rounded bezel with a soft glow behind the video: the window stays findable even without video.
  const bezel = new THREE.Mesh(new THREE.PlaneGeometry(1.04, 1.07), new THREE.MeshBasicMaterial({
    transparent: true, depthWrite: false,
    map: canvasTexture(1040, 620, (ctx, w, h) => {
      ctx.shadowColor = "rgba(255,149,0,0.45)"; ctx.shadowBlur = 40;
      ctx.fillStyle = "#12141c"; ctx.beginPath(); ctx.roundRect(24, 24, w - 48, h - 48, 26); ctx.fill();
      ctx.shadowBlur = 0; ctx.strokeStyle = "rgba(255,255,255,0.18)"; ctx.lineWidth = 3; ctx.stroke();
    }),
  }));
  bezel.position.z = -0.01;
  screen.add(bezel);
  const robotView = new THREE.Group();
  robotView.add(screen);
  scene.add(robotView);

  // Head-locked debug panel at the top of the view, hidden unless "Debug" is on (More menu).
  const hudCanvas = document.createElement("canvas");
  hudCanvas.width = 1800; hudCanvas.height = 190;
  const hudCtx = hudCanvas.getContext("2d");
  const hudTex = new THREE.CanvasTexture(hudCanvas);
  const hud = new THREE.Mesh(new THREE.PlaneGeometry(1.1, 0.116), new THREE.MeshBasicMaterial({ map: hudTex, transparent: true }));
  hud.position.set(0, 0.36, -1.2);
  hud.visible = false;
  camera.add(hud);
  setInterval(() => {
    if (!hud.visible) return;
    hudCtx.clearRect(0, 0, hudCanvas.width, hudCanvas.height);
    hudCtx.fillStyle = "rgba(8,10,16,0.82)";
    hudCtx.beginPath(); hudCtx.roundRect(0, 0, hudCanvas.width, hudCanvas.height, 24); hudCtx.fill();
    hudCtx.fillStyle = "#86efac"; hudCtx.font = "26px ui-monospace, monospace";
    [...statusText().split("\n"), `${source.stats()}   ${perfLine}`].forEach((l, i) => hudCtx.fillText(l, 20, 40 + i * 40));
    hudTex.needsUpdate = true;
  }, 200);

  // ---- comfort vignette: darkens the edge of the view while the picture turns without your own head motion
  // (Reachy turning to a speaker or framing a face). Optic flow in the periphery that the body does not feel is
  // the main cause of VR sickness; a narrower field of view during such turns is the standard counter-measure.
  // Head-locked, centre stays clear (the dock and the middle of the video stay visible).
  const vignette = new THREE.Mesh(new THREE.PlaneGeometry(2.4, 2.4), new THREE.MeshBasicMaterial({
    transparent: true, depthTest: false, depthWrite: false, opacity: 0,
    map: canvasTexture(512, 512, (ctx, w, h) => {
      const g = ctx.createRadialGradient(w / 2, h / 2, w * 0.17, w / 2, h / 2, w * 0.36);
      g.addColorStop(0, "rgba(0,0,0,0)"); g.addColorStop(1, "rgba(0,0,0,1)");
      ctx.fillStyle = g; ctx.fillRect(0, 0, w, h);
    }),
  }));
  vignette.position.set(0, 0, -0.6);
  vignette.renderOrder = 999;
  vignette.visible = false;
  camera.add(vignette);
  let vignetteGoal = 0;

  // ---- dock below the view, standing in the room (it moves with the video window, not with every head turn):
  // round buttons with icon + label like a video call, the mic in the middle. Entries with `more: true` sit in
  // a second row that the automatic "More" button opens. label / icon / active may be functions.
  // {kind: "mic", muted: () => bool}: the big mic button.
  const ui = new THREE.Group();   // lazily follows the head yaw (see the animation loop)
  scene.add(ui);
  const val = (v) => (typeof v === "function" ? v() : v);
  let moreOpen = false;
  const entries = [...vrButtons.filter((b) => !b.more && b.kind !== "mic"),
    { icon: () => (moreOpen ? "✕" : "⋯"), label: () => (moreOpen ? "Less" : "More"), onClick: () => { moreOpen = !moreOpen; } },
    ...vrButtons.filter((b) => b.more), ...vrButtons.filter((b) => b.kind === "mic")];
  const DOCK_Z = -1.1, DOCK_Y = -0.3, BD = 0.09, MIC_D = 0.12, STEP = 0.11, ROW = 0.125;   // metres
  const buttons = entries.map((b) => {
    let key = "";
    const isMic = b.kind === "mic";
    const draw = (hover) => (ctx, w, h) => {
      ctx.clearRect(0, 0, w, h);
      if (isMic) return drawMicButton(ctx, w, h, b.muted(), hover, val(b.label));
      const active = !!val(b.active), r = w / 2 - 8, cy = r + 8;
      ctx.fillStyle = hover ? "#ff9500" : active ? "rgba(255,149,0,0.28)" : "rgba(22,24,34,0.9)";
      ctx.beginPath(); ctx.arc(w / 2, cy, r, 0, 2 * Math.PI); ctx.fill();
      ctx.strokeStyle = hover ? "rgba(255,255,255,0)" : active ? "rgba(255,149,0,0.95)" : "rgba(255,255,255,0.22)";
      ctx.lineWidth = 6; ctx.stroke();
      ctx.textAlign = "center"; ctx.textBaseline = "middle";
      ctx.font = "110px system-ui, 'Noto Color Emoji', sans-serif";
      ctx.fillStyle = hover ? "#111" : "#fff";
      ctx.fillText(val(b.icon) ?? "", w / 2, cy + 6);
      ctx.font = "600 46px system-ui, sans-serif"; ctx.textBaseline = "alphabetic";
      ctx.fillStyle = active ? "#ffb347" : "#eef0f4";
      ctx.fillText(val(b.label), w / 2, h - 10);
    };
    const d = isMic ? MIC_D : BD;
    const tex = [canvasTexture(256, 320, draw(false)), canvasTexture(256, 320, draw(true))];
    const mesh = new THREE.Mesh(new THREE.PlaneGeometry(d, d * 1.25), new THREE.MeshBasicMaterial({ map: tex[0], transparent: true }));
    mesh.userData = {
      onClick: b.onClick, tex, more: !!b.more, mic: isMic, d,
      refresh() {   // redraw when label / icon / active / mute state changed
        const now = `${val(b.icon)}|${val(b.label)}|${!!val(b.active)}|${b.muted?.() ?? ""}`;
        if (now === key) return;
        key = now;
        tex.forEach((t, hover) => { draw(!!hover)(t.image.getContext("2d"), t.image.width, t.image.height); t.needsUpdate = true; });
      },
    };
    mesh.userData.refresh();
    ui.add(mesh);
    return mesh;
  });
  // Warning above the dock while something essential is missing (e.g. no caption server = no speaker following).
  const warnCanvas = document.createElement("canvas");
  warnCanvas.width = 1600; warnCanvas.height = 90;
  const warnTex = new THREE.CanvasTexture(warnCanvas);
  warnTex.colorSpace = THREE.SRGBColorSpace;
  const warn = new THREE.Mesh(new THREE.PlaneGeometry(0.64, 0.036), new THREE.MeshBasicMaterial({ map: warnTex, transparent: true }));
  warn.position.set(0, DOCK_Y + MIC_D * 0.625 + 0.035, DOCK_Z);
  ui.add(warn);
  let warnText = null, infoText = "", infoUntil = 0;
  function updateWarning() {
    const w0 = warning() || "";
    const t = w0 || (performance.now() < infoUntil ? infoText : "");
    const bg = w0 ? "rgba(120,20,24,0.88)" : "rgba(22,24,34,0.9)";
    if (t === warnText) return;
    warnText = t;
    warn.visible = !!t;
    const ctx = warnCanvas.getContext("2d"), w = warnCanvas.width, h = warnCanvas.height;
    ctx.clearRect(0, 0, w, h);
    if (!t) return;
    ctx.font = "600 44px system-ui, sans-serif";
    const tw = Math.min(w, ctx.measureText(t).width + 60);
    ctx.fillStyle = bg;
    ctx.beginPath(); ctx.roundRect((w - tw) / 2, 0, tw, h, h / 2); ctx.fill();
    ctx.fillStyle = "#fff"; ctx.textAlign = "center"; ctx.textBaseline = "middle";
    ctx.fillText(t.length > 70 ? `${t.slice(0, 68)}…` : t, w / 2, h / 2 + 2);
    warnTex.needsUpdate = true;
  }
  function layoutDock() {
    const main = buttons.filter((m) => !m.userData.more && !m.userData.mic);
    const mic = buttons.find((m) => m.userData.mic);
    const more = moreOpen ? buttons.filter((m) => m.userData.more) : [];
    for (const m of buttons) m.visible = false;
    // first row: mic in the middle, the other buttons split left / right of it
    const left = main.slice(0, Math.floor(main.length / 2)), right = main.slice(left.length);
    const gap = mic ? (MIC_D + BD) / 2 + 0.02 : STEP / 2;
    left.forEach((m, i) => { m.visible = true; m.position.set(-gap - (left.length - 1 - i) * STEP, DOCK_Y, DOCK_Z); });
    right.forEach((m, i) => { m.visible = true; m.position.set(gap + i * STEP, DOCK_Y, DOCK_Z); });
    if (mic) { mic.visible = true; mic.position.set(0, DOCK_Y, DOCK_Z); }
    more.forEach((m, i) => { m.visible = true; m.position.set((i - (more.length - 1) / 2) * STEP, DOCK_Y - ROW - 0.01, DOCK_Z); });
  }
  const visibleButtons = () => buttons.filter((m) => m.visible);

  // One visible ray per input source (controller, hand, gaze+pinch), so the buttons can be aimed at.
  const raycaster = new THREE.Raycaster();
  const rays = [0, 1].map(() => {
    const line = new THREE.Line(
      new THREE.BufferGeometry().setFromPoints([new THREE.Vector3(0, 0, 0), new THREE.Vector3(0, 0, -1)]),
      new THREE.LineBasicMaterial({ color: 0xffffff, transparent: true, opacity: 0.6 }));
    line.visible = false;
    scene.add(line);
    return line;
  });
  const m4 = new THREE.Matrix4();

  /** Button hit by the ray of this XR pose, or null. */
  function hitButton(rayPose) {
    m4.fromArray(rayPose.transform.matrix);
    raycaster.ray.origin.setFromMatrixPosition(m4);
    raycaster.ray.direction.set(0, 0, -1).transformDirection(m4);
    scene.updateMatrixWorld(true);
    return raycaster.intersectObjects(visibleButtons(), false)[0] ?? null;
  }

  function updatePointers(frame) {
    const ref = renderer.xr.getReferenceSpace();
    let hovered = null, n = 0;
    for (const src of frame.session.inputSources) {
      const pose = src.targetRaySpace && frame.getPose(src.targetRaySpace, ref);
      if (!pose || n >= rays.length) continue;
      const hit = hitButton(pose);
      if (hit) hovered = hit.object;
      const ray = rays[n++];
      ray.matrix.copy(m4); ray.matrix.decompose(ray.position, ray.quaternion, ray.scale);
      ray.scale.z = hit ? hit.distance : 3;
      ray.visible = true;
    }
    for (; n < rays.length; n++) rays[n].visible = false;
    for (const b of buttons) {
      const map = b.userData.tex[b === hovered ? 1 : 0];
      if (b.material.map !== map) { b.material.map = map; b.material.needsUpdate = true; }
    }
  }

  function handleSelect(e) {
    if (justDragged) { justDragged = false; return; }   // the end of a drag is not a click
    const pose = e.frame.getPose(e.inputSource.targetRaySpace, renderer.xr.getReferenceSpace());
    const hit = pose && hitButton(pose);
    if (hit) hit.object.userData.onClick();
    else onSelect();
  }

  // ---- draggable panels (meeting notes): grab with the ray + pinch/trigger (VR) or the mouse (desktop)
  const draggables = [];   // {mesh, onMoved}
  let dragging = null;     // {item, source: XRInputSource | "mouse", dist}
  let justDragged = false;
  const tmpO = new THREE.Vector3(), tmpD = new THREE.Vector3(), headPos = new THREE.Vector3();
  function hitDraggable(origin, dir) {
    raycaster.set(origin, dir);
    const meshes = draggables.filter((d) => d.mesh.visible).map((d) => d.mesh);
    const hit = raycaster.intersectObjects(meshes, false)[0];
    return hit ? { item: draggables.find((d) => d.mesh === hit.object), dist: hit.distance } : null;
  }
  function moveDragged(origin, dir) {
    const m = dragging.item.mesh;
    m.position.copy(origin).addScaledVector(dir, dragging.dist);
    m.lookAt(headPos);
  }
  function rayOf(pose) {
    m4.fromArray(pose.transform.matrix);
    tmpO.setFromMatrixPosition(m4);
    tmpD.set(0, 0, -1).transformDirection(m4);
  }
  function onSelectStart(e) {
    const pose = e.frame.getPose(e.inputSource.targetRaySpace, renderer.xr.getReferenceSpace());
    if (!pose || hitButton(pose)) return;
    rayOf(pose);
    const hit = hitDraggable(tmpO, tmpD);
    if (hit) dragging = { ...hit, source: e.inputSource };
  }
  function onSelectEnd(e) {
    if (dragging?.source !== e.inputSource) return;
    dragging.item.onMoved?.();
    dragging = null;
    justDragged = true;
  }

  // ---- desktop preview (no headset): same scene in the browser window, mouse drag = head turn
  let desktop = null;   // { yaw, pitch } while active
  const mouse = new THREE.Vector2();
  let hoveredDesktop = null;

  function desktopHit(e) {
    const r = renderer.domElement.getBoundingClientRect();
    mouse.set(((e.clientX - r.left) / r.width) * 2 - 1, -((e.clientY - r.top) / r.height) * 2 + 1);
    scene.updateMatrixWorld(true);
    raycaster.setFromCamera(mouse, camera);
    return raycaster.intersectObjects(visibleButtons(), false)[0]?.object ?? null;
  }

  function resize() {
    renderer.setSize(innerWidth, innerHeight);
    camera.aspect = innerWidth / innerHeight;
    camera.updateProjectionMatrix();
  }

  let drag = null;
  const el = renderer.domElement;
  function mouseRay(e) {
    const r = renderer.domElement.getBoundingClientRect();
    mouse.set(((e.clientX - r.left) / r.width) * 2 - 1, -((e.clientY - r.top) / r.height) * 2 + 1);
    camera.updateMatrixWorld(true);
    raycaster.setFromCamera(mouse, camera);
    tmpO.copy(raycaster.ray.origin); tmpD.copy(raycaster.ray.direction);
  }
  el.addEventListener("pointerdown", (e) => {
    el.setPointerCapture(e.pointerId);
    if (desktop) {
      mouseRay(e);
      const hit = hitDraggable(tmpO, tmpD);
      if (hit && !desktopHit(e)) { dragging = { ...hit, source: "mouse" }; return; }
    }
    drag = { x: e.clientX, y: e.clientY, moved: false };
  });
  el.addEventListener("pointermove", (e) => {
    if (!desktop) return;
    if (dragging?.source === "mouse") {
      if (!(e.buttons & 1)) { dragging.item.onMoved?.(); dragging = null; return; }
      mouseRay(e); headPos.copy(camera.position); moveDragged(tmpO, tmpD);
      return;
    }
    if (drag && !(e.buttons & 1)) drag = null;   // button released outside / pointerup lost: stop turning
    if (drag) {
      const dx = e.clientX - drag.x, dy = e.clientY - drag.y;
      if (Math.abs(dx) + Math.abs(dy) > 3) drag.moved = true;
      desktop.yaw += dx * 0.004;
      desktop.pitch = Math.max(-1.4, Math.min(1.4, desktop.pitch + dy * 0.004));
      drag.x = e.clientX; drag.y = e.clientY;
    }
    hoveredDesktop = desktopHit(e);
    el.style.cursor = hoveredDesktop ? "pointer" : drag ? "grabbing" : "grab";
  });
  el.addEventListener("pointerup", (e) => {
    if (dragging?.source === "mouse") { dragging.item.onMoved?.(); dragging = null; return; }
    const click = drag && !drag.moved;
    drag = null;
    if (click) desktopHit(e)?.userData.onClick();
  });
  function onKey(e) {
    if (e.key === "Escape") exitDesktop();
    if (e.key === "r" || e.key === "R") onSelect();
  }
  function exitDesktop() {
    if (!desktop) return;
    desktop = null;
    removeEventListener("resize", resize);
    removeEventListener("keydown", onKey);
    el.remove();
    camera.quaternion.identity();
    onEnd();
  }

  const target = new THREE.Quaternion();
  let haveTarget = false;
  // windowMode "world" (default, rotation-only reprojection): the video window is world-locked where the robot
  // camera looked when the frame was taken (the app hands in the time-aligned pose), so your own head turns are
  // shown without lag; the robot's delay only shows as the edge of the window.
  // windowMode "comfort": the window stands still while you look into it and glides back in front of you only
  // after you look clearly elsewhere (> FOLLOW_DEG for FOLLOW_S), yaw only. No latency hiding, but calm.
  // The dock always follows lazily like the comfort window.
  const FOLLOW_DEG = 30, FOLLOW_S = 0.5, GLIDE_S = 0.25;
  const headQ = new THREE.Quaternion(), headE = new THREE.Euler(0, 0, 0, "YXZ"), lazyQ = new THREE.Quaternion(), yAxis = new THREE.Vector3(0, 1, 0);
  let lazyYaw = null, awaySince = null, gliding = false, lastNow = 0;
  const wrap = (a) => Math.atan2(Math.sin(a), Math.cos(a));
  function followHead(now) {
    const dt = Math.min(0.1, Math.max(0, (now - lastNow) / 1000));
    lastNow = now;
    const yaw = headE.setFromQuaternion(headQ, "YXZ").y;
    if (lazyYaw == null) lazyYaw = yaw;
    const diff = wrap(yaw - lazyYaw);
    if (Math.abs(diff) > FOLLOW_DEG * Math.PI / 180) awaySince ??= now; else awaySince = null;
    if (awaySince != null && now - awaySince > FOLLOW_S * 1000) gliding = true;
    if (gliding) {
      lazyYaw = wrap(lazyYaw + diff * (1 - Math.exp(-dt / GLIDE_S)));
      if (Math.abs(diff) < 2 * Math.PI / 180) { gliding = false; awaySince = null; }
    }
    lazyQ.setFromAxisAngle(yAxis, lazyYaw);
  }
  // Frame timing for the debug panel: XR frame rate, and how long the video upload and the rendering take.
  let perfN = 0, perfFrames = 0, perfUpload = 0, perfRender = 0, perfLine = "", perfT = performance.now();
  renderer.setAnimationLoop((now, frame) => {
    const t0 = performance.now();
    if (frame) {
      const pose = frame.getViewerPose(renderer.xr.getReferenceSpace());
      if (pose) {
        const o = pose.transform.orientation, p = pose.transform.position;
        headQ.set(o.x, o.y, o.z, o.w); headPos.set(p.x, p.y, p.z);
        ui.position.copy(headPos);
        onHeadsetPose(o, now);
      }
      if (dragging && dragging.source !== "mouse") {
        const rp = frame.getPose(dragging.source.targetRaySpace, renderer.xr.getReferenceSpace());
        if (rp) { rayOf(rp); moveDragged(tmpO, tmpD); }
      }
    } else if (desktop) {
      camera.quaternion.setFromEuler(new THREE.Euler(desktop.pitch, desktop.yaw, 0, "YXZ"));
      headQ.copy(camera.quaternion);
      const q = camera.quaternion;
      onHeadsetPose({ x: q.x, y: q.y, z: q.z, w: q.w }, now);
      for (const b of buttons) {
        const map = b.userData.tex[b === hoveredDesktop ? 1 : 0];
        if (b.material.map !== map) { b.material.map = map; b.material.needsUpdate = true; }
      }
    }
    const tUp = performance.now();
    let map = noVideoTex;
    if (source.mode === "direct") {
      if (video.readyState >= video.HAVE_CURRENT_DATA && video.videoWidth > 0) { videoTex.needsUpdate = true; map = videoTex; }
      source.update();
    } else {
      if (source.update()) canvasTex.needsUpdate = true;
      if (source.hasFrame) map = canvasTex;
    }
    if (screenMat.map !== map) { screenMat.map = map; screenMat.needsUpdate = true; }
    const upMs = performance.now() - tUp;
    for (const b of buttons) b.userData.refresh();
    layoutDock();
    updateWarning();
    followHead(now);
    ui.quaternion.copy(lazyQ);
    if (windowMode === "comfort") robotView.quaternion.copy(lazyQ);
    else if (haveTarget) robotView.quaternion.copy(target);   // already interpolated + time-aligned by the app
    else robotView.quaternion.copy(lazyQ);   // no trustworthy robot pose: stand in front of the user like comfort
    vignette.material.opacity += (vignetteGoal - vignette.material.opacity) * (vignetteGoal > vignette.material.opacity ? 0.35 : 0.06);
    vignette.visible = vignette.material.opacity > 0.01;
    if (frame) updatePointers(frame);
    onFrame?.(now);
    const tR = performance.now();
    renderer.render(scene, camera);
    perfFrames++; perfUpload += upMs; perfRender += performance.now() - tR; perfN += performance.now() - t0;
    if (t0 - perfT > 2000) {
      const n = perfFrames || 1;
      perfLine = `frame ${(perfFrames * 1000 / (t0 - perfT)).toFixed(0)} fps   js ${(perfN / n).toFixed(1)}ms (video draw ${(perfUpload / n).toFixed(1)}ms, render ${(perfRender / n).toFixed(1)}ms)`;
      perfFrames = perfUpload = perfRender = perfN = 0; perfT = t0;
    }
  });

  return {
    async xrSupported() {
      return !!navigator.xr && await navigator.xr.isSessionSupported("immersive-vr").catch(() => false);
    },

    /** Must be called from a user gesture (button tap). */
    async enterVR() {
      log("entering VR, video mode", source.mode);
      const session = await navigator.xr.requestSession("immersive-vr", { optionalFeatures: ["local"] });
      session.addEventListener("select", handleSelect);   // pinch / trigger: button under the ray, else recenter
      session.addEventListener("selectstart", onSelectStart);   // grab a panel
      session.addEventListener("selectend", onSelectEnd);
      session.addEventListener("end", onEnd);
      await renderer.xr.setSession(session);
    },

    /** One line about the camera path (for the heartbeat log). */
    videoStats() { return `${source.stats()}   ${perfLine}`; },

    /** Current camera frame for image analysis (face detection): the fixed-size frame canvas, else the <video>. */
    videoFrame() { return source.mode !== "direct" && source.hasFrame ? source.canvas : video; },

    get videoMode() { return source.mode; },

    /** A camera frame is on the VR window (not the "No camera image yet" placeholder). */
    get hasVideo() { return screenMat.map !== noVideoTex; },

    /** Switch how camera frames reach the VR window (track -> canvas -> direct). Returns the new mode. */
    cycleVideo() { return source.cycle(); },

    /** Debug panel (status lines) at the top of the view. */
    toggleDebug() { hud.visible = !hud.visible; },

    /** "world" (world-locked, latency-hiding) or "comfort" (calm, lazily follows the head). */
    get windowMode() { return windowMode; },
    setWindowMode(m) { windowMode = m === "comfort" ? "comfort" : "world"; lazyYaw = null; log("view:", windowMode); },

    /** 0..1: how strongly to darken the edge of the view (fades in fast, out slowly). */
    setVignette(strength) { vignetteGoal = Math.max(0, Math.min(1, strength)); },

    exitVR() { renderer.xr.getSession()?.end(); exitDesktop(); },

    /** Desktop preview for debugging without a headset: drag = look, click = VR buttons, R = recenter, Esc = exit. */
    enterDesktop() {
      desktop = { yaw: 0, pitch: 0 };
      el.style.cssText = "position:fixed;inset:0;z-index:10;cursor:grab;touch-action:none";
      renderer.setPixelRatio(Math.min(devicePixelRatio, 1.5));   // full retina is costly and not needed here
      resize();
      document.body.appendChild(el);
      addEventListener("resize", resize);
      addEventListener("keydown", onKey);
    },

    /** Short message above the dock (a warning takes precedence). */
    info(text, ms = 5000) { infoText = text; infoUntil = performance.now() + ms; },

    /** Bring the video window and the dock back in front of you (after a recenter). */
    recenterView() { gliding = true; },

    /** Robot head orientation in the XR world, as {x, y, z, w}. */
    setRobotHead(q) { target.set(q.x, q.y, q.z, q.w); haveTarget = true; },

    /** World-locked mode without a trustworthy robot pose: the window stands in front of the user instead. */
    clearRobotHead() { haveTarget = false; },

    /** For overlays (speech bubbles): the room, and the group that moves with the video window. */
    three: { scene, robotView },

    /** Make a mesh in the room draggable (ray + pinch in VR, mouse on the desktop); onMoved after each drag. */
    addDraggable(mesh, onMoved) { draggables.push({ mesh, onMoved }); },
  };
}

function canvasTexture(w, h, draw) {
  const c = document.createElement("canvas");
  c.width = w; c.height = h;
  draw(c.getContext("2d"), w, h);
  const tex = new THREE.CanvasTexture(c);
  tex.colorSpace = THREE.SRGBColorSpace;
  return tex;
}

/** Round mic button: red with a crossed-out mic when muted, like a video call. */
function drawMicButton(ctx, w, h, muted, hover, label) {
  const r = w / 2 - 10, cx = w / 2, cy = r + 10;
  ctx.fillStyle = muted ? (hover ? "#ff6b6b" : "#e5484d") : (hover ? "#ff9500" : "rgba(40,40,46,0.95)");
  ctx.beginPath(); ctx.arc(cx, cy, r, 0, 2 * Math.PI); ctx.fill();
  ctx.strokeStyle = "#fff"; ctx.fillStyle = "#fff"; ctx.lineWidth = 12; ctx.lineCap = "round";
  ctx.beginPath(); ctx.roundRect(cx - 24, cy - 70, 48, 88, 24); ctx.fill();          // capsule
  ctx.beginPath(); ctx.arc(cx, cy - 10, 46, 0.15 * Math.PI, 0.85 * Math.PI); ctx.stroke(); // holder
  ctx.beginPath(); ctx.moveTo(cx, cy + 36); ctx.lineTo(cx, cy + 62); ctx.stroke();          // stem
  if (muted) {
    ctx.strokeStyle = muted ? "#e5484d" : "#fff"; ctx.lineWidth = 26;
    ctx.beginPath(); ctx.moveTo(cx - 62, cy - 70); ctx.lineTo(cx + 62, cy + 62); ctx.stroke();
    ctx.strokeStyle = "#fff"; ctx.lineWidth = 12;
    ctx.beginPath(); ctx.moveTo(cx - 62, cy - 70); ctx.lineTo(cx + 62, cy + 62); ctx.stroke();
  }
  ctx.fillStyle = "#eee"; ctx.font = "600 40px system-ui, sans-serif"; ctx.textAlign = "center"; ctx.textBaseline = "alphabetic";
  ctx.fillText(label, cx, h - 4);
}
