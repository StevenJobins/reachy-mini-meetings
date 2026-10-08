// Vector line icons for the VR dock (canvas, no emoji fonts: those render differently on every headset).
// Path data from Lucide 1.53 (https://lucide.dev), ISC License, Copyright (c) Lucide Contributors 2022;
// portions MIT, Copyright (c) 2013-2022 Cole Bemis (Feather). 24x24 grid, stroke 2, round caps/joins.

const circle = (cx, cy, r) => `M${cx - r} ${cy}a${r} ${r} 0 1 0 ${2 * r} 0a${r} ${r} 0 1 0 ${-2 * r} 0`;
const rect = (x, y, w, h, r) => `M${x + r} ${y}h${w - 2 * r}a${r} ${r} 0 0 1 ${r} ${r}v${h - 2 * r}a${r} ${r} 0 0 1 ${-r} ${r}h${-(w - 2 * r)}a${r} ${r} 0 0 1 ${-r} ${-r}v${-(h - 2 * r)}a${r} ${r} 0 0 1 ${r} ${-r}z`;

const PATHS = {
  hand: ["M18 11V6a2 2 0 0 0-2-2a2 2 0 0 0-2 2", "M14 10V4a2 2 0 0 0-2-2a2 2 0 0 0-2 2v2",
    "M10 10.5V6a2 2 0 0 0-2-2a2 2 0 0 0-2 2v8",
    "M18 8a2 2 0 1 1 4 0v6a8 8 0 0 1-8 8h-2c-2.8 0-4.5-.86-5.99-2.34l-3.6-3.6a2 2 0 0 1 2.83-2.82L7 15"],
  laugh: ["M15 10V9", "M7.084 14.302a5.12 5.12 0 009.833 0 .24.24 0 00-.235-.302H7.32a.24.24 0 00-.235.302", "M9 10V9",
    circle(12, 12, 10)],
  languages: ["m5 8 6 6", "m4 14 6-6 2-3", "M2 5h12", "M7 2h1", "m22 22-5-10-5 10", "M14 18h6"],
  "message-square": ["M22 17a2 2 0 0 1-2 2H6.828a2 2 0 0 0-1.414.586l-2.202 2.202A.71.71 0 0 1 2 21.286V5a2 2 0 0 1 2-2h16a2 2 0 0 1 2 2z"],
  "notebook-pen": ["M13.4 2H6a2 2 0 0 0-2 2v16a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2v-7.4", "M2 6h4", "M2 10h4", "M2 14h4", "M2 18h4",
    "M21.378 5.626a1 1 0 1 0-3.004-3.004l-5.01 5.012a2 2 0 0 0-.506.854l-.837 2.87a.5.5 0 0 0 .62.62l2.87-.837a2 2 0 0 0 .854-.506z"],
  "volume-2": ["M11 4.702a.705.705 0 0 0-1.203-.498L6.413 7.587A1.4 1.4 0 0 1 5.416 8H3a1 1 0 0 0-1 1v6a1 1 0 0 0 1 1h2.416a1.4 1.4 0 0 1 .997.413l3.383 3.384A.705.705 0 0 0 11 19.298z",
    "M16 9a5 5 0 0 1 0 6", "M19.364 18.364a9 9 0 0 0 0-12.728"],
  "volume-x": ["M11 4.702a.7.7 0 0 0-1.203-.498L6.413 7.587A1.4 1.4 0 0 1 5.416 8H3a1 1 0 0 0-1 1v6a1 1 0 0 0 1 1h2.416a1.4 1.4 0 0 1 .997.413l3.383 3.384A.7.7 0 0 0 11 19.298z",
    "m16.5 14.5 5-5", "m16.5 9.5 5 5"],
  "audio-lines": ["M2 10v3", "M6 6v11", "M10 3v18", "M14 8v7", "M18 5v13", "M22 10v3"],
  globe: [circle(12, 12, 10), "M12 2a14.5 14.5 0 0 0 0 20 14.5 14.5 0 0 0 0-20", "M2 12h20"],
  sofa: ["M20 9V6a2 2 0 0 0-2-2H6a2 2 0 0 0-2 2v3",
    "M2 16a2 2 0 0 0 2 2h16a2 2 0 0 0 2-2v-5a2 2 0 0 0-4 0v1.5a.5.5 0 0 1-.5.5h-11a.5.5 0 0 1-.5-.5V11a2 2 0 0 0-4 0z",
    "M4 18v2", "M20 18v2", "M12 4v9"],
  "scan-line": ["M3 7V5a2 2 0 0 1 2-2h2", "M17 3h2a2 2 0 0 1 2 2v2", "M21 17v2a2 2 0 0 1-2 2h-2", "M7 21H5a2 2 0 0 1-2-2v-2", "M7 12h10"],
  house: ["M15 21v-8a1 1 0 0 0-1-1h-4a1 1 0 0 0-1 1v8",
    "M3 10a2 2 0 0 1 .709-1.528l7-6a2 2 0 0 1 2.582 0l7 6A2 2 0 0 1 21 10v9a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2z"],
  "rotate-ccw": ["M3 12a9 9 0 1 0 9-9 9.75 9.75 0 0 0-6.74 2.74L3 8", "M3 3v5h5"],
  film: [rect(3, 3, 18, 18, 2), "M7 3v18", "M3 7.5h4", "M3 12h18", "M3 16.5h4", "M17 3v18", "M17 7.5h4", "M17 16.5h4"],
  bug: ["M12 20v-9", "M14 7a4 4 0 0 1 4 4v3a6 6 0 0 1-12 0v-3a4 4 0 0 1 4-4z", "M14.12 3.88 16 2", "M21 21a4 4 0 0 0-3.81-4",
    "M21 5a4 4 0 0 1-3.55 3.97", "M22 13h-4", "M3 21a4 4 0 0 1 3.81-4", "M3 5a4 4 0 0 0 3.55 3.97", "M6 13H2",
    "m8 2 1.88 1.88", "M9 7.13V6a3 3 0 1 1 6 0v1.13"],
  bot: ["M12 8V4H8", rect(4, 8, 16, 12, 2), "M2 14h2", "M20 14h2", "M15 13v2", "M9 13v2"],
  "message-circle": ["M2.992 16.342a2 2 0 0 1 .094 1.167l-1.065 3.29a1 1 0 0 0 1.236 1.168l3.413-.998a2 2 0 0 1 1.099.092 10 10 0 1 0-4.777-4.719"],
  x: ["M18 6 6 18", "m6 6 12 12"],
  ellipsis: [circle(12, 12, 1), circle(19, 12, 1), circle(5, 12, 1)],
  mic: ["M12 19v3", "M19 10v2a7 7 0 0 1-14 0v-2", rect(9, 2, 6, 13, 3)],
  "mic-off": ["M12 19v3", "M15 9.34V5a3 3 0 0 0-5.68-1.33", "M16.95 16.95A7 7 0 0 1 5 12v-2", "M18.89 13.23A7 7 0 0 0 19 12v-2",
    "m2 2 20 20", "M9 9v3a3 3 0 0 0 5.12 2.12"],
};

