// Rendering only (three.js + WebXR). Gets quaternions as plain {x, y, z, w}; no robot or pose logic here.
//
// The camera image is a window anchored in the room at the robot's MEASURED head orientation
// (rotation-only reprojection): turn your head and the image stays put until the robot has followed.
// The window has the camera's field of view, so a flat plane matches the pinhole image exactly.

import * as THREE from "three";
import { createVideoSource } from "./videosource.js";

export function createScene({ video, vfovDeg, distM, statusText, onHeadsetPose, onSelect, onEnd, onFrame, vrButtons = [], log = console.log }) {
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
    [...statusText().split("\n"), source.stats()].forEach((l, i) => hudCtx.fillText(l, 20, 40 + i * 40));
    hudTex.needsUpdate = true;
  }, 200);

  // ---- head-locked dock below the view: glass pills with icon + label. Entries with `more: true` sit in a
  // second row that the automatic "More" button opens. label / icon / active may be functions.
  // {kind: "mic", muted: () => bool}: big round video-call style mic button left of the dock.
  const val = (v) => (typeof v === "function" ? v() : v);
  let moreOpen = false;
  const entries = [...vrButtons.filter((b) => !b.more && b.kind !== "mic"),
    { icon: () => (moreOpen ? "✕" : "⋯"), label: () => (moreOpen ? "Less" : "More"), onClick: () => { moreOpen = !moreOpen; } },
    ...vrButtons.filter((b) => b.more), ...vrButtons.filter((b) => b.kind === "mic")];
  const PW = 0.15, PH = 0.044, GAP = 0.012;
  const buttons = entries.map((b) => {
    let key = "";
    const isMic = b.kind === "mic";
    const draw = (hover) => (ctx, w, h) => {
      if (isMic) { ctx.clearRect(0, 0, w, h); return drawMicButton(ctx, w, h, b.muted(), hover, val(b.label)); }
      const active = !!val(b.active);
      ctx.clearRect(0, 0, w, h);
      ctx.fillStyle = hover ? "#ff9500" : active ? "rgba(255,149,0,0.22)" : "rgba(22,24,34,0.88)";
      ctx.beginPath(); ctx.roundRect(4, 4, w - 8, h - 8, (h - 8) / 2); ctx.fill();
      ctx.strokeStyle = hover ? "rgba(255,255,255,0.0)" : active ? "rgba(255,149,0,0.9)" : "rgba(255,255,255,0.16)";
      ctx.lineWidth = 3; ctx.stroke();
      ctx.textBaseline = "middle";
      ctx.font = "44px system-ui, 'Noto Color Emoji', sans-serif"; ctx.textAlign = "center";
      ctx.fillStyle = hover ? "#111" : "#fff";
      ctx.fillText(val(b.icon) ?? "", 62, h / 2 + 2);
      ctx.font = "600 38px system-ui, sans-serif"; ctx.textAlign = "left";
      ctx.fillStyle = hover ? "#111" : active ? "#ffb347" : "#eef0f4";
      ctx.fillText(val(b.label), 104, h / 2 + 2);
    };
    const [cw, ch, pw, ph] = isMic ? [256, 300, 0.085, 0.1] : [420, 124, PW, PH];
    const tex = [canvasTexture(cw, ch, draw(false)), canvasTexture(cw, ch, draw(true))];
    const mesh = new THREE.Mesh(new THREE.PlaneGeometry(pw, ph), new THREE.MeshBasicMaterial({ map: tex[0], transparent: true }));
    mesh.userData = {
      onClick: b.onClick, tex, more: !!b.more, mic: isMic,
      refresh() {   // redraw when label / icon / active / mute state changed
        const now = `${val(b.icon)}|${val(b.label)}|${!!val(b.active)}|${b.muted?.() ?? ""}`;
        if (now === key) return;
        key = now;
        tex.forEach((t, hover) => { draw(!!hover)(t.image.getContext("2d"), t.image.width, t.image.height); t.needsUpdate = true; });
      },
    };
    mesh.userData.refresh();
    camera.add(mesh);
    return mesh;
  });
  function layoutDock() {
    const main = buttons.filter((m) => !m.userData.more && !m.userData.mic);
    const rows = [main, moreOpen ? buttons.filter((m) => m.userData.more) : []];
    for (const m of buttons) m.visible = false;
    rows.forEach((row, r) => row.forEach((m, i) => {
      m.visible = true;
      m.position.set((i - (row.length - 1) / 2) * (PW + GAP), -0.33 - r * (PH + GAP), -1.1);
    }));
    for (const m of buttons.filter((b) => b.userData.mic)) {   // round mic button left of the first row
      m.visible = true;
      m.position.set(-((main.length + 1) / 2) * (PW + GAP) - 0.03, -0.33 - 0.025, -1.1);
    }
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
    camera.updateMatrixWorld(true);
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
    const pose = e.frame.getPose(e.inputSource.targetRaySpace, renderer.xr.getReferenceSpace());
    const hit = pose && hitButton(pose);
    if (hit) hit.object.userData.onClick();
    else onSelect();
  }

  // ---- desktop preview (no headset): same scene in the browser window, mouse drag = head turn
  let desktop = null;   // { yaw, pitch } while active
  const mouse = new THREE.Vector2();
  let hoveredDesktop = null;

  function desktopHit(e) {
    const r = renderer.domElement.getBoundingClientRect();
    mouse.set(((e.clientX - r.left) / r.width) * 2 - 1, -((e.clientY - r.top) / r.height) * 2 + 1);
    camera.updateMatrixWorld(true);
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
  el.addEventListener("pointerdown", (e) => { drag = { x: e.clientX, y: e.clientY, moved: false }; el.setPointerCapture(e.pointerId); });
  el.addEventListener("pointermove", (e) => {
    if (!desktop) return;
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
  renderer.setAnimationLoop((now, frame) => {
    if (frame) {
      const pose = frame.getViewerPose(renderer.xr.getReferenceSpace());
      if (pose) onHeadsetPose(pose.transform.orientation, now);
    } else if (desktop) {
      camera.quaternion.setFromEuler(new THREE.Euler(desktop.pitch, desktop.yaw, 0, "YXZ"));
      const q = camera.quaternion;
      onHeadsetPose({ x: q.x, y: q.y, z: q.z, w: q.w }, now);
      for (const b of buttons) {
        const map = b.userData.tex[b === hoveredDesktop ? 1 : 0];
        if (b.material.map !== map) { b.material.map = map; b.material.needsUpdate = true; }
      }
    }
    let map = noVideoTex;
    if (source.mode === "direct") {
      if (video.readyState >= video.HAVE_CURRENT_DATA && video.videoWidth > 0) { videoTex.needsUpdate = true; map = videoTex; }
      source.update();
    } else {
      if (source.update()) canvasTex.needsUpdate = true;
      if (source.hasFrame) map = canvasTex;
    }
    if (screenMat.map !== map) { screenMat.map = map; screenMat.needsUpdate = true; }
    for (const b of buttons) b.userData.refresh();
    layoutDock();
    if (frame) updatePointers(frame);
    if (haveTarget) robotView.quaternion.slerp(target, 0.5);   // pose stream ~30 Hz -> smooth at display rate
    onFrame?.(now);
    renderer.render(scene, camera);
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
      session.addEventListener("end", onEnd);
      await renderer.xr.setSession(session);
    },

    /** One line about the camera path (for the heartbeat log). */
    videoStats() { return source.stats(); },

    /** Current camera frame for image analysis (face detection): the fixed-size frame canvas, else the <video>. */
    videoFrame() { return source.mode !== "direct" && source.hasFrame ? source.canvas : video; },

    /** Switch how camera frames reach the VR window (track -> canvas -> direct). Returns the new mode. */
    cycleVideo() { return source.cycle(); },

    /** Debug panel (status lines) at the top of the view. */
    toggleDebug() { hud.visible = !hud.visible; },

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

    /** Robot head orientation in the XR world, as {x, y, z, w}. */
    setRobotHead(q) { target.set(q.x, q.y, q.z, q.w); haveTarget = true; },

    /** For overlays (speech bubbles): the room, and the group that moves with the video window. */
    three: { scene, robotView },
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
  ctx.fillStyle = "#eee"; ctx.font = "bold 30px sans-serif"; ctx.textAlign = "center"; ctx.textBaseline = "alphabetic";
  ctx.fillText(label, cx, h - 4);
}
