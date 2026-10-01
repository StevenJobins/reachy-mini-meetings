// Rendering only (three.js + WebXR). Gets quaternions as plain {x, y, z, w}; no robot or pose logic here.
//
// The camera image is a window anchored in the room at the robot's MEASURED head orientation
// (rotation-only reprojection): turn your head and the image stays put until the robot has followed.
// The window has the camera's field of view, so a flat plane matches the pinhole image exactly.

import * as THREE from "three";

export function createScene({ video, vfovDeg, distM, statusText, onHeadsetPose, onSelect, onEnd }) {
  const renderer = new THREE.WebGLRenderer({ antialias: true });
  renderer.xr.enabled = true;
  renderer.xr.setReferenceSpaceType("local");
  const scene = new THREE.Scene();
  scene.background = new THREE.Color(0x101014);
  const camera = new THREE.PerspectiveCamera(70, 1, 0.05, 100);
  scene.add(camera);

  const grid = new THREE.GridHelper(20, 40, 0x335533, 0x222822);   // floor: a stable room to stand in
  grid.position.y = -1.4;
  scene.add(grid);

  // Plain Texture updated every XR frame. THREE.VideoTexture waits for requestVideoFrameCallback,
  // which can stop firing on Android while an immersive session hides the page.
  const videoTex = new THREE.Texture(video);
  videoTex.colorSpace = THREE.SRGBColorSpace;
  videoTex.minFilter = THREE.LinearFilter;
  videoTex.generateMipmaps = false;
  const screenH = 2 * distM * Math.tan(vfovDeg / 2 * Math.PI / 180);
  const screen = new THREE.Mesh(new THREE.PlaneGeometry(1, 1), new THREE.MeshBasicMaterial({ map: videoTex }));
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
    statusText().split("\n").forEach((l, i) => hudCtx.fillText(l, 12, 32 + i * 34));
    hudTex.needsUpdate = true;
  }, 200);

  const target = new THREE.Quaternion();
  let haveTarget = false;
  renderer.setAnimationLoop((now, frame) => {
    if (frame) {
      const pose = frame.getViewerPose(renderer.xr.getReferenceSpace());
      if (pose) onHeadsetPose(pose.transform.orientation, now);
    }
    if (video.readyState >= video.HAVE_CURRENT_DATA) videoTex.needsUpdate = true;
    if (haveTarget) robotView.quaternion.slerp(target, 0.5);   // pose stream ~30 Hz -> smooth at display rate
    renderer.render(scene, camera);
  });

  return {
    async xrSupported() {
      return !!navigator.xr && await navigator.xr.isSessionSupported("immersive-vr").catch(() => false);
    },

    /** Must be called from a user gesture (button tap). */
    async enterVR() {
      const session = await navigator.xr.requestSession("immersive-vr", { optionalFeatures: ["local"] });
      session.addEventListener("select", onSelect);   // pinch / trigger
      session.addEventListener("end", onEnd);
      await renderer.xr.setSession(session);
    },

    /** Robot head orientation in the XR world, as {x, y, z, w}. */
    setRobotHead(q) { target.set(q.x, q.y, q.z, q.w); haveTarget = true; },

    /** For overlays (speech bubbles): the room, and the group that moves with the video window. */
    three: { scene, robotView },
  };
}
