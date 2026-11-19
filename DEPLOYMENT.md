# Publishing VendorGuard

There are two different things you can publish:

| What | Needs a server? | What visitors can do |
|---|---|---|
| **Static snapshot** (`dashboard.html`) | No | Browse the existing run history and audit details. The check panel is disabled. |
| **Live app** (`server.py`) | Yes, a Python host | Fill the form / paste JSON or text / upload a file and run the real pipeline. |

Notion, GitHub Pages and Netlify can only host the **static snapshot** — they
cannot execute Python. For the **live app** you need a free Python host such as
Render or Hugging Face Spaces. Both paths are below.

---

## Option 0 — Share a temporary link in 2 minutes (no account)

Keep the server running locally:

```powershell
python server.py
```

In a second terminal, expose it through a free tunnel:

```powershell
# Cloudflare quick tunnel (free, no account, URL lives only while the command runs)
winget install Cloudflare.cloudflared   # once
cloudflared tunnel --url http://127.0.0.1:8765
```

Cloudflare prints a public `https://….trycloudflare.com` URL. Anyone can open
it while your laptop and the tunnel are running. Good for a quick demo or a
Notion embed; not a permanent home.

`npx localtunnel --port 8765` is another no-account option if Node.js is
installed.

---

## Option 1 — Live app on Render (recommended free host)

The repo already contains `render.yaml`, `requirements.txt` and a `Procfile`,
so deployment is mostly clicking.

### 1. Put the project on GitHub

```powershell
cd C:\Users\HP\Downloads\ps2-vendor-onboarding
git init
git add .
git commit -m "VendorGuard vendor onboarding verification"
git branch -M main
# create an empty repo on github.com first, then:
git remote add origin https://github.com/<your-username>/vendorguard.git
git push -u origin main
```

### 2. Create the Render service

1. Sign up at <https://render.com> (GitHub login is easiest).
2. **New → Blueprint**, pick the repository. Render reads `render.yaml`.
3. Apply the blueprint. Wait for the first deploy; Render gives you
   `https://vendorguard-xxxx.onrender.com`.

If you prefer a manual service instead of the blueprint:

- **New → Web Service**, connect the repo
- Runtime: **Python**
- Build command: `pip install -r requirements.txt`
- Start command: `python server.py --host 0.0.0.0 --port $PORT`
- Plan: **Free**

### 3. Test it

Open the public URL, use **Load example → Valid vendor**, press
**Run verification**, and confirm **REAL**. Then try the fraud example and
confirm **FAKE / FRAUD RISK**.

### Free-plan behaviour to expect

- The service **sleeps after ~15 minutes without traffic**; the next visit can
  take 30–60 seconds to wake.
- The filesystem (including `runs/`) is **ephemeral**: history resets when the
  service restarts or redeploys. Mount a paid disk or write to a database/S3 if
  you need the audit trail to survive.
- Free tier limits change over time — check Render's current pricing page.

---

## Option 2 — Hugging Face Spaces (free Docker host)

Useful if your audience is comfortable with ML demo sites.

1. Create a free account at <https://huggingface.co>.
2. **New Space** → SDK: **Docker** → visibility: **Public**.
3. Upload this folder (or connect the GitHub repo). The included `Dockerfile`
   listens on port `7860`, which is what Spaces expects.
4. The Space URL is public, e.g.
   `https://huggingface.co/spaces/<you>/vendorguard`.

The Dockerfile also works on Koyeb, Fly.io, Google Cloud Run and similar
container hosts.

---

## Option 3 — Static snapshot on GitHub Pages or Netlify Drop

Use this when you only need people to **view the dashboard and results** — the
live form will be disabled.

`dashboard.html` is fully self-contained (CSS, JS and all run data embedded).
No build step.

### GitHub Pages

```powershell
# Pages expects index.html at the published root
Copy-Item dashboard.html index.html
git add index.html
git commit -m "Add static dashboard for GitHub Pages"
git push
```

Then in GitHub: **Settings → Pages → Build and deployment → Deploy from a
branch → main / (root)**. The site appears at
`https://<your-username>.github.io/<repo>/`.

### Netlify Drop

1. Copy `dashboard.html` to a folder and rename it `index.html`.
2. Open <https://app.netlify.com/drop> and drag the folder in.
3. Netlify gives you a public `https://….netlify.app` URL.

On a static host the page detects that the API is missing and disables the
check button, so visitors cannot send requests into the void.

---

## Sharing / embedding in Notion

Notion is a document tool, not an app host. It cannot run `server.py`.

- **Best result:** deploy the live app (Option 1 or 2), then in Notion type
  `/embed` and paste the public URL. The dashboard becomes a live, usable panel
  inside the page. If Notion refuses to embed a particular host, use
  `/bookmark` or a normal link instead.
- **Static snapshot:** publish the page (Option 3), then `/embed` or link that
  URL in Notion.
- **Quick demo:** use the temporary tunnel URL from Option 0 in an embed, but
  remember it dies when the tunnel stops.

A good Notion page structure:

1. Short problem/solution summary (from `README.md`)
2. Embedded live dashboard
3. The four edge cases and expected verdicts (from `README.md`)
4. Link to `ANALYSIS.md` and the GitHub repo

---

## Security and privacy before going public

The server has **no login** and is meant for a demo. Anyone with the URL can:

- submit arbitrary test data,
- see every run in the history and audit trail,
- consume the free host's CPU/disk.

Do not enter real vendor PII or bank details on a public deployment. If you
need a private setup, add authentication, rate limiting and durable storage
before sharing the URL.

---

## Troubleshooting

| Symptom | Fix |
|---|---|
| Render deploy fails at start | Start command must include `--host 0.0.0.0 --port $PORT` |
| First visit is slow | Free instance was asleep; it wakes on the first request |
| "history only – live pipeline not hosted here" | You opened a static copy (Pages/Netlify) or the API is down |
| History disappears after a restart | Free hosts have ephemeral filesystems |
| Page loads but verification 404s | Static host; deploy `server.py`, not only `dashboard.html` |
