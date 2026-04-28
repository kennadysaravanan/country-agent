# 🚀 Free Deployment Guide

This app deploys for **completely free** on **Streamlit Community Cloud**.

- ✅ No credit card
- ✅ No time limit
- ✅ Public HTTPS URL (`https://your-app.streamlit.app`)
- ✅ Free TLS, free DNS
- ⚠️ Public GitHub repo required (Streamlit Cloud limitation)
- ⚠️ App sleeps after ~7 days of no traffic; first hit wakes it (~30s cold start)

---

## Why this approach?

The repo has both a FastAPI server (`app/main.py`) and a Streamlit UI (`ui.py`). For **production** you'd run both. For **a free demo**, the Streamlit UI imports the agent in-process, so we only need to deploy ONE thing.

```
┌──────────────────────────────────────────┐
│  Streamlit Cloud (FREE)                  │
│                                          │
│  ui.py  ──imports──►  app.agent.ask()    │
│                            │             │
│                            ▼             │
│                   Groq API + REST        │
│                   Countries API          │
└──────────────────────────────────────────┘
```

The FastAPI server stays in the repo so reviewers can verify the production architecture, run tests, and run it locally with `./run.sh`.

---

## Step-by-step (5 minutes)

### 1. Get a free Groq API key

Go to <https://console.groq.com/keys>, sign up (free, no card), click **Create API Key**, copy it (starts with `gsk_`).

### 2. Push this code to a public GitHub repo

```bash
cd country-agent
git init
git add .
git commit -m "Initial commit"
git branch -M main

# Create an empty repo on github.com first (e.g. "country-agent"), then:
git remote add origin https://github.com/<your-username>/country-agent.git
git push -u origin main
```

> ⚠️ Streamlit Community Cloud requires the repo to be **public**. Make sure no real secrets are committed — `.gitignore` already excludes `.env` and `.streamlit/secrets.toml`.

### 3. Deploy on Streamlit Cloud

1. Go to <https://share.streamlit.io/> and sign in with GitHub.
2. Click **Create app** → **Deploy a public app from GitHub**.
3. Fill in:
   - **Repository:** `<your-username>/country-agent`
   - **Branch:** `main`
   - **Main file path:** `ui.py`
   - **App URL:** pick something like `country-agent` → `https://country-agent.streamlit.app`
4. Click **Advanced settings** → **Secrets**, paste:
   ```toml
   GROQ_API_KEY = "gsk_your_actual_key_here"
   ```
5. Click **Deploy**.

First build takes 1–2 minutes. After that you'll have a public URL.

### 4. Done

Open the URL. Try:
- *"What is the population of Germany?"*
- *"What about its capital?"* ← memory test
- *"Compare Brazil and India"*

---

## Updating the app

Just push to `main`:

```bash
git add .
git commit -m "Update"
git push
```

Streamlit Cloud auto-redeploys on push. Watch it in the dashboard.

---

## Troubleshooting

### "GROQ_API_KEY is not configured"
You skipped step 3 → Secrets. Open the app → ⋮ menu → **Settings → Secrets** → paste the key → save.

### Build fails: "ModuleNotFoundError"
Streamlit Cloud reads `requirements.txt` from the repo root. Make sure it was committed.

### App is slow to start
That's the cold start (sleep-on-idle). After the first request, subsequent ones are fast. To keep it warm, you can hit the URL once a day with a simple uptime monitor, but that's optional.

### Logs
App dashboard → **Manage app** in the bottom-right → live logs.

---

## Alternative free hosts (if you don't want a public repo)

| Platform | Free? | Public repo? | Notes |
|---|---|---|---|
| **Streamlit Cloud** | ✅ Truly free | Required | What we use here |
| **Hugging Face Spaces** | ✅ Truly free | Required (or token) | Streamlit/Gradio support, similar limits |
| **Render** | ✅ 750hr/mo | Not required | Sleeps after 15min idle. Use the FastAPI version. |
| **Fly.io** | ❌ Trial only | n/a | Not really free in 2026 |
| **Railway** | ❌ Trial credits | n/a | Runs out fast |

If you want to deploy the **FastAPI** version separately on Render (the "production architecture" version), see the FastAPI section in `README.md`.
