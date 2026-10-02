// Robot connection through Pollen's JS SDK: HF sign-in -> central signalling -> WebRTC
// (video + set_target on the data channel + measured pose stream). No three.js, no DOM except the <video>.

import { ReachyMini, rpyToMatrix, matrixToRpy } from "reachy";
import { DEG } from "./pose.js";

const SILENT_TRIED = "reachy-xr-silent-signin-tried";

export function createRobot({ clientId, onStatus, onMeasuredHead, log }) {
  const reachy = new ReachyMini({ clientId, appName: "Reachy Meetings XR", videoJitterBufferTargetMs: 0 });
  let streaming = false;

  reachy.addEventListener("iceStateChange", (e) => onStatus({ ice: e.detail?.state }));
  reachy.addEventListener("sessionStopped", (e) => {
    streaming = false;
    onStatus({ robot: "stopped" });
    log("session stopped", JSON.stringify(e.detail));
  });
  reachy.addEventListener("sessionReconnecting", () => onStatus({ robot: "reconnecting" }));
  reachy.addEventListener("sessionReconnected", () => { streaming = true; onStatus({ robot: "connected" }); });
  reachy.addEventListener("error", (e) => log("error", JSON.stringify(e.detail)));
  reachy.addEventListener("state", (e) => {
    const h = e.detail?.head;
    if (!h || h.length !== 16) return;
    const { roll, pitch, yaw } = matrixToRpy([h.slice(0, 4), h.slice(4, 8), h.slice(8, 12), h.slice(12, 16)]);
    onMeasuredHead(roll, pitch, yaw);
  });

  return {
    /**
     * Resolve the sign-in state. Not signed in yet -> one silent attempt (OAuth prompt=none):
     * users who already authorized the app come straight back signed in, without any screen.
     * Returns "signed-in", "signed-out", or "redirecting" (page is about to navigate away).
     */
    async signInState() {
      const ok = await reachy.authenticate().catch((e) => { log("auth", e?.message ?? e); return false; });
      if (ok) { sessionStorage.removeItem(SILENT_TRIED); return "signed-in"; }
      if (!clientId || sessionStorage.getItem(SILENT_TRIED)) return "signed-out";
      sessionStorage.setItem(SILENT_TRIED, "1");
      await reachy.login({ prompt: "none" });
      return "redirecting";
    },

    signIn() { return reachy.login(); },
    signOut() { reachy.logout(); sessionStorage.removeItem(SILENT_TRIED); },

    attachVideo(video) {
      reachy.attachVideo(video);   // before connecting: the videoTrack event fires during session setup
      reachy.addEventListener("videoTrack", () => {
        onStatus({ video: "track" });
        video.play().catch((e) => log("video.play:", e?.message ?? e));
      });
    },

    /** Connect to the robot. Single free robot -> picked automatically; several -> first free one. */
    async connect() {
      onStatus({ robot: "connecting" });
      const res = await reachy.autoConnect({
        wakeOnConnect: false,   // the robot stays asleep until the user taps Start (wake())
        pickRobot: async (robots) => {
          log("robots:", robots.map((r) => `${r.name ?? r.id}${r.busy ? " (busy)" : ""}`).join(", "));
          return robots.find((r) => !r.busy)?.id ?? null;   // TODO picker when the team has several robots
        },
      });
      reachy.subscribePose();
      streaming = true;
      onStatus({ robot: res.robotName ?? res.robotId });
      return res;
    },

    get connected() { return streaming; },

    /** Robot microphone on/off (the audio track of the video element). Turn on inside a user gesture. */
    setAudio(on) { reachy.setAudioMuted(!on); },

    /** Wake the robot (plays the wake-up motion, motors on). Resolves when it is ready for head targets. */
    async wake() {
      onStatus({ motors: "waking" });
      await reachy.ensureAwake(3000);
      onStatus({ motors: "awake" });
    },

    /** Back to the sleep pose, then motors off. Never throws: the user may already be gone. */
    async sleep() {
      onStatus({ motors: "sleeping" });
      try {
        await reachy.gotoSleep();
        reachy.setMotorMode("disabled");
        onStatus({ motors: "asleep" });
      } catch (e) {
        log("goto sleep:", e?.message ?? e);
      }
    },

    /** Head target in degrees (robot frame), body yaw and antennas [right, left] in degrees. Returns true if queued. */
    setHead({ roll, pitch, yaw, bodyYaw, antennas = [0, 0] }) {
      if (!streaming) return false;
      return reachy.setTarget({
        head: rpyToMatrix(roll, pitch, yaw).flat(),
        antennas: antennas.map((a) => a / DEG),   // SDK order: [right, left]
        body_yaw: bodyYaw / DEG,
      });
    },
  };
}
