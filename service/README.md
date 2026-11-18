# The hosted service: translation and meeting notes without keys of one's own

`worker.js` is a Cloudflare Worker (with a D1 database) that holds the project's DeepL and Gemini keys and passes the
backend's requests on. People who use the Mac app then need no keys. It counts meeting minutes per installation
and day; 60 a day are free, after that it answers "quota used up" until an installation is entered in `plans`.
There is no payment in it: "more than an hour costs something" is, for now, a row someone adds by hand.

| | |
|---|---|
| The program | `worker.js`, settings in `wrangler.toml` |
| The tables | `schema.sql` (`usage_minutes`, `plans`), in the D1 database `reachy-meetings` |
| Secrets | `DEEPL_AUTH_KEY`, `GEMINI_API_KEY` (`npx wrangler secret put <name>`); `FREE_MINUTES` in `wrangler.toml` |
| Who calls it | the backend (`translate.py`, `summary.py`) when `REACHY_SERVICE_URL` and `REACHY_INSTALL_ID` are set, which the Mac app does once `SERVICE_URL` in `laptop-app/reachy_meetings_app/supervisor.py` names the worker |

Set up, once (in this folder):

```bash
npx wrangler login
npx wrangler d1 create reachy-meetings          # put the database_id it prints into wrangler.toml
npx wrangler d1 execute reachy-meetings --remote --file schema.sql
npx wrangler secret put DEEPL_AUTH_KEY
npx wrangler secret put GEMINI_API_KEY
npx wrangler deploy                             # prints the address: that is SERVICE_URL
```

Unlock an installation (its id is in the Mac app's note when the hour is used up, and in its settings file):

```bash
npx wrangler d1 execute reachy-meetings --remote --command "INSERT INTO plans (install, note) VALUES ('<id>', 'who and why')"
```

What goes through it: the text of what is said in the meetings of everyone who uses the app. Whoever runs this
service is responsible for that text; say so in a privacy notice before anyone outside the team uses it. On
Gemini's free plan Google may use the content, so the paid plan belongs here.

An installation id is not a login: new ids get new free hours. Fine while the app is not public.
