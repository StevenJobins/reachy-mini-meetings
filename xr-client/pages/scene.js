// Rendering only (three.js + WebXR). Gets quaternions as plain {x, y, z, w}; no robot or pose logic here.
//
// The camera image is a window anchored in the room at the robot's MEASURED head orientation
// (rotation-only reprojection): turn your head and the image stays put until the robot has followed.
// The window has the camera's field of view, so a flat plane matches the pinhole image exactly.

import * as THREE from "three";
import { createVideoSource } from "./videosource.js";
import { CameraModel } from "./camera.js";

export function createScene({ video, vfovDeg, cameraModel = null, distM, statusText, onHeadsetPose, onSelect, onEnd, onFrame, vrButtons = [], warning = () => "", windowMode = "world", log = console.log }) {
  const renderer = new THREE.WebGLRenderer({ antialias: true });
  renderer.xr.enabled = true;
  // The Galaxy XR's native eye buffers are huge: at scale 1 VR ran at 24-35 fps although JS needed ~3 ms per
  // frame (GPU-bound, headset log 2026-10-08). 0.75 renders ~56 % of the pixels; the 1080p video stays sharp.
  renderer.xr.setFramebufferScaleFactor(0.75);
  renderer.xr.setFoveation(1);
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
  let cam = cameraModel ?? CameraModel.pinhole(vfovDeg);
  // Shown instead of the video while no camera frame has arrived, so "black" is never ambiguous.
  const noVideoTex = canvasTexture(1024, 576, (ctx, w, h) => {
    ctx.fillStyle = "#1a1a1f"; ctx.fillRect(0, 0, w, h);
    ctx.fillStyle = "#ff9500"; ctx.font = "bold 56px sans-serif"; ctx.textAlign = "center";
    ctx.fillText("No camera image yet", w / 2, h / 2 - 30);
    ctx.fillStyle = "#bbb"; ctx.font = "32px sans-serif";
    ctx.fillText("Robot Mac: camera permission for the app running the daemon?", w / 2, h / 2 + 40);
  });
  // The video is shown on a piece of sphere around the eye (radius distM): every grid point of the image sits in
  // exactly the direction its pixel sees, computed from the camera model (camera.js). That is the projection
  // and the lens undistortion in one step, and it is the grid the depth extension (WP2) would deform.
  // transparent only for the draw order: the live video is drawn after the room panorama (always on top of it)
  const screenMat = new THREE.MeshBasicMaterial({ map: noVideoTex, transparent: true });
  const screen = new THREE.Mesh(screenGeometry(cam, distM), screenMat);
  screen.renderOrder = 5;
  // Dark rounded bezel with a soft glow behind the video: the window stays findable even without video.
  const bezel = new THREE.Mesh(screenGeometry(cam, distM + 0.02, 1.04, 1.07), new THREE.MeshBasicMaterial({
    transparent: true, depthWrite: false,
    map: canvasTexture(1040, 620, (ctx, w, h) => {
      ctx.shadowColor = "rgba(255,149,0,0.45)"; ctx.shadowBlur = 40;
      ctx.fillStyle = "#12141c"; ctx.beginPath(); ctx.roundRect(24, 24, w - 48, h - 48, 26); ctx.fill();
      ctx.shadowBlur = 0; ctx.strokeStyle = "rgba(255,255,255,0.18)"; ctx.lineWidth = 3; ctx.stroke();
    }),
  }));
  bezel.renderOrder = 4;
  screen.add(bezel);
  const robotView = new THREE.Group();
  robotView.add(screen);
  scene.add(robotView);

  // ---- room panorama (room scan, WP2): frames Reachy took while looking around, world-locked in the room frame
  // (the app turns it with the speaker-following base, like the live window). Turn your head and the room is
  // there at once; only what moves (people) waits for the live video, which is drawn on top. Each patch is the
  // video sphere of one frame at the robot pose it was taken with; with metric depth from the laptop
  // (backend depth.py) the patch becomes 3D geometry, so moving the head gives parallax. Soft edges blend the
  // overlaps; the newest frame lies on top.
  const ROOM_R = distM + 0.3;   // without depth: just behind the live video sphere
  const room = new THREE.Group();
  scene.add(room);
  const roomPatches = new Map();   // key -> { mesh, depth }
  let roomSeq = 0, roomOn = true;
  let worldViewOn = false, frameSeq = 0;   // hooks for Dominic's world view (worldview.js), see the API below
  const feather = canvasTexture(256, 256, (ctx, w, h) => {
    ctx.fillStyle = "#000"; ctx.fillRect(0, 0, w, h);
    ctx.filter = "blur(10px)"; ctx.fillStyle = "#fff"; ctx.fillRect(14, 14, w - 28, h - 28);
  });
  feather.colorSpace = THREE.NoColorSpace;
  const roomGeometry = (depth) => (depth ? depthGeometry(cam, depth) : screenGeometry(cam, ROOM_R));
  function updateRoomLook() {
    const has = roomOn && roomPatches.size > 0;
    room.visible = roomOn;
    floor.visible = !has && !worldViewOn;      // the scanned room replaces the virtual floor ...
    bezel.visible = !has;      // ... and the live video blends into it without a frame
  }

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
      if (video.readyState >= video.HAVE_CURRENT_DATA && video.videoWidth > 0) { videoTex.needsUpdate = true; map = videoTex; frameSeq++; }
      source.update();
    } else {
      if (source.update()) { canvasTex.needsUpdate = true; frameSeq++; }
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

    /** Copy of the current camera frame (canvas w x h) for the room scan, or null without video. */
    captureFrame(w = 960, h = 540) {
      const src = source.mode !== "direct" && source.hasFrame ? source.canvas
        : video.readyState >= video.HAVE_CURRENT_DATA && video.videoWidth ? video : null;
      if (!src) return null;
      const c = document.createElement("canvas");
      c.width = w; c.height = h;
      c.getContext("2d").drawImage(src, 0, 0, w, h);
      return c;
    },

    /** Add or replace a room patch: image (canvas), robot camera orientation q {x,y,z,w} in the room frame,
     *  depth grid (metres, 49 x 28) or null. */
    setRoomPatch(key, image, q, depth = null) {
      let p = roomPatches.get(key);
      if (!p) {
        const mesh = new THREE.Mesh(roomGeometry(depth), new THREE.MeshBasicMaterial({
          transparent: true, depthWrite: false, alphaMap: feather, side: THREE.DoubleSide }));
        p = { mesh, depth };
        roomPatches.set(key, p);
        room.add(mesh);
      } else if (p.depth !== depth) {
        p.mesh.geometry.dispose(); p.mesh.geometry = roomGeometry(depth); p.depth = depth;
      }
      const old = p.mesh.material.map;
      const tex = new THREE.CanvasTexture(image);
      tex.colorSpace = THREE.SRGBColorSpace; tex.minFilter = THREE.LinearFilter; tex.generateMipmaps = false;
      p.mesh.material.map = tex; p.mesh.material.needsUpdate = true;
      old?.dispose();
      p.mesh.quaternion.set(q.x, q.y, q.z, q.w);
      p.mesh.renderOrder = 1 + (++roomSeq % 1000) * 1e-4;   // newest on top
      updateRoomLook();
    },

    /** Depth for an existing patch arrived (or null: back to the flat sphere). */
    setRoomPatchDepth(key, depth) {
      const p = roomPatches.get(key);
      if (!p) return;
      p.mesh.geometry.dispose(); p.mesh.geometry = roomGeometry(depth); p.depth = depth;
    },

    /** The room frame (scanned panorama) in the XR world, {x,y,z,w}. */
    setRoomFrame(q) { room.quaternion.set(q.x, q.y, q.z, q.w); },
    setRoomVisible(on) { roomOn = on; updateRoomLook(); },
    clearRoom() {
      for (const p of roomPatches.values()) { p.mesh.removeFromParent(); p.mesh.geometry.dispose(); p.mesh.material.map?.dispose(); p.mesh.material.dispose(); }
      roomPatches.clear(); updateRoomLook();
    },
    get roomInfo() { let d = 0; for (const p of roomPatches.values()) if (p.depth) d++; return { patches: roomPatches.size, depth: d, visible: roomOn }; },

    /** Use another camera model (calibration loaded or switched): rebuilds the video sphere. */
    setCamera(c) {
      cam = c;
      screen.geometry.dispose(); screen.geometry = screenGeometry(cam, distM);
      bezel.geometry.dispose(); bezel.geometry = screenGeometry(cam, distM + 0.02, 1.04, 1.07);
      for (const p of roomPatches.values()) { p.mesh.geometry.dispose(); p.mesh.geometry = roomGeometry(p.depth); }
      log(`camera model: ${cam.name}, ${cam.hfovDeg.toFixed(1)}° x ${cam.vfovDeg.toFixed(1)}°`);
    },

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
    three: { scene, robotView, renderer },

    // ---- hooks for Dominic's world view (worldview.js / worldmode.js); they change nothing while it is off
    /** Texture of the live camera frame (null while there is none). */
    get videoTexture() { return screenMat.map !== noVideoTex ? screenMat.map : null; },
    /** Counts the camera frames put on the live texture (a new frame = a new number). */
    get videoFrameSeq() { return frameSeq; },
    /** World view on: it draws the live picture itself, so the video window (and its bezel) and the floor hide. */
    setWorldView(on) {
      if (on === worldViewOn) return;
      worldViewOn = on; screen.visible = !on; updateRoomLook();
    },

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

/**
 * Piece of sphere (radius R, centred on the eye) carrying the video: an N x M grid over the image, each vertex in
 * the direction its pixel sees (camera model, lens distortion included), uv = the pixel. growX/growY > 1 widens it
 * around the optical axis (the bezel behind the video). Cells the lens model cannot reach are left out.
 * Three.js frame: x right, y up, z back; camera frame: x right, y down, z forward.
 */
function screenGeometry(cam, R, growX = 1, growY = 1, N = 48, M = 27) {
  const pos = [], uv = [], idx = [], ok = [];
  let guess = null;
  for (let j = 0; j <= M; j++) {
    for (let i = 0; i <= N; i++) {
      const u = i / N, v = j / M;
      let d = cam.unproject(u, v, guess);
      ok.push(!!d);
      if (!d) d = [0, 0, 1];
      else guess = [d[0] / d[2], d[1] / d[2]];
      if (growX !== 1 || growY !== 1) {
        const yaw = Math.atan2(d[0], d[2]) * growX, pitch = Math.atan2(d[1], Math.hypot(d[0], d[2])) * growY;
        d = [Math.sin(yaw) * Math.cos(pitch), Math.sin(pitch), Math.cos(yaw) * Math.cos(pitch)];
      }
      pos.push(d[0] * R, -d[1] * R, -d[2] * R);
      uv.push(u, 1 - v);
    }
    guess = null;
  }
  const at = (i, j) => j * (N + 1) + i;
  for (let j = 0; j < M; j++) {
    for (let i = 0; i < N; i++) {
      const a = at(i, j), b = at(i + 1, j), c = at(i, j + 1), e = at(i + 1, j + 1);
      if (ok[a] && ok[b] && ok[c] && ok[e]) idx.push(a, c, b, b, c, e);
    }
  }
  const g = new THREE.BufferGeometry();
  g.setAttribute("position", new THREE.Float32BufferAttribute(pos, 3));
  g.setAttribute("uv", new THREE.Float32BufferAttribute(uv, 2));
  g.setIndex(idx);
  g.computeBoundingSphere();
  return g;
}

/**
 * Room patch with metric depth: the same grid as screenGeometry, each vertex at its measured distance along its
 * pixel's ray (depth = metres along the optical axis, (N+1) x (M+1) values, row-major). Cells across a depth
 * jump (> 25 %, e.g. a person in front of a wall) are left out instead of stretched: an honest hole beats a
 * rubber sheet when the head moves (disocclusion).
 */
function depthGeometry(cam, depth, N = 48, M = 27) {
  const pos = [], uv = [], idx = [], ok = [], dd = [];
  let guess = null;
  for (let j = 0; j <= M; j++) {
    for (let i = 0; i <= N; i++) {
      const d = cam.unproject(i / N, j / M, guess);
      const z = Math.max(0.3, Math.min(15, depth[j * (N + 1) + i] ?? 0));
      ok.push(!!d && depth[j * (N + 1) + i] > 0);
      dd.push(z);
      const [X, Y, Z] = d ?? [0, 0, 1];
      if (d) guess = [X / Z, Y / Z];
      const r = z / Math.max(0.05, Z);   // along the ray to depth z
      pos.push(X * r, -Y * r, -Z * r);
      uv.push(i / N, 1 - j / M);
    }
    guess = null;
  }
  const at = (i, j) => j * (N + 1) + i;
  for (let j = 0; j < M; j++) {
    for (let i = 0; i < N; i++) {
      const q = [at(i, j), at(i + 1, j), at(i, j + 1), at(i + 1, j + 1)];
      if (!q.every((k) => ok[k])) continue;
      const zs = q.map((k) => dd[k]);
      if (Math.max(...zs) / Math.min(...zs) > 1.25) continue;
      idx.push(q[0], q[2], q[1], q[1], q[2], q[3]);
    }
  }
  const g = new THREE.BufferGeometry();
  g.setAttribute("position", new THREE.Float32BufferAttribute(pos, 3));
  g.setAttribute("uv", new THREE.Float32BufferAttribute(uv, 2));
  g.setIndex(idx);
  g.computeBoundingSphere();
  return g;
}
