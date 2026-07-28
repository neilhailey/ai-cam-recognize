# Deploying so a friend can try it (Render + Vercel)

Same shape as `ai-relief-demo`: FastAPI backend on Render, React/Vite frontend on
Vercel. Your friend just gets a URL — nothing to install. The STL path works with no
API key; the photo path needs `OPENAI_API_KEY` set on the backend.

## 0. GitHub repo — already done

The code is pushed to **https://github.com/neilhailey/ai-cam-recognize** (private).
`.gitignore` excludes `backend/.venv`, `backend/.env`, `backend/output`, and
`frontend/node_modules`, so your OpenAI key is not in the repo (only `.env.example`).

Render and Vercel can deploy from a private repo. If you'd rather it be public (like
`ai-relief-demo`): `gh repo edit neilhailey/ai-cam-recognize --visibility public`.
Push updates with a normal `git push` — both platforms auto-redeploy.

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

## Analysis resolution vs instance size

Coarse meshes lose small undercuts, so a 4-axis-machinable part can be reported as
5-axis. Measured on a real 482k-triangle carving (correct answer: **4-axis, 99.9%**):

| Analysed faces | Verdict | 4-axis reach | Peak RAM |
|----------------|---------|--------------|----------|
| 30k (default)  | 5-axis ✗ | 95.2 %      | ~250 MB  |
| 120k           | 5-axis ✗ | 97.6 %      | ~540 MB  |
| 190k           | **4-axis ✓** | 98.6 %  | ~700 MB  |
| 482k (full)    | **4-axis ✓** | 99.9 %  | ~980 MB  |

A 2M-triangle model needs ~2 GB at full resolution. So accuracy is limited by RAM:

| Render plan | RAM / CPU | Suggested `MAX_ANALYSIS_FACES` |
|-------------|-----------|-------------------------------|
| Free        | 512 MB / 0.1 | `30000` (default) |
| Starter     | 512 MB / 0.5 | `30000` — same RAM, ~5x faster only |
| Standard    | 2 GB / 1     | `400000` — full res for most models |
| Pro         | 4 GB / 2     | `2000000` — effectively no decimation |

To change it, set **`MAX_ANALYSIS_FACES`** on the Render service (and optionally
`SEARCH_FACES`, default `6000`, for the orientation/setup/tool searches). Set
**`VITE_MAX_UPLOAD_FACES`** to the same value on Vercel so the browser stops
pre-shrinking below what the server can handle. No code changes needed.

Note the upload itself is separate: a full 94 MB STL still takes minutes to send on a
typical home uplink, which is why the browser reduces it first.

## Updating later

Push to the repo's default branch — Render and Vercel both auto-redeploy. If you change
`VITE_API_BASE`, trigger a fresh Vercel build (env changes need a rebuild).
