// The hosted service behind Reachy Meetings: translation (DeepL) and meeting notes (Gemini) with our keys, so the
// people who use the app need none. A Cloudflare Worker with a D1 database. The keys never leave it.
//
// Every installation of the Mac app has an id (a UUID it makes on its first start) and sends it where the key
// would go. Time is counted per installation and day (UTC): every minute in which it asked for a translation is a
// meeting minute. FREE_MINUTES of those a day are free (default 60); after that it answers "quota used up" unless
// the installation is in the table `plans` (schema.sql). There is no payment here: a plan is entered by hand.
//
//   POST /v2/translate        like api.deepl.com/v2/translate; Authorization: DeepL-Auth-Key <installation>
//                             over the free time: 456, DeepL's own "quota exceeded"
//   POST /chat/completions    like Gemini's OpenAI-compatible endpoint; Authorization: Bearer <installation>
//                             over the free time: 402
//   GET  /usage               {used_min, free_min, unlimited} for that installation, counts nothing
//
// Secrets (wrangler secret put …): DEEPL_AUTH_KEY, GEMINI_API_KEY. FREE_MINUTES is in wrangler.toml.
// An installation id is not a login: someone who makes up new ids gets new free hours. Good enough while the app
// is not public; before that, sign-in belongs here.

const GEMINI = "https://generativelanguage.googleapis.com/v1beta/openai/chat/completions";
const UUID = /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i;
const MAX_BODY = 200_000;

const json = (body, status = 200) =>
  new Response(JSON.stringify(body), { status, headers: { "Content-Type": "application/json" } });

/** Counts this minute if `count`, and says how the installation stands today. */
async function usage(db, install, count) {
  const minute = new Date().toISOString().slice(0, 16);   // 2026-11-18T15:04
  if (count) await db.prepare("INSERT OR IGNORE INTO usage_minutes (install, minute) VALUES (?, ?)").bind(install, minute).run();
  const used = await db.prepare("SELECT COUNT(*) AS n FROM usage_minutes WHERE install = ? AND minute >= ?")
    .bind(install, minute.slice(0, 10)).first("n");
  const plan = await db.prepare("SELECT 1 AS yes FROM plans WHERE install = ?").bind(install).first();
  return { used_min: used ?? 0, unlimited: plan != null };
}

export default {
  async fetch(request, env) {
    const path = new URL(request.url).pathname;
    const free = Number(env.FREE_MINUTES ?? "60");
    const install = (request.headers.get("Authorization") ?? "").replace(/^(DeepL-Auth-Key|Bearer)\s+/i, "").trim();
    if (!UUID.test(install)) return json({ message: "no installation id" }, 403);
    try {
      if (request.method === "GET" && path === "/usage") return json({ ...(await usage(env.DB, install, false)), free_min: free });
      if (request.method !== "POST") return json({ message: "not found" }, 404);
      const body = await request.text();
      if (body.length > MAX_BODY) return json({ message: "too large" }, 413);

      if (path === "/v2/translate") {
        const u = await usage(env.DB, install, true);
        if (!u.unlimited && u.used_min > free) return json({ message: "Quota Exceeded", ...u, free_min: free }, 456);
        const key = env.DEEPL_AUTH_KEY ?? "";
        const host = key.endsWith(":fx") ? "api-free.deepl.com" : "api.deepl.com";
        const r = await fetch(`https://${host}/v2/translate`, {
          method: "POST",
          headers: { "Authorization": `DeepL-Auth-Key ${key}`, "Content-Type": request.headers.get("Content-Type") ?? "application/json" },
          body,
        });
        return new Response(r.body, { status: r.status, headers: { "Content-Type": r.headers.get("Content-Type") ?? "application/json" } });
      }

      if (path === "/chat/completions") {
        const u = await usage(env.DB, install, false);   // the notes ride on the translation's minutes
        if (!u.unlimited && u.used_min > free) return json({ message: "free time used up", ...u, free_min: free }, 402);
        const r = await fetch(GEMINI, {
          method: "POST",
          headers: { "Authorization": `Bearer ${env.GEMINI_API_KEY ?? ""}`, "Content-Type": "application/json" },
          body,
        });
        return new Response(r.body, { status: r.status, headers: { "Content-Type": "application/json" } });
      }
      return json({ message: "not found" }, 404);
    } catch (e) {
      return json({ message: String(e) }, 500);
    }
  },
};
