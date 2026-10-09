// World view (Dominic's view mode, see xr-client/README.md "World view"): ONE continuous, world-locked room panorama
// that grows from the robot camera, with the live picture blended into it. Rendering only (three.js); when to paint
// and with which pose is decided by worldmode.js.
//
// Panorama = two equirectangular render targets in the robot's world frame (lon = yaw, + = left; lat = up):
//   colour: half float linear (RGBA8 sRGB fallback), the picture in the exposure of the first frame (see gain)
//   meta:   R = quality of the source that wrote the texel, G/B = when (16 bit, 0.5 s steps), A = coverage
// paint(frame, pose): for every panorama texel in the frame's footprint: direction -> camera frame -> lens model
// (same maths as camera.js) -> sample the frame. Per texel the BEST source wins (no alpha stacking, no ghosts):
//   quality = centrality in the frame (0 at its border, 1 in the middle) * stillness,
//   old quality decays with age (recency), the new frame replaces where it is better, with a narrow soft
//   transition where both are equal (a seam half-way between two frame centres, never a frame border).
// Exposure: the camera's auto exposure differs between frames, so every frame is scaled by a gain measured from its
// overlap with the panorama (probe(): 32 x 18 cells, median ratio, read back asynchronously, no GPU stall).
// Display: a big sphere; per pixel the stored panorama (greying and dimming with age) and the LIVE frame at its
// capture pose on top, faded out over the outer ~14 % of the frame: no rectangle, no frame border anywhere.

import * as THREE from "three";
import { worldToCamRows } from "./worldpolicy.js";

const DEG = Math.PI / 180;
const TIME_STEP_S = 0.5;          // meta time code resolution (16 bit -> 9 h)
const PROBE_W = 32, PROBE_H = 18;

// Camera model in GLSL (camera.js: OpenCV rational + thin prism, x right, y down, z forward; uv origin top left).
const GLSL_CAMERA = /* glsl */ `
  uniform vec4 uK;                 // fx / width, fy / height, cx / width, cy / height
  uniform vec4 uD0, uD1, uD2;      // k1 k2 p1 p2 | k3 k4 k5 k6 | s1 s2 s3 s4
  uniform float uR2max;            // largest undistorted r^2 inside the picture (the polynomial folds back beyond)
  // direction in the camera frame -> image uv (0..1, top left), or (-1, -1) outside the lens model
  vec2 projectCam(vec3 d) {
    if (d.z < 0.02) return vec2(-1.0);
    vec2 p = d.xy / d.z;
    float r2 = dot(p, p);
    if (r2 > uR2max) return vec2(-1.0);
    float r4 = r2 * r2, r6 = r4 * r2;
    float radial = (1.0 + uD0.x * r2 + uD0.y * r4 + uD1.x * r6) / (1.0 + uD1.y * r2 + uD1.z * r4 + uD1.w * r6);
    vec2 q = vec2(
      p.x * radial + 2.0 * uD0.z * p.x * p.y + uD0.w * (r2 + 2.0 * p.x * p.x) + uD2.x * r2 + uD2.y * r4,
      p.y * radial + uD0.z * (r2 + 2.0 * p.y * p.y) + 2.0 * uD0.w * p.x * p.y + uD2.z * r2 + uD2.w * r4);
    return vec2(uK.x * q.x + uK.z, uK.y * q.y + uK.w);
  }
  bool inImage(vec2 uv) { return uv.x >= 0.0 && uv.x <= 1.0 && uv.y >= 0.0 && uv.y <= 1.0; }
  // 0 at the picture border, 1 in the middle
  float centrality(vec2 uv) { vec2 e = min(uv, 1.0 - uv) * 2.0; return clamp(min(e.x, e.y), 0.0, 1.0); }
  // panorama texture coords (s left -> right, t bottom -> top) <-> robot world direction (x fwd, y left, z up)
  vec3 dirFromST(vec2 st) {
    float lon = (0.5 - st.x) * 6.28318530718, lat = (st.y - 0.5) * 3.14159265359;
    return vec3(cos(lat) * cos(lon), cos(lat) * sin(lon), sin(lat));
  }
  vec2 stFromDir(vec3 d) {
    return vec2(0.5 - atan(d.y, d.x) / 6.28318530718, 0.5 + asin(clamp(d.z, -1.0, 1.0)) / 3.14159265359);
  }
  float lum(vec3 c) { return dot(c, vec3(0.2126, 0.7152, 0.0722)); }
`;

