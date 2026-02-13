import modal

# ---------------------------------------------------------------------------
# Modal app
# ---------------------------------------------------------------------------
image = (
    modal.Image.debian_slim(python_version="3.12")
    .pip_install(
        "fastapi[standard]",
        "psycopg[binary]",
        "pydantic",
        "google-genai",
        "httpx",
    )
)

app = modal.App("ea-transform-podcast", image=image)

SYSTEM_SLUG = "transform-podcast-audio-into-content-for-platforms"

# ---------------------------------------------------------------------------
# Modal entrypoint
# ---------------------------------------------------------------------------
@app.function(
    secrets=[modal.Secret.from_name("ea-secrets")],
    timeout=900,  # 15 mins for transcription/analysis
    concurrency_limit=10,
)
@modal.asgi_app()
def fastapi_app():
    import json
    import os
    import uuid
    import shutil
    from pathlib import Path
    from typing import Optional, List, Any

    import httpx
    from fastapi import FastAPI, Header, HTTPException, BackgroundTasks
    from google import genai
    from google.genai import types
    from pydantic import BaseModel, Field

    web_app = FastAPI()

    # ------------------------------------------------------------------
    # Request / Response models
    # ------------------------------------------------------------------
    class JobRequest(BaseModel):
        client_id: str = Field(..., min_length=1)
        audio_url: str = Field(..., min_length=1)
        platforms: List[str] = Field(
            default=["linkedin", "twitter", "instagram", "newsletter"]
        )
        tone: str = Field(default="professional")
        episode_title: Optional[str] = None
        guest_name: Optional[str] = None

    class ContentOutput(BaseModel):
        linkedin: List[str]
        twitter: List[str]  # Thread + standalone
        instagram: List[str]
        newsletter: str
        key_quotes: List[str]
        topics: List[str]

    class JobResponse(BaseModel):
        job_id: str
        status: str
        content: Optional[ContentOutput] = None

    # ------------------------------------------------------------------
    # DB helpers
    # ------------------------------------------------------------------
    def get_conn():
        import psycopg
        return psycopg.connect(os.environ["DATABASE_URL"])

    def validate_api_key(api_key: str | None) -> None:
        if not api_key:
            raise HTTPException(status_code=401, detail="Missing API key")
        
        # Runtime lookup against Supabase
        with get_conn() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    "SELECT api_key FROM systems WHERE slug = %s",
                    (SYSTEM_SLUG,),
                )
                row = cur.fetchone()
        
        if row is None:
            raise HTTPException(status_code=500, detail="System not registered in database")
        
        if api_key != row[0]:
            raise HTTPException(status_code=401, detail="Invalid API key")

    def create_job(payload: JobRequest) -> str:
        job_id = str(uuid.uuid4())
        payload_json = json.loads(payload.model_dump_json())
        with get_conn() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    INSERT INTO jobs (id, system_slug, client_id, status, payload)
                    VALUES (%s, %s, %s, 'received', %s::jsonb)
                    """,
                    (job_id, SYSTEM_SLUG, payload.client_id, json.dumps(payload_json)),
                )
            conn.commit()
        return job_id

    def update_job(job_id: str, status: str, result: dict) -> None:
        with get_conn() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    UPDATE jobs
                    SET status = %s,
                        result = coalesce(result, '{}'::jsonb) || %s::jsonb,
                        updated_at = now()
                    WHERE id = %s
                    """,
                    (status, json.dumps(result), job_id),
                )
            conn.commit()

    def fail_job(job_id: str, error: str) -> None:
        with get_conn() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    UPDATE jobs
                    SET status = 'failed',
                        error = %s,
                        updated_at = now()
                    WHERE id = %s
                    """,
                    (error, job_id),
                )
            conn.commit()

    # ------------------------------------------------------------------
    # Processing Logic
    # ------------------------------------------------------------------
    def download_file(url: str, dest_path: Path) -> None:
        with httpx.stream("GET", url, timeout=120) as resp:
            resp.raise_for_status()
            with open(dest_path, "wb") as f:
                for chunk in resp.iter_bytes():
                    f.write(chunk)

    def process_podcast(job_id: str, payload: JobRequest):
        tmp_dir = Path(f"/tmp/{job_id}")
        tmp_dir.mkdir(parents=True, exist_ok=True)
        
        client = genai.Client(api_key=os.environ["GOOGLE_API_KEY"])

        try:
            # 1. Download Audio
            audio_path = tmp_dir / "input_audio"
            download_file(payload.audio_url, audio_path)
            
            # 2. Upload to Gemini
            # Note: The file API is used for large context inputs
            uploaded_file = client.files.upload(
                file=audio_path,
                config=types.UploadFileConfig(mime_type="audio/mp3") # Defaulting to generic audio/mp3, Gemini detects usually
            )

            # 3. Construct Prompt
            platforms_str = ", ".join(payload.platforms)
            context_str = ""
            if payload.episode_title:
                context_str += f"Episode Title: {payload.episode_title}\n"
            if payload.guest_name:
                context_str += f"Guest Name: {payload.guest_name}\n"

            prompt = f"""