const cache = {};

export const hasIcon = (name) => name in PATHS;

/** Draws icon `name` centred at (x, y), `size` px wide. Returns false (nothing drawn) for an unknown name. */
export function drawIcon(ctx, name, x, y, size, color, strokeWidth = 2) {
  if (!hasIcon(name)) return false;
  cache[name] ??= PATHS[name].map((d) => new Path2D(d));
  const s = size / 24;
  ctx.save();
  ctx.translate(x - size / 2, y - size / 2);
  ctx.scale(s, s);
  ctx.strokeStyle = color; ctx.lineWidth = strokeWidth; ctx.lineCap = "round"; ctx.lineJoin = "round";
  for (const p of cache[name]) ctx.stroke(p);
  ctx.restore();
  return true;
}

// ---- dock buttons (scene.js draws them into 256 x 320 canvas textures: circle on top, label below)
const ORANGE = "#ff9500", FONT = "system-ui, -apple-system, 'Segoe UI', Roboto, sans-serif";

function disc(ctx, cx, cy, r, top, bottom, rim, rimWidth) {
  const g = ctx.createLinearGradient(0, cy - r, 0, cy + r);
  g.addColorStop(0, top); g.addColorStop(1, bottom);
  ctx.fillStyle = g;
  ctx.beginPath(); ctx.arc(cx, cy, r, 0, 2 * Math.PI); ctx.fill();
  if (rim) {
    ctx.strokeStyle = rim; ctx.lineWidth = rimWidth;
    ctx.beginPath(); ctx.arc(cx, cy, r - rimWidth / 2, 0, 2 * Math.PI); ctx.stroke();
  }
}

function label(ctx, text, cx, y, maxW, px, color) {
  text = String(text ?? "");
  ctx.textAlign = "center"; ctx.textBaseline = "alphabetic"; ctx.fillStyle = color;
  let size = px;
  do { ctx.font = `600 ${size}px ${FONT}`; } while (ctx.measureText(text).width > maxW && --size > 30);
  while (ctx.measureText(text).width > maxW && text.length > 2) text = `${text.slice(0, -2)}…`;
  ctx.fillText(text, cx, y);
}

/** Round dock button: icon (registry name, else drawn as text) in a dark glass circle, label below. */
export function drawDockButton(ctx, w, h, { icon, text, active = false, hover = false }) {
  const r = w / 2 - 8, cx = w / 2, cy = r + 8;
  if (hover) disc(ctx, cx, cy, r, "#ffab3d", "#f28500", null, 0);
  else if (active) disc(ctx, cx, cy, r, "rgba(92,58,18,0.94)", "rgba(52,34,14,0.94)", ORANGE, 7);
  else disc(ctx, cx, cy, r, "rgba(52,56,70,0.92)", "rgba(20,22,30,0.92)", "rgba(255,255,255,0.16)", 4);
  const ink = hover ? "#1c1306" : active ? "#ffb547" : "#ffffff";
  if (!drawIcon(ctx, icon, cx, cy, 110, ink, 2)) {
    ctx.textAlign = "center"; ctx.textBaseline = "middle"; ctx.fillStyle = ink;
    ctx.font = `100px ${FONT}, 'Noto Color Emoji'`;
    ctx.fillText(icon ?? "", cx, cy + 6);
  }
  label(ctx, text, cx, h - 16, w - 8, 44, active ? "#ffb547" : "#eef0f4");
}

/** Big round mic button: red with a crossed-out mic when muted, like a video call. */
export function drawMicButton(ctx, w, h, muted, hover, text) {
  const r = w / 2 - 10, cx = w / 2, cy = r + 10;
  if (muted) disc(ctx, cx, cy, r, hover ? "#ff7a7d" : "#f0575b", hover ? "#e5484d" : "#c9353a", null, 0);
  else if (hover) disc(ctx, cx, cy, r, "#ffab3d", "#f28500", null, 0);
  else disc(ctx, cx, cy, r, "rgba(52,56,70,0.95)", "rgba(20,22,30,0.95)", "rgba(255,255,255,0.16)", 4);
  drawIcon(ctx, muted ? "mic-off" : "mic", cx, cy, 120, hover && !muted ? "#1c1306" : "#ffffff", 2);
  label(ctx, text, cx, h - 12, w - 8, 42, "#eef0f4");
}
