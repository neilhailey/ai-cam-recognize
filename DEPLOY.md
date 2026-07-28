# Deploying so a friend can try it (Render + Vercel)

Same shape as `ai-relief-demo`: FastAPI backend on Render, React/Vite frontend on
Vercel. Your friend just gets a URL — nothing to install. The STL path works with no
API key; the photo path needs `OPENAI_API_KEY` set on the backend.

## 0. Put this folder in its own GitHub repo

`AI_CAM_Recognize/` should be the repo root (like `ai-relief-demo` is its own repo).

```bash
cd AI_CAM_Recognize
git init && git add . && git commit -m "CNC machinability checker"
gh repo create ai-cam-recognize --public --source=. --push   # or create on github.com and push
```

`.gitignore` already excludes `backend/.venv`, `backend/.env`, `backend/output`, and
`frontend/node_modules` — your OpenAI key is not committed.

## 1. Backend → Render

1. Render dashboard → **New → Blueprint**, pick this repo. It reads [`render.yaml`](render.yaml)
   (Python 3.12, `rootDir: backend`, installs `requirements.txt`, runs uvicorn on `$PORT`).
2. When prompted for env vars:
   - `OPENAI_API_KEY` — paste your key (only needed for the photo path; STL works without it).
   - `CORS_ORIGINS` — leave blank for now (defaults to `*`); tighten in step 3.
3. Deploy. Note the URL, e.g. `https://ai-cam-recognize-backend.onrender.com`.
   Check `https://<that-url>/api/health` returns `{"status":"ok",...}`.

> Free tier sleeps after ~15 min idle, so the first request after a nap takes ~30–60s to
> wake. `embreex` (fast ray casting) has Linux/py3.12 wheels; if it ever fails to install,
> the engine automatically falls back to the pure-Python caster.

## 2. Frontend → Vercel

1. Vercel → **Add New → Project**, import the same repo.
2. Set **Root Directory** to `frontend`. Framework preset: **Vite** (auto-detected).
3. Add an Environment Variable:
   - `VITE_API_BASE` = your Render backend URL from step 1 (no trailing slash).
4. Deploy. You get a URL like `https://ai-cam-recognize.vercel.app` — **this is the link to send.**
   The built-in sample models ship with it, so your friend can click "try an example"
   without having their own STL.

## 3. Lock CORS (optional but tidy)

Back in Render, set `CORS_ORIGINS` to your Vercel URL (e.g.
`https://ai-cam-recognize.vercel.app`) and redeploy, so only your frontend can call the API.

## Updating later

Push to the repo's default branch — Render and Vercel both auto-redeploy. If you change
`VITE_API_BASE`, trigger a fresh Vercel build (env changes need a rebuild).
