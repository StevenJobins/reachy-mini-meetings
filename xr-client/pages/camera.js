// Robot camera model: pixel <-> direction, with lens distortion. Pure maths: no three.js, no DOM.
//
// Image coordinates are normalized (u, v in 0..1, origin top left), so a scaled-down WebRTC stream uses the same
// model as long as the aspect ratio stays. Camera frame: x right, y down, z forward (OpenCV).
// Distortion: OpenCV order [k1, k2, p1, p2, k3, k4, k5, k6, s1, s2, s3, s4] (rational + thin prism); shorter
// arrays are padded with 0, the tilt terms (tau x/y) of the 14-term model are ignored (tiny for this lens).
//
// Sources, best first: camera.json (our checkerboard calibration of the stream, robot/scripts/calibrate_camera.py),
// Pollen's factory calibration of the Lite camera (reachy_mini 1.11 camera_constants, scaled like the daemon
// does), or the old estimate: a pinhole with 54 deg vertical field of view and no distortion.

const DEG = 180 / Math.PI;

export class CameraModel {
  constructor({ name = "custom", width = 1920, height = 1080, fx, fy, cx, cy, dist = [], rms = null } = {}) {
    Object.assign(this, { name, width, height, fx, fy, cx, cy, rms });
    this.dist = [...dist, ...new Array(12).fill(0)].slice(0, 12);
    this.hfovDeg = this.yawDeg(0, 0.5) - this.yawDeg(1, 0.5);
    this.vfovDeg = this.upDeg(0.5, 0) - this.upDeg(0.5, 1);
  }

  /** Pinhole without distortion from a vertical field of view (the old estimate). */
  static pinhole(vfovDeg = 54, width = 1920, height = 1080) {
    const f = (height / 2) / Math.tan(vfovDeg / 2 / DEG);
    return new CameraModel({ name: `estimate ${vfovDeg}°`, width, height, fx: f, fy: f, cx: width / 2, cy: height / 2 });
  }

  /** Undistorted normalized coords (x = X/Z, y = Y/Z) -> distorted normalized coords. */
  distort(x, y) {
    const [k1, k2, p1, p2, k3, k4, k5, k6, s1, s2, s3, s4] = this.dist;
    const r2 = x * x + y * y, r4 = r2 * r2, r6 = r4 * r2;
    const radial = (1 + k1 * r2 + k2 * r4 + k3 * r6) / (1 + k4 * r2 + k5 * r4 + k6 * r6);
    return [
      x * radial + 2 * p1 * x * y + p2 * (r2 + 2 * x * x) + s1 * r2 + s2 * r4,
      y * radial + p1 * (r2 + 2 * y * y) + 2 * p2 * x * y + s3 * r2 + s4 * r4,
    ];
  }

  /** Direction [X, Y, Z] (camera frame) -> [u, v] normalized, or null behind the camera. */
  project([X, Y, Z]) {
    if (Z <= 1e-6) return null;
    const [xd, yd] = this.distort(X / Z, Y / Z);
    return [(this.fx * xd + this.cx) / this.width, (this.fy * yd + this.cy) / this.height];
  }

  /** [u, v] normalized -> unit direction [X, Y, Z], or null if the lens model does not reach there. */
  unproject(u, v, guess = null) {
    const xd = (u * this.width - this.cx) / this.fx, yd = (v * this.height - this.cy) / this.fy;
    let [x, y] = guess ?? [xd, yd];
    for (let i = 0; i < 30; i++) {   // Newton on distort(x, y) = (xd, yd), numeric Jacobian
      const [fx0, fy0] = this.distort(x, y);
      const ex = fx0 - xd, ey = fy0 - yd;
      if (ex * ex + ey * ey < 1e-18) break;
      const h = 1e-6;
      const [ax, ay] = this.distort(x + h, y), [bx, by] = this.distort(x, y + h);
      const j11 = (ax - fx0) / h, j21 = (ay - fy0) / h, j12 = (bx - fx0) / h, j22 = (by - fy0) / h;
      const det = j11 * j22 - j12 * j21;
      if (Math.abs(det) < 1e-12) return null;
      x -= (j22 * ex - j12 * ey) / det;
      y -= (-j21 * ex + j11 * ey) / det;
      if (!isFinite(x) || !isFinite(y) || Math.abs(x) > 20 || Math.abs(y) > 20) return null;
    }
    const [cx, cy] = this.distort(x, y);
    if (Math.abs(cx - xd) > 1e-5 || Math.abs(cy - yd) > 1e-5) return null;
    const n = Math.hypot(x, y, 1);
    return [x / n, y / n, 1 / n];
  }

  /** Horizontal angle of an image point, degrees, + = left (robot convention). */
  yawDeg(u, v) { const d = this.unproject(u, v) ?? this.unproject(0.5, v); return d ? -Math.atan2(d[0], d[2]) * DEG : 0; }

  /** Vertical angle of an image point, degrees, + = up. */
  upDeg(u, v) { const d = this.unproject(u, v) ?? this.unproject(u, 0.5); return d ? Math.atan2(-d[1], Math.hypot(d[0], d[2])) * DEG : 0; }

  /** Image column (u) of a direction yawDeg (+ = left) on the horizon. */
  uForYaw(yawDeg) {
    const p = this.project([-Math.sin(yawDeg / DEG), 0, Math.cos(yawDeg / DEG)]);
    return p ? p[0] : (yawDeg > 0 ? 0 : 1);
  }

  toJSON() {
    const { name, width, height, fx, fy, cx, cy, dist, rms } = this;
    return { name, width, height, fx, fy, cx, cy, dist, rms };
  }
}

/**
 * Pollen's factory calibration of the Reachy Mini Lite camera (full sensor 3840x2592), scaled to the 1920x1080
 * stream exactly like the daemon does (camera_utils.intrinsics_for_size, crop 1.115). Unverified for our stream:
 * that scaling treats x and y differently (16:9 out of a 4:3 sensor), so prefer a calibration (camera.json).
 */
export function factoryLite(width = 1920, height = 1080, crop = 1.115) {
  const K = { fx: 2001.8076426486707, fy: 2003.0778885944105, cx: 1905.876059826701, cy: 1328.3239717935594 };
  const D = [-1.4652320301298614, 0.6542714131667414, 0.012147809271745049, -0.002677286460143648,
    0.3035939941825349, -1.4300809080461876, 0.570024082887235, 0.3567299243352951,
    0.003057363348400015, 0.0003357614008682464, -0.009897126394310923, -0.002050919484589521];
  const sx = width / 3840, sy = height / 2592;
  return new CameraModel({
    name: "Pollen factory", width, height, dist: D,
    fx: K.fx * sx * crop, fy: K.fy * sy * crop, cx: K.cx / 3840 * width, cy: K.cy / 2592 * height,
  });
}
