// Meeting notes (summary + action items from backend/, message type "summary"):
// a panel in the room to the right of the video window (toggled in VR) and a card on the page.

import * as THREE from "three";

const W = 1024, H = 1408, PANEL_W = 1.3;   // canvas px, metres
const SIDE_DEG = 42;                       // panel direction, right of "straight ahead"

function wrap(ctx, text, maxW) {
  const lines = [];
  let line = "";
  for (const word of text.split(/\s+/)) {
    const next = line ? `${line} ${word}` : word;
    if (ctx.measureText(next).width > maxW && line) { lines.push(line); line = word; } else line = next;
  }
  if (line) lines.push(line);
  return lines;
}

function draw(ctx, notes) {
  ctx.clearRect(0, 0, W, H);
  ctx.fillStyle = "rgba(18,20,28,0.92)";
  ctx.beginPath(); ctx.roundRect(0, 0, W, H, 40); ctx.fill();
  ctx.strokeStyle = "rgba(255,149,0,0.7)"; ctx.lineWidth = 4; ctx.stroke();
  const x = 56, maxW = W - 2 * x - 30;
  let y = 64;
  ctx.textBaseline = "top";
  const heading = (t) => { ctx.font = "700 44px system-ui, sans-serif"; ctx.fillStyle = "#ff9500"; ctx.fillText(t, x, y); y += 70; };
  const item = (bullet, t, color = "#e5e7eb") => {
    ctx.font = "34px system-ui, sans-serif";
    wrap(ctx, t, maxW).forEach((l, i) => {
      if (y > H - 60) return;
      if (i === 0) { ctx.fillStyle = "#ff9500"; ctx.fillText(bullet, x, y); }
      ctx.fillStyle = color; ctx.fillText(l, x + 36, y); y += 46;
    });
    y += 10;
  };
  heading("Meeting notes");
  if (!notes) { item("·", "The summary appears after the first minute of talking.", "#9ca3af"); return; }
  for (const s of notes.summary) item("•", s);
  if (notes.actions.length) {
    y += 16;
    heading("Action items");
    for (const a of notes.actions) item("☐", actionText(a));
  }
  if (notes.next_steps.length) {
    y += 16;
    heading("Next steps");
    for (const s of notes.next_steps) item("→", s);
  }
}

const actionText = (a) => (a.who ? `${a.who}: ${a.what}` : a.what) + (a.when ? ` (${a.when})` : "");

export function createNotes({ three, recenter, distM, cardEl }) {
  const canvas = document.createElement("canvas");
  canvas.width = W; canvas.height = H;
  const ctx = canvas.getContext("2d");
  const tex = new THREE.CanvasTexture(canvas);
  tex.colorSpace = THREE.SRGBColorSpace;
  const panel = new THREE.Mesh(new THREE.PlaneGeometry(PANEL_W, PANEL_W * H / W),
    new THREE.MeshBasicMaterial({ map: tex, transparent: true }));
  panel.visible = false;
  three.scene.add(panel);
  let notes = null;

  function render() {
    draw(ctx, notes);
    tex.needsUpdate = true;
    if (!cardEl) return;
    if (!notes) return;
    const section = (title, items) => {
      const h = document.createElement("h3"); h.textContent = title;
      const ul = document.createElement("ul");
      ul.append(...items.map((t) => { const li = document.createElement("li"); li.textContent = t; return li; }));
      return [h, ul];
    };
    const updated = document.createElement("p");
    updated.className = "muted";
    updated.textContent = `Updated ${new Date(notes.t * 1000).toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" })}`;
    cardEl.replaceChildren(
      ...section("Summary", notes.summary),
      ...(notes.actions.length ? section("Action items", notes.actions.map(actionText)) : []),
      ...(notes.next_steps.length ? section("Next steps", notes.next_steps) : []),
      updated);
  }

  let movedByUser = false;   // once dragged, the panel stays where the user put it
  function layout() {
    if (movedByUser) return;
    const yaw = new THREE.Euler().setFromQuaternion(
      new THREE.Quaternion(recenter.q0.x, recenter.q0.y, recenter.q0.z, recenter.q0.w), "YXZ").y;
    const a = yaw - SIDE_DEG * Math.PI / 180, d = distM * 0.8, y = 0.1;
    panel.position.set(-Math.sin(a) * d, y, -Math.cos(a) * d);
    panel.lookAt(0, y, 0);
  }

  render();
  layout();
  return {
    update(msg) { notes = { summary: msg.summary ?? [], actions: msg.actions ?? [], next_steps: msg.next_steps ?? [], t: msg.t }; render(); },
    layout,
    get visible() { return panel.visible; },
    toggle() { panel.visible = !panel.visible; layout(); return panel.visible; },
    /** The VR panel, for the scene to make it draggable. */
    panel,
    moved() { movedByUser = true; },
  };
}
