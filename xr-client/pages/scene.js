// Rendering only (three.js + WebXR). Gets quaternions as plain {x, y, z, w}; no robot or pose logic here.
//
// The camera image is a window anchored in the room at the robot's MEASURED head orientation
// (rotation-only reprojection): turn your head and the image stays put until the robot has followed.
// The window has the camera's field of view, so a flat plane matches the pinhole image exactly.

import * as THREE from "three";
import { createVideoSource } from "./videosource.js";

export function createScene({ video, vfovDeg, distM, statusText, onHeadsetPose, onSelect, onEnd, vrButtons = [], log = console.log }) {
  const renderer = new THREE.WebGLRenderer({ antialias: true });
  renderer.xr.enabled = true;
  renderer.domElement.addEventListener("webglcontextlost", () => log("ERROR webgl context lost (GPU crash / out of memory)"));
  renderer.xr.setReferenceSpaceType("local");
  const scene = new THREE.Scene();
  scene.background = new THREE.Color(0x101014);
  const camera = new THREE.PerspectiveCamera(70, 1, 0.05, 100);
  scene.add(camera);

  const grid = new THREE.GridHelper(20, 40, 0x335533, 0x222822);   // floor: a stable room to stand in
  grid.position.y = -1.4;
  scene.add(grid);

  // Camera frames: see videosource.js for why the VR window does not simply use the <video> element.
  const source = createVideoSource(video, log);
  const canvasTex = new THREE.CanvasTexture(source.canvas);   // fixed size, so texStorage2D is fine
  canvasTex.colorSpace = THREE.SRGBColorSpace;
  canvasTex.minFilter = THREE.LinearFilter;
  canvasTex.generateMipmaps = false;
  // "direct" mode only: VideoTexture is re-allocated with texImage2D on every upload (no texStorage2D),
  // so resolution changes are fine. needsUpdate is set per XR frame below, because its
  // requestVideoFrameCallback can stop firing on Android while an immersive session hides the page.
  const videoTex = new THREE.VideoTexture(video);
  videoTex.colorSpace = THREE.SRGBColorSpace;
  videoTex.minFilter = THREE.LinearFilter;
  videoTex.generateMipmaps = false;
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
  // Orange frame: the window stays findable even when no video arrives.
  const frame = new THREE.LineSegments(
    new THREE.EdgesGeometry(new THREE.PlaneGeometry(1, 1)),
    new THREE.LineBasicMaterial({ color: 0xff9500 }));
  screen.add(frame);
  const robotView = new THREE.Group();
  robotView.add(screen);
  scene.add(robotView);

  // Small head-locked status panel, low in the view.
  const hudCanvas = document.createElement("canvas");
  hudCanvas.width = 1024; hudCanvas.height = 160;
  const hudCtx = hudCanvas.getContext("2d");
  const hudTex = new THREE.CanvasTexture(hudCanvas);
  const hud = new THREE.Mesh(new THREE.PlaneGeometry(0.8, 0.125), new THREE.MeshBasicMaterial({ map: hudTex, transparent: true }));
  hud.position.set(0, -0.45, -1.2);
  camera.add(hud);
  setInterval(() => {
    hudCtx.clearRect(0, 0, hudCanvas.width, hudCanvas.height);
    hudCtx.fillStyle = "rgba(0,0,0,0.6)";
    hudCtx.fillRect(0, 0, hudCanvas.width, hudCanvas.height);
    hudCtx.fillStyle = "#7f7"; hudCtx.font = "24px monospace";
    [...statusText().split("\n"), source.stats()].forEach((l, i) => hudCtx.fillText(l, 12, 32 + i * 34));
    hudTex.needsUpdate = true;
  }, 200);

  // ---- head-locked VR buttons, row above the status panel. A label may be a function (e.g. mute state).
  const labelOf = (b) => (typeof b.label === "function" ? b.label() : b.label);
  const buttons = vrButtons.map((b, i) => {
    let text = labelOf(b);
    const draw = (hover) => (ctx, w, h) => {
      ctx.clearRect(0, 0, w, h);
      ctx.fillStyle = hover ? "#ff9500" : "rgba(40,40,46,0.92)";
      ctx.beginPath(); ctx.roundRect(4, 4, w - 8, h - 8, 28); ctx.fill();
      ctx.fillStyle = hover ? "#000" : "#eee"; ctx.font = "bold 44px sans-serif";
      ctx.textAlign = "center"; ctx.textBaseline = "middle"; ctx.fillText(text, w / 2, h / 2);
    };
    const tex = [canvasTexture(512, 128, draw(false)), canvasTexture(512, 128, draw(true))];
    const mesh = new THREE.Mesh(new THREE.PlaneGeometry(0.2, 0.05), new THREE.MeshBasicMaterial({ map: tex[0], transparent: true }));
    // Rows of 3, centred, just above the status panel.
    const perRow = 3, row = Math.floor(i / perRow), col = i % perRow;
    const inRow = Math.min(perRow, vrButtons.length - row * perRow);
    mesh.position.set((col - (inRow - 1) / 2) * 0.215, -0.25 - row * 0.06, -1.2);
    mesh.userData = {
      onClick: b.onClick, tex,
      refresh() {   // redraw both textures when the label changed
        const now = labelOf(b);
        if (now === text) return;
        text = now;
        tex.forEach((t, hover) => { draw(!!hover)(t.image.getContext("2d"), t.image.width, t.image.height); t.needsUpdate = true; });
      },
    };
    camera.add(mesh);
    return mesh;
  });

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
    return raycaster.intersectObjects(buttons, false)[0] ?? null;
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

  const target = new THREE.Quaternion();
  let haveTarget = false;
  renderer.setAnimationLoop((now, frame) => {
    if (frame) {
      const pose = frame.getViewerPose(renderer.xr.getReferenceSpace());
      if (pose) onHeadsetPose(pose.transform.orientation, now);
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
    if (frame) updatePointers(frame);
    if (haveTarget) robotView.quaternion.slerp(target, 0.5);   // pose stream ~30 Hz -> smooth at display rate
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

    /** Switch how camera frames reach the VR window (track -> canvas -> direct). Returns the new mode. */
    cycleVideo() { return source.cycle(); },

    exitVR() { renderer.xr.getSession()?.end(); },

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
