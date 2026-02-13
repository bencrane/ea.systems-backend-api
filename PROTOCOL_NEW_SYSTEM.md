# Protocol: Creating a New EA System Function

This protocol defines the standard process for creating, implementing, and deploying a new automation system within the Everything Automation (ea.systems) platform. Follow this guide strictly to ensure consistency across all serverless functions.

## 1. System Architecture

- **Platform:** Modal (serverless Python functions)
- **App Structure:** 
  - **One App per System:** Each system gets its own Modal App named `ea-{slug}`.
  - **Shared Secrets:** All systems use the shared secret group `ea-secrets`.
  - **File Structure:** `systems/{slug}/main.py` (logic) and `systems/{slug}/README.md` (docs).
- **Database:** Supabase (Postgres). Raw `psycopg` connection within functions (no ORM).
- **Storage:** Supabase Storage (bucket: `system-assets`).
- **Entrypoint:** Each system exposes a FastAPI web endpoint (`@web_app.post("/")`).

## 2. Pre-requisites Checklist

Before writing code, ask the user:
1. **System Slug:** What is the unique slug? (e.g., `generate-ai-video-ads`)
2. **Input Payload:** What JSON fields are required?
3. **Output:** What is the expected JSON response?
4. **Third-Party APIs:** Which APIs are needed? (e.g., Fal, Gemini, OpenAI). Are the keys already in `ea-secrets`?
5. **Compute Requirements:** Does it need GPU? High timeout? (`timeout=300` default, up to `1200` for video).

## 3. Implementation Steps

### Step A: Scaffold (Automated via API)
*Usually done via the EA backend API, but if manual:*
1. Create directory: `systems/{slug}/`
2. Create `main.py` stub.
3. Create `README.md` stub.
4. Insert row into Supabase `systems` table.

### Step B: Define Modal Image
Standard image definition in `main.py`:
```python
image = (
    modal.Image.debian_slim(python_version="3.12")
    .pip_install(
        "fastapi[standard]",
        "psycopg[binary]",
        "pydantic",
        "httpx",
        # Add system-specific deps: "google-genai", "fal-client", "openai"
    )
    # Add system-specific apt deps: .apt_install("ffmpeg")
)
```

### Step C: Define App & Secrets
```python
app = modal.App(f"ea-{slug}", image=image)

@app.function(
    secrets=[modal.Secret.from_name("ea-secrets")], # ALWAYS use this shared group
    timeout=600, # Adjust based on task
)
@modal.asgi_app()
def fastapi_app():
    # ... imports inside function ...
    return web_app
```

### Step D: Database Logic (Raw SQL)
Do **not** use SQLAlchemy or the shared `app.database` module inside the Modal function. It adds unnecessary cold-start overhead.
Use `psycopg` with `os.environ["DATABASE_URL"]`.

**Standard Job Pattern:**
1. **Validate API Key:** Check `x-api-key` header against `systems` table.
2. **Create Job:** Insert `received` row into `jobs` table with `payload`.
3. **Process:** Run logic (sync or async background task).
4. **Update Job:** Update `status` and `result` (JSONB) in `jobs` table.

### Step E: Async Processing (If > 30s)
If the task takes longer than 30s (e.g., video gen):
1. Endpoint returns `{ "job_id": "...", "status": "processing_started" }` immediately.
2. Use `fastapi.BackgroundTasks` to run the pipeline.
3. Pipeline updates DB row to `completed` or `failed`.

### Step F: Deployment
Deploy directly from the root of the repo:
```bash
modal deploy systems/{slug}/main.py
```
*Note: Do not use `modal serve` for production endpoints.*

## 4. Key Conventions

- **Secrets:** NEVER hardcode API keys. ALWAYS use `os.environ["KEY"]` loaded from `ea-secrets`.
- **Imports:** Put heavy imports (pandas, ffmpeg, ml libraries) **inside** the `def fastapi_app():` or handler function to keep cold starts fast.
- **Validation:** Use Pydantic models for all inputs/outputs.
- **Error Handling:** Wrap pipeline in `try/except`, log error to DB `jobs.error`, and raise 500.

## 5. Fal.ai Integration (Media Gen)
If using Fal for media:
- Ensure `FAL_KEY` is in `ea-secrets`.
- Use `fal_client.subscribe()` for async result polling.
- **Flux Ultra** for images.
- **Kling** or **LTX** for video.
- **F5-TTS** for audio.
- Always upload Fal results to Supabase Storage immediately; Fal URLs expire.

## 6. Railway Integration
- The **Management API** runs on Railway (`app/`).
- The **Systems (Workers)** run on Modal.
- Railway simply triggers deployments via the `POST /systems/{slug}/deploy` endpoint (which runs `modal deploy` internally).
- You do **not** deploy system logic to Railway. Railway is just the control plane.