You are an expert social media content strategist and copywriter.
Analyze the attached audio file (a podcast episode).

CONTEXT:
{context_str}
Tone: {payload.tone}

TASK:
1. Transcribe the audio internally (no need to output full transcript, just use it for analysis).
2. Extract key quotes, main topics, and the most engaging/controversial moments.
3. Generate content for the following platforms: {platforms_str}.

PLATFORM REQUIREMENTS:
- LinkedIn: 1-2 long-form posts with strong hooks, professional yet engaging spacing, and a clear call to action.
- Twitter/X: A thread of 5-10 tweets summarizing the episode, plus 3 standalone viral-style tweets.
- Instagram: 3 caption options (short, medium, long) with relevant hashtags.
- Newsletter: A concise summary + bulleted key takeaways + "Why you should listen" section.

OUTPUT FORMAT:
Return PURE JSON matching this schema:
{{
  "linkedin": ["post 1", "post 2"],
  "twitter": ["thread tweet 1", "thread tweet 2", ... "standalone tweet 1", ...],
  "instagram": ["caption 1", "caption 2", "caption 3"],
  "newsletter": "full newsletter text...",
  "key_quotes": ["quote 1", "quote 2"],
  "topics": ["topic 1", "topic 2"]
}}
"""

            # 4. Generate Content
            response = client.models.generate_content(
                model="gemini-2.0-flash", # Using 2.0 Flash for speed/multimodal capabilities
                contents=[
                    types.Content(
                        role="user",
                        parts=[
                            types.Part.from_uri(
                                file_uri=uploaded_file.uri,
                                mime_type=uploaded_file.mime_type
                            ),
                            types.Part.from_text(text=prompt),
                        ]
                    )
                ],
                config=types.GenerateContentConfig(
                    response_mime_type="application/json",
                    response_schema=ContentOutput,
                ),
            )
            
            output_data = response.parsed.model_dump()

            # 5. Save Results
            update_job(job_id, "completed", {"content": output_data})

        except Exception as e:
            print(f"Job {job_id} failed: {e}")
            fail_job(job_id, str(e))
        finally:
            shutil.rmtree(tmp_dir, ignore_errors=True)

    # ------------------------------------------------------------------
    # Endpoint
    # ------------------------------------------------------------------
    @web_app.post("/", response_model=JobResponse)
    async def handle_job(
        payload: JobRequest,
        background_tasks: BackgroundTasks,
        x_api_key: Optional[str] = Header(None),
    ):
        validate_api_key(x_api_key)
        job_id = create_job(payload)
        
        background_tasks.add_task(process_podcast, job_id, payload)

        return JobResponse(
            job_id=job_id,
            status="processing_started",
        )

    return web_app