const FULLSCREEN_VS = /* glsl */ `void main() { gl_Position = vec4(position.xy, 0.0, 1.0); }`;

/** Robot world direction -> camera frame as a THREE.Matrix3 (worldpolicy.js worldToCamRows). */
export function worldToCamMatrix(roll, pitch, yaw, out = new THREE.Matrix3()) { return out.set(...worldToCamRows(roll, pitch, yaw)); }

export function createWorldView({ renderer, parent, camera, radius = 4, width = 2048, exposure = true, log = console.log }) {
  const height = width / 2;
  // Colour in half float (linear light) where the GPU can render to it: the panorama is stored in the exposure of
  // its first frame, and a frame shot darker must be scaled UP, which an 8-bit target would clip at 1.0 (synthetic
  // test with 0.7-1.3 auto exposure: compensation made the seams worse in RGBA8, see README).
  const gl0 = renderer.getContext();
  const halfFloat = !!(gl0.getExtension("EXT_color_buffer_half_float") || gl0.getExtension("EXT_color_buffer_float"));
  const rtOpts = (isColor) => ({
    depthBuffer: false, stencilBuffer: false, generateMipmaps: false,
    magFilter: isColor ? THREE.LinearFilter : THREE.NearestFilter, minFilter: isColor ? THREE.LinearFilter : THREE.NearestFilter,
    wrapS: THREE.RepeatWrapping, wrapT: THREE.ClampToEdgeWrapping,
    type: isColor && halfFloat ? THREE.HalfFloatType : THREE.UnsignedByteType,
    colorSpace: isColor && !halfFloat ? THREE.SRGBColorSpace : THREE.NoColorSpace,
  });
  log(`world view: panorama colour ${halfFloat ? "half float" : "RGBA8 sRGB (no float render targets)"}`);
  const color = [new THREE.WebGLRenderTarget(width, height, rtOpts(true)), new THREE.WebGLRenderTarget(width, height, rtOpts(true))];
  const meta = [new THREE.WebGLRenderTarget(width, height, rtOpts(false)), new THREE.WebGLRenderTarget(width, height, rtOpts(false))];
  const orthoCam = new THREE.OrthographicCamera(-1, 1, 1, -1, 0, 1);
  const passScene = new THREE.Scene();

  const camUniforms = () => ({ uK: { value: new THREE.Vector4() }, uD0: { value: new THREE.Vector4() },
    uD1: { value: new THREE.Vector4() }, uD2: { value: new THREE.Vector4() }, uR2max: { value: 1 } });
  function setCamUniforms(u, cam) {
    const d = cam.dist;
    u.uK.value.set(cam.fx / cam.width, cam.fy / cam.height, cam.cx / cam.width, cam.cy / cam.height);
    u.uD0.value.set(d[0], d[1], d[2], d[3]); u.uD1.value.set(d[4], d[5], d[6], d[7]); u.uD2.value.set(d[8], d[9], d[10], d[11]);
    u.uR2max.value = r2max;
  }

  // ---- paint pass (colour or meta), drawn only over the frame's footprint box
  const paintUniforms = {
    ...camUniforms(),
    uPrevColor: { value: color[0].texture }, uPrevMeta: { value: meta[0].texture }, uFrame: { value: null },
    uW2C: { value: new THREE.Matrix3() }, uSize: { value: new THREE.Vector2(width, height) },
    uGain: { value: 1 }, uStill: { value: 1 }, uNowCode: { value: 0 }, uTauS: { value: 45 },
  };
  const paintFS = (out) => /* glsl */ `
    ${GLSL_CAMERA}
    uniform sampler2D uPrevColor, uPrevMeta, uFrame;
    uniform mat3 uW2C;
    uniform vec2 uSize;
    uniform float uGain, uStill, uNowCode, uTauS;
    void main() {
      vec2 st = gl_FragCoord.xy / uSize;
      vec4 oc = texture2D(uPrevColor, st), om = texture2D(uPrevMeta, st);
      vec2 uv = projectCam(uW2C * dirFromST(st));
      float qNew = inImage(uv) ? centrality(uv) * uStill : 0.0;
      float oldCode = floor(om.g * 255.0 + 0.5) * 256.0 + floor(om.b * 255.0 + 0.5);
      float ageS = max(0.0, uNowCode - oldCode) * ${TIME_STEP_S.toFixed(2)};
      float qOld = om.a > 0.0 ? om.r * exp(-ageS / uTauS) : 0.0;
      // best source wins; soft only where both are about equally good (a seam half-way between frame centres)
      float b = qNew <= 0.0 ? 0.0 : smoothstep(0.75, 1.33, qNew / max(qOld, 1e-4));
      ${out === "color" ? `
      vec3 c = texture2D(uFrame, vec2(uv.x, 1.0 - uv.y)).rgb * uGain;
      gl_FragColor = vec4(mix(oc.rgb, c, b), 1.0);` : `
      float cov = max(om.a, b > 0.0 ? smoothstep(0.0, 0.06, qNew) : 0.0);
      gl_FragColor = b > 0.5 ? vec4(qNew, floor(uNowCode / 256.0) / 255.0, mod(uNowCode, 256.0) / 255.0, cov) : vec4(om.rgb, cov);`}
    }`;
  const mk = (fs, uniforms) => new THREE.ShaderMaterial({ uniforms, vertexShader: FULLSCREEN_VS, fragmentShader: fs, depthTest: false, depthWrite: false, blending: THREE.NoBlending });
  const paintColorMat = mk(paintFS("color"), paintUniforms);
  const paintMetaMat = mk(paintFS("meta"), paintUniforms);
  const copyUniforms = { uSrc: { value: null }, uSize: paintUniforms.uSize };
  const copyMat = mk(/* glsl */ `uniform sampler2D uSrc; uniform vec2 uSize; void main() { gl_FragColor = texture2D(uSrc, gl_FragCoord.xy / uSize); }`, copyUniforms);
  const boxGeo = new THREE.BufferGeometry();
  boxGeo.setAttribute("position", new THREE.BufferAttribute(new Float32Array(12 * 3), 3));
  const boxMesh = new THREE.Mesh(boxGeo, paintColorMat);
  boxMesh.frustumCulled = false;
  passScene.add(boxMesh);

  // ---- gain probe: frame luminance vs panorama luminance on a 32 x 18 grid over the picture
  const probeRT = new THREE.WebGLRenderTarget(PROBE_W, PROBE_H, { depthBuffer: false, minFilter: THREE.NearestFilter, magFilter: THREE.NearestFilter });
  const probeDirs = new THREE.DataTexture(new Float32Array(PROBE_W * PROBE_H * 4), PROBE_W, PROBE_H, THREE.RGBAFormat, THREE.FloatType);
  const probeUniforms = { ...camUniforms(), uDirs: { value: probeDirs }, uFrame: { value: null }, uColor: { value: color[0].texture },
    uMeta: { value: meta[0].texture }, uC2W: { value: new THREE.Matrix3() }, uProbeSize: { value: new THREE.Vector2(PROBE_W, PROBE_H) }, uNowCode: paintUniforms.uNowCode };
  const probeMat = mk(/* glsl */ `
    ${GLSL_CAMERA}
    uniform sampler2D uDirs, uFrame, uColor, uMeta;
    uniform mat3 uC2W;
    uniform vec2 uProbeSize;
    uniform float uNowCode;
    void main() {
      vec2 cell = gl_FragCoord.xy / uProbeSize;            // cell centre, x right, y up (row 0 = bottom of the picture)
      vec4 dc = texture2D(uDirs, cell);
      if (dc.w < 0.5) { gl_FragColor = vec4(0.0); return; }
      vec2 uv = vec2(cell.x * 0.8 + 0.1, (1.0 - cell.y) * 0.8 + 0.1);   // inner 80 % of the picture
      vec2 st = stFromDir(uC2W * dc.xyz);
      vec4 m = texture2D(uMeta, st);
      float code = floor(m.g * 255.0 + 0.5) * 256.0 + floor(m.b * 255.0 + 0.5);
      bool valid = m.a > 0.5 && (uNowCode - code) * ${TIME_STEP_S.toFixed(2)} < 120.0;
      // the same small area (~1.4 deg) in both: 3 x 3 samples in the frame and around the direction in the panorama
      vec2 o = vec2(0.008, 0.014);
      float dl = 0.70 / 360.0 / max(0.2, cos((st.y - 0.5) * 3.14159265)), dt = 0.70 / 180.0;   // ~0.7 deg (= 0.008 of 89 deg)
      float f = 0.0, p = 0.0;
      for (int i = -1; i <= 1; i++) for (int j = -1; j <= 1; j++) {
        f += lum(texture2D(uFrame, vec2(uv.x + float(i) * o.x, 1.0 - uv.y - float(j) * o.y)).rgb);
        vec2 sj = st + vec2(float(i) * dl, -float(j) * dt);
        p += lum(texture2D(uColor, sj).rgb);
        if (texture2D(uMeta, sj).a < 0.9) valid = false;   // all 9 samples on stored panorama
      }
      f /= 9.0; p /= 9.0;
      if (!valid) p = 0.0;
      // 16 bit each (8 bit linear luminance quantises dark areas into useless ratios)
      float f16 = floor(clamp(f, 0.0, 1.0) * 65535.0 + 0.5), p16 = floor(clamp(p / 4.0, 0.0, 1.0) * 65535.0 + 0.5);   // stored values may exceed 1 (half float)
      gl_FragColor = vec4(floor(f16 / 256.0), mod(f16, 256.0), floor(p16 / 256.0), mod(p16, 256.0)) / 255.0;
    }`, probeUniforms);
  const probeQuad = new THREE.Mesh(new THREE.PlaneGeometry(2, 2), probeMat);
  probeQuad.frustumCulled = false;
  const probeScene = new THREE.Scene();
  probeScene.add(probeQuad);
  const gl = renderer.getContext();
  const isGL2 = typeof WebGL2RenderingContext !== "undefined" && gl instanceof WebGL2RenderingContext;
  let pbo = null, fence = null, probePending = false;
  const probeBuf = new Uint8Array(PROBE_W * PROBE_H * 4);

  // ---- display sphere: stored panorama + live frame, one shader, no rectangles
  const displayUniforms = {
    ...camUniforms(),
    uColor: { value: color[0].texture }, uMeta: { value: meta[0].texture }, uLive: { value: null }, uLiveOn: { value: 0 },
    uLiveW2C: { value: new THREE.Matrix3() }, uNowCode: { value: 0 }, uInvGain: { value: 1 }, uFeather: { value: 0.14 },
    uFreshS: { value: 15 }, uStaleS: { value: 150 }, uDim: { value: 0.72 }, uOpacity: { value: 1 },
  };
  const displayMat = new THREE.ShaderMaterial({
    uniforms: displayUniforms, side: THREE.BackSide, transparent: true, depthWrite: false, depthTest: false,
    vertexShader: /* glsl */ `varying vec3 vLocal; void main() { vLocal = position; gl_Position = projectionMatrix * modelViewMatrix * vec4(position, 1.0); }`,
    fragmentShader: /* glsl */ `
      ${GLSL_CAMERA}
      varying vec3 vLocal;
      uniform sampler2D uColor, uMeta, uLive;
      uniform float uLiveOn, uNowCode, uInvGain, uFeather, uFreshS, uStaleS, uDim, uOpacity;
      uniform mat3 uLiveW2C;
      void main() {
        vec3 d = normalize(vec3(-vLocal.z, -vLocal.x, vLocal.y));   // three.js (x right, y up, z back) -> robot world
        vec2 st = stFromDir(d);
        vec4 m = texture2D(uMeta, st);
        float code = floor(m.g * 255.0 + 0.5) * 256.0 + floor(m.b * 255.0 + 0.5);
        float age = max(0.0, uNowCode - code) * ${TIME_STEP_S.toFixed(2)};
        vec3 c = texture2D(uColor, st).rgb * uInvGain;
        float stale = smoothstep(uFreshS, uStaleS, age);
        vec3 grey = vec3(lum(c)) * vec3(0.92, 0.96, 1.06);
        c = mix(c, grey, stale) * mix(1.0, uDim, stale);
        float a = m.a;
        if (uLiveOn > 0.5) {
          vec2 uv = projectCam(uLiveW2C * d);
          if (inImage(uv)) {
            vec2 e = min(uv, 1.0 - uv);
            float wl = smoothstep(0.0, uFeather, e.x) * smoothstep(0.0, uFeather * 16.0 / 9.0, e.y);
            vec3 lc = texture2D(uLive, vec2(uv.x, 1.0 - uv.y)).rgb;
            c = mix(c, lc, wl);
            a = max(a, wl);
          }
        }
        gl_FragColor = vec4(c, a * uOpacity);
        #include <colorspace_fragment>
      }`,
  });
  const sphere = new THREE.Mesh(new THREE.SphereGeometry(radius, 96, 48), displayMat);
  sphere.renderOrder = 2;
  sphere.frustumCulled = false;
  const group = new THREE.Group();
  group.add(sphere);
  parent.add(group);

  // ---- camera-dependent precomputation: border directions (footprint box), probe directions, r^2 limit
  let cam = null, r2max = 1, border = [];
  function setCamera(c) {
    cam = c;
    border = [];
    let rmax = 0;
    const N = 16;
    for (let k = 0; k <= N; k++) {
      for (const [u, v] of [[k / N, 0], [k / N, 1], [0, k / N], [1, k / N]]) {
        const d = cam.unproject(u, v);
        if (!d) continue;
        border.push(d);
        rmax = Math.max(rmax, (d[0] * d[0] + d[1] * d[1]) / (d[2] * d[2]));
      }
    }
    r2max = rmax * 1.02;
    const data = probeDirs.image.data;
    for (let j = 0; j < PROBE_H; j++) for (let i = 0; i < PROBE_W; i++) {
      // row j = 0 is the BOTTOM of the probe target = bottom of the picture
      const u = ((i + 0.5) / PROBE_W) * 0.8 + 0.1, v = (1 - (j + 0.5) / PROBE_H) * 0.8 + 0.1;
      const d = cam.unproject(u, v);
      const o = (j * PROBE_W + i) * 4;
      if (d) { data[o] = d[0]; data[o + 1] = d[1]; data[o + 2] = d[2]; data[o + 3] = 1; } else data[o + 3] = 0;
    }
    probeDirs.needsUpdate = true;
    for (const u of [paintUniforms, probeUniforms, displayUniforms]) setCamUniforms(u, cam);
  }
  setCamera(camera);

  /** Footprint of a frame at pose -> list of [s0, s1, t0, t1] boxes in panorama texture coords (split at the seam). */
  function footprint(m) {
    const mt = m.clone().transpose();   // camera -> world
    const e = mt.elements;              // column-major
    const toWorld = (d) => [e[0] * d[0] + e[3] * d[1] + e[6] * d[2], e[1] * d[0] + e[4] * d[1] + e[7] * d[2], e[2] * d[0] + e[5] * d[1] + e[8] * d[2]];
    const c = toWorld([0, 0, 1]);
    const lonC = Math.atan2(c[1], c[0]);
    let lo = Infinity, hi = -Infinity, latLo = Infinity, latHi = -Infinity;
    for (const d of border) {
      const w = toWorld(d);
      let lon = Math.atan2(w[1], w[0]) - lonC;
      lon = Math.atan2(Math.sin(lon), Math.cos(lon));
      const lat = Math.asin(Math.max(-1, Math.min(1, w[2])));
      lo = Math.min(lo, lon); hi = Math.max(hi, lon); latLo = Math.min(latLo, lat); latHi = Math.max(latHi, lat);
    }
    const pad = 1.5 * DEG;
    // a pole inside the picture: the footprint covers all longitudes beyond the border's latitude
    const pole = (z) => { const v = m.elements; const d = [v[6] * z, v[7] * z, v[8] * z]; const p = cam.project(d); return p && p[0] >= 0 && p[0] <= 1 && p[1] >= 0 && p[1] <= 1; };
    let t0 = (latLo - pad) / Math.PI + 0.5, t1 = (latHi + pad) / Math.PI + 0.5;
    if (pole(1)) { t1 = 1; lo = -Math.PI; hi = Math.PI; }
    if (pole(-1)) { t0 = 0; lo = -Math.PI; hi = Math.PI; }
    t0 = Math.max(0, t0); t1 = Math.min(1, t1);
    if (hi - lo + 2 * pad >= 2 * Math.PI) return [[0, 1, t0, t1]];
    // s = 0.5 - lon / 2pi: larger lon (left) = smaller s
    let s0 = 0.5 - (lonC + hi + pad) / (2 * Math.PI), s1 = 0.5 - (lonC + lo - pad) / (2 * Math.PI);
    const k = Math.floor(s0);
    s0 -= k; s1 -= k;
    return s1 <= 1 ? [[s0, s1, t0, t1]] : [[s0, 1, t0, t1], [0, s1 - 1, t0, t1]];
  }

  function setBoxes(boxes) {
    const p = boxGeo.attributes.position.array;
    p.fill(0);
    boxes.forEach(([s0, s1, t0, t1], i) => {
      const x0 = 2 * s0 - 1, x1 = 2 * s1 - 1, y0 = 2 * t0 - 1, y1 = 2 * t1 - 1;
      p.set([x0, y0, 0, x1, y0, 0, x1, y1, 0, x0, y0, 0, x1, y1, 0, x0, y1, 0], i * 18);
    });
    boxGeo.attributes.position.needsUpdate = true;
    boxGeo.setDrawRange(0, boxes.length * 6);
  }

  /** Offscreen render that also works inside a WebXR frame (three would otherwise use the XR camera / target). */
  function offscreen(target, sceneToDraw) {
    const prevTarget = renderer.getRenderTarget(), prevXR = renderer.xr.enabled, prevClear = renderer.autoClear;
    renderer.xr.enabled = false; renderer.autoClear = false;
    renderer.setRenderTarget(target);
    renderer.render(sceneToDraw, orthoCam);
    renderer.setRenderTarget(prevTarget);
    renderer.xr.enabled = prevXR; renderer.autoClear = prevClear;
  }

  let lastProbe = null, t0S = null, nowCode = 0, gain = 1, paints = 0, lastPaintMs = 0, lastBoxFrac = 0, probes = 0, gainLog = [];
  const codeFor = (nowS) => { if (t0S == null) t0S = nowS; return Math.min(65535, Math.floor((nowS - t0S) / TIME_STEP_S)); };
  const w2c = new THREE.Matrix3(), c2w = new THREE.Matrix3();

  function readProbe() {
    if (!probePending || !isGL2) return;
    const st = gl.clientWaitSync(fence, 0, 0);
    if (st !== gl.ALREADY_SIGNALED && st !== gl.CONDITION_SATISFIED) return;
    gl.deleteSync(fence); fence = null; probePending = false;
    gl.bindBuffer(gl.PIXEL_PACK_BUFFER, pbo);
    gl.getBufferSubData(gl.PIXEL_PACK_BUFFER, 0, probeBuf);
    gl.bindBuffer(gl.PIXEL_PACK_BUFFER, null);
    const ratios = [];
    for (let i = 0; i < PROBE_W * PROBE_H; i++) {
      const f = (probeBuf[i * 4] * 256 + probeBuf[i * 4 + 1]) / 65535, p = 4 * (probeBuf[i * 4 + 2] * 256 + probeBuf[i * 4 + 3]) / 65535;
      if (f > 0.02 && f < 0.95 && p > 0.005 && p < 3.9) ratios.push(p / f);   // p = 0: no stored panorama there
    }
    if (ratios.length < 40) return;
    ratios.sort((a, b) => a - b);
    lastProbe = { n: ratios.length, p25: ratios[ratios.length >> 2], p50: ratios[ratios.length >> 1], p75: ratios[(3 * ratios.length) >> 2] };
    const g = Math.max(0.25, Math.min(4, ratios[ratios.length >> 1]));   // linear light: an sRGB exposure step x1.3 is x1.8 here
    gain = g;   // measured on the very view that is painted next (same exposure): no smoothing
    gainLog.push([probes, g]);
    if (gainLog.length > 200) gainLog.shift();
  }

  return {
    group,

    setCamera,

    /** Paint a frame (texture) taken at pose [roll, pitch, yaw] (deg) into the panorama. still: 0..1 quality factor. */
    paint(tex, pose, nowS, still = 1) {
      if (!tex) return null;
      const t = performance.now();
      readProbe();
      nowCode = codeFor(nowS);
      worldToCamMatrix(pose[0], pose[1], pose[2], w2c);
      const boxes = footprint(w2c);
      setBoxes(boxes);
      lastBoxFrac = boxes.reduce((a, [s0, s1, t0, t1]) => a + (s1 - s0) * (t1 - t0), 0);
      paintUniforms.uFrame.value = tex; paintUniforms.uW2C.value.copy(w2c);
      paintUniforms.uGain.value = paints === 0 || !exposure ? 1 : gain; paintUniforms.uStill.value = still; paintUniforms.uNowCode.value = nowCode;
      paintUniforms.uPrevColor.value = color[0].texture; paintUniforms.uPrevMeta.value = meta[0].texture;
      boxMesh.material = paintColorMat; offscreen(color[1], passScene);
      boxMesh.material = paintMetaMat; offscreen(meta[1], passScene);
      boxMesh.material = copyMat;
      copyUniforms.uSrc.value = color[1].texture; offscreen(color[0], passScene);
      copyUniforms.uSrc.value = meta[1].texture; offscreen(meta[0], passScene);
      paints++;
      lastPaintMs = performance.now() - t;
      return { ms: lastPaintMs, boxFrac: lastBoxFrac };
    },

    /** Measure the exposure gain of the current frame against the panorama (result read back in a later frame). */
    probe(tex, pose) {
      readProbe();
      if (!tex || !isGL2 || probePending || paints === 0) return false;
      worldToCamMatrix(pose[0], pose[1], pose[2], w2c);
      c2w.copy(w2c).transpose();
      probeUniforms.uFrame.value = tex; probeUniforms.uC2W.value.copy(c2w);
      probeUniforms.uColor.value = color[0].texture; probeUniforms.uMeta.value = meta[0].texture;
      offscreen(probeRT, probeScene);
      const prev = renderer.getRenderTarget();
      renderer.setRenderTarget(probeRT);
      if (!pbo) { pbo = gl.createBuffer(); gl.bindBuffer(gl.PIXEL_PACK_BUFFER, pbo); gl.bufferData(gl.PIXEL_PACK_BUFFER, probeBuf.byteLength, gl.STREAM_READ); }
      gl.bindBuffer(gl.PIXEL_PACK_BUFFER, pbo);
      gl.readPixels(0, 0, PROBE_W, PROBE_H, gl.RGBA, gl.UNSIGNED_BYTE, 0);
      gl.bindBuffer(gl.PIXEL_PACK_BUFFER, null);
      renderer.setRenderTarget(prev);
      fence = gl.fenceSync(gl.SYNC_GPU_COMMANDS_COMPLETE, 0);
      probePending = true; probes++;
      return true;
    },

    /** Per VR frame: the live frame and its capture pose (null = no live picture), the current time. */
    setLive(tex, pose, nowS) {
      readProbe();
      displayUniforms.uNowCode.value = t0S == null ? 0 : (nowS - t0S) / TIME_STEP_S;
      displayUniforms.uInvGain.value = exposure ? 1 / gain : 1;
      if (tex && pose) {
        worldToCamMatrix(pose[0], pose[1], pose[2], displayUniforms.uLiveW2C.value);
        displayUniforms.uLive.value = tex; displayUniforms.uLiveOn.value = 1;
      } else displayUniforms.uLiveOn.value = 0;
    },

    /** A gain probe is on its way back from the GPU. */
    get probeBusy() { readProbe(); return probePending; },

    set visible(v) { group.visible = v; },
    get visible() { return group.visible; },
    /** Display parameters (feather, fresh/stale seconds, dim) for the harness. */
    params: displayUniforms,

    clear() {
      for (const rt of [...color, ...meta]) { offscreenClear(rt); }
      t0S = null; paints = 0; gain = 1; gainLog = [];
    },

    get stats() {
      return { paints, probes, gain, lastPaintMs, lastBoxFrac, gains: gainLog.slice(-20).map((x) => x[1]), gainLog: gainLog.slice(), lastProbe };
    },

    /** The panorama render targets (harness: read them back). */
    targets: { color: color[0], meta: meta[0] },

    dispose() {
      for (const rt of [...color, ...meta, probeRT]) rt.dispose();
      group.removeFromParent();
      sphere.geometry.dispose(); displayMat.dispose();
    },
  };

  function offscreenClear(rt) {
    const prev = renderer.getRenderTarget(), prevXR = renderer.xr.enabled;
    renderer.xr.enabled = false;
    const cc = renderer.getClearColor(new THREE.Color()), ca = renderer.getClearAlpha();
    renderer.setRenderTarget(rt);
    renderer.setClearColor(0x000000, 0);
    renderer.clear(true, false, false);
    renderer.setClearColor(cc, ca);
    renderer.setRenderTarget(prev);
    renderer.xr.enabled = prevXR;
  }
}
