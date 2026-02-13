import modal

# ---------------------------------------------------------------------------
# Modal app
# ---------------------------------------------------------------------------
image = (
    modal.Image.debian_slim(python_version="3.12")
    .apt_install("ffmpeg")
    .pip_install(
        "fastapi[standard]",
        "psycopg[binary]",
        "pydantic",
        "google-genai",
        "httpx",
        "fal-client",
    )
)

app = modal.App("ea-generate-ai-video-ads", image=image)

SYSTEM_SLUG = "generate-ai-video-ads"
_VERSION = "4.0"  # phase 4 — video assembly


# ---------------------------------------------------------------------------
# Modal entrypoint
# ---------------------------------------------------------------------------
@app.function(
    secrets=[modal.Secret.from_name("ea-secrets")],
    timeout=1200,  # 20 mins for video gen
    concurrency_limit=5,
)
@modal.asgi_app()
def fastapi_app():
    import base64
    import json
    import os
    import re
    import shutil
    import subprocess
    import uuid
    from enum import Enum
    from pathlib import Path
    from typing import Optional

    import fal_client
    import httpx
    from fastapi import FastAPI, Header, HTTPException, BackgroundTasks
    from google import genai
    from google.genai import types
    from pydantic import BaseModel, Field

    web_app = FastAPI()

    # ------------------------------------------------------------------
    # Request / Response models
    # ------------------------------------------------------------------
    class ProductInteraction(str, Enum):
        wearing = "wearing"
        holding = "holding"
        using = "using"

    class CameraAngle(str, Enum):
        full_body = "full_body"
        waist_up = "waist_up"
        close_up = "close_up"

    class JobRequest(BaseModel):
        client_id: str = Field(..., min_length=1)
        product_photos: list[str] = Field(..., min_length=1)
        product_brief: str = Field(..., min_length=1)
        product_interaction: ProductInteraction
        camera_angle: CameraAngle
        target_audience: Optional[str] = None
        brand_reference_url: Optional[str] = None

    class ScriptChunk(BaseModel):
        chunk_id: int
        text: str
        duration_estimate: int

    class Script(BaseModel):
        script_id: str
        hook_angle: str
        full_text: str
        chunks: list[ScriptChunk]

    class ScriptsOutput(BaseModel):
        scripts: list[Script]

    class JobResponse(BaseModel):
        job_id: str
        status: str
        scripts: Optional[list[Script]] = None
        character_images: Optional[list[str]] = None
        final_video_url: Optional[str] = None

    # ------------------------------------------------------------------
    # DB helpers
    # ------------------------------------------------------------------
    def get_conn():
        import psycopg
        return psycopg.connect(os.environ["DATABASE_URL"])

    def validate_api_key(api_key: str | None) -> None:
        if not api_key:
            raise HTTPException(status_code=401, detail="Missing API key")
        with get_conn() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    "SELECT api_key FROM systems WHERE slug = %s",
                    (SYSTEM_SLUG,),
                )
                row = cur.fetchone()
        if row is None:
            raise HTTPException(status_code=500, detail="System not registered")
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
                # Merge new result keys into existing result
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
    # Storage & Utils
    # ------------------------------------------------------------------
    def upload_from_file(path: str, local_path: str, content_type: str) -> str:
        with open(local_path, "rb") as f:
            data = f.read()
        return upload_to_storage(path, data, content_type)

    def upload_from_url(path: str, url: str) -> str:
        with httpx.Client(timeout=60) as client:
            resp = client.get(url)
            resp.raise_for_status()
            data = resp.content
            content_type = resp.headers.get("content-type", "image/png")
        return upload_to_storage(path, data, content_type)

    def upload_to_storage(path: str, data: bytes, content_type: str) -> str:
        supabase_url = os.environ["SUPABASE_URL"]
        service_key = os.environ["SUPABASE_SERVICE_KEY"]
        upload_url = f"{supabase_url}/storage/v1/object/system-assets/{path}"
        resp = httpx.post(
            upload_url,
            content=data,
            headers={
                "Authorization": f"Bearer {service_key}",
                "apikey": service_key,
                "Content-Type": content_type,
            },
            timeout=60,
        )
        resp.raise_for_status()
        return f"{supabase_url}/storage/v1/object/public/system-assets/{path}"

    def download_file(url: str, dest_path: str) -> None:
        with httpx.stream("GET", url, timeout=60) as resp:
            resp.raise_for_status()
            with open(dest_path, "wb") as f:
                for chunk in resp.iter_bytes():
                    f.write(chunk)

    # ------------------------------------------------------------------
    # Media Generation
    # ------------------------------------------------------------------
    def generate_scripts(client: genai.Client, payload: JobRequest, brand_context: str) -> list[dict]:
        # ... (same as before, collapsed for brevity)
        brand_section = ""
        if brand_context:
            brand_section = f"BRAND CONTEXT:\n{brand_context}\n"
        audience_section = f"Target audience: {payload.target_audience}" if payload.target_audience else ""
        
        prompt = f"""You are a world-class UGC ad scriptwriter.
Write exactly 3 short-form video ad scripts.
PRODUCT BRIEF: {payload.product_brief}
PRODUCT INTERACTION: {payload.product_interaction.value}
CAMERA ANGLE: {payload.camera_angle.value}
{audience_section}
{brand_section}

REQUIREMENTS:
- 3 scripts, 30-60s each.
- Hooks: Emotional, Practical, Social Proof.
- Break into 8-10s chunks.
- Natural, conversational voice.
"""
        response = client.models.generate_content(
            model="gemini-2.5-flash",
            contents=prompt,
            config=types.GenerateContentConfig(
                response_mime_type="application/json",
                response_schema=ScriptsOutput,
            ),
        )
        return [s.model_dump() for s in response.parsed.scripts]

    def analyze_product_photos(client: genai.Client, payload: JobRequest) -> str:
        # ... (same as before)
        photo_parts = []
        http = httpx.Client(timeout=20, follow_redirects=True)
        for photo in payload.product_photos[:3]:
            try:
                if photo.startswith("data:"):
                    header, b64data = photo.split(",", 1)
                    mime = header.split(":")[1].split(";")[0]
                    photo_parts.append(types.Part.from_bytes(data=base64.b64decode(b64data), mime_type=mime))
                elif photo.startswith("http"):
                    resp = http.get(photo)
                    ct = resp.headers.get("content-type", "image/jpeg").split(";")[0]
                    photo_parts.append(types.Part.from_bytes(data=resp.content, mime_type=ct))
            except: continue
        
        if not photo_parts: return payload.product_brief
        
        return client.models.generate_content(
            model="gemini-2.5-flash",
            contents=["Describe this product in precise visual detail for image generation.", *photo_parts],
        ).text.strip()

    def generate_character_images(payload: JobRequest, product_desc: str, job_id: str) -> list[str]:
        # ... (same as before)
        framing = {
            "full_body": "full body shot from head to toe",
            "waist_up": "medium shot from the waist up",
            "close_up": "close-up shot of face and upper chest",
        }[payload.camera_angle.value]
        
        prompt = (
            f"Photorealistic UGC-style photo of a real person {payload.product_interaction.value} a product. "
            f"The product: {product_desc}. Framing: {framing}. "
            f"Setting: casual home environment, natural lighting. "
            f"Authentic expression, looking at camera. "
            f"High quality, 4k, raw photo."
        )
        if payload.target_audience:
            prompt = f"Demographic: {payload.target_audience}. " + prompt

        urls = []
        for i in range(3):
            res = fal_client.subscribe(
                "fal-ai/flux-pro/v1.1-ultra",
                arguments={
                    "prompt": prompt,
                    "aspect_ratio": "9:16",
                    "safety_tolerance": "2",
                    "seed": i * 1000 + 42,
                },
            )
            urls.append(upload_from_url(f"jobs/{job_id}/character/variation-{i+1}.png", res["images"][0]["url"]))
        return urls

    def generate_audio(text: str, job_id: str, chunk_index: int) -> str:
        """Generate audio via Fal F5-TTS."""
        # Clean text for TTS
        clean_text = text.replace('"', '').replace('\n', ' ').strip()
        
        res = fal_client.subscribe(
            "fal-ai/f5-tts",
            arguments={
                "gen_text": clean_text,
                "ref_audio_url": "https://fal.media/files/monkey/Tx_dev_S5-JgJ7c8w8L7j.wav", # Generic female voice
            },
        )
        audio_url = res["audio_url"]["url"]
        return upload_from_url(f"jobs/{job_id}/audio/chunk-{chunk_index}.wav", audio_url)

    def generate_video_clip(image_url: str, prompt: str, job_id: str, chunk_index: int) -> str:
        """Generate video clip via Fal Kling."""
        # Using Kling 1.0 Standard for speed/reliability in this demo. 
        # Pro is better but slower.
        res = fal_client.subscribe(
            "fal-ai/kling-video/v1.0/standard/image-to-video", 
            arguments={
                "prompt": prompt,
                "image_url": image_url,
                "duration": "5", # Kling supports 5 or 10. Chunks are ~8-10s, so we might need to loop or slow down.
                "aspect_ratio": "9:16",
            },
        )
        video_url = res["video"]["url"]
        return upload_from_url(f"jobs/{job_id}/video/chunk-{chunk_index}.mp4", video_url)

    def process_full_pipeline(job_id: str, payload: JobRequest, x_api_key: str):
        """Async worker to run the full expensive pipeline."""
        gemini = genai.Client(api_key=os.environ["GEMINI_API_KEY"])
        tmp_dir = Path(f"/tmp/{job_id}")
        tmp_dir.mkdir(parents=True, exist_ok=True)

        try:
            # 1. Scripts
            brand_context = scrape_brand_context(payload.brand_reference_url) if payload.brand_reference_url else ""
            scripts = generate_scripts(gemini, payload, brand_context)
            update_job(job_id, "scripts_generated", {"scripts": scripts})

            # 2. Character Images
            prod_desc = analyze_product_photos(gemini, payload)
            char_urls = generate_character_images(payload, prod_desc, job_id)
            update_job(job_id, "images_generated", {"character_images": char_urls, "product_description": prod_desc})

            # 3. Pick Script & Character (Defaults: Script 1, Image 1)
            selected_script = scripts[0]
            selected_char_url = char_urls[0]
            
            # 4. Generate Media for Chunks
            clips = []
            for i, chunk in enumerate(selected_script["chunks"]):
                # Audio
                audio_supa_url = generate_audio(chunk["text"], job_id, i)
                local_audio = tmp_dir / f"audio_{i}.wav"
                download_file(audio_supa_url, str(local_audio))

                # Video
                video_supa_url = generate_video_clip(selected_char_url, chunk["text"], job_id, i)
                local_video = tmp_dir / f"video_{i}.mp4"
                download_file(video_supa_url, str(local_video))
                
                clips.append({"audio": local_audio, "video": local_video, "index": i})

            # 5. Assembly with ffmpeg
            # Merge audio/video for each clip, trim video to audio length
            concat_list_path = tmp_dir / "concat.txt"
            with open(concat_list_path, "w") as f:
                for clip in clips:
                    out_clip = tmp_dir / f"final_clip_{clip['index']}.mp4"
                    # Combine: video (looped/slowed if needed) + audio
                    # Simple version: trim video to audio duration.
                    # If video is shorter than audio, we might need to loop it.
                    # Ideally Kling generates 5s. If audio is 8s, we need 2 clips or slowmo.
                    # For MVP: We will loop the video to match audio duration.
                    
                    cmd = [
                        "ffmpeg", "-y",
                        "-stream_loop", "-1", "-i", str(clip["video"]),
                        "-i", str(clip["audio"]),
                        "-shortest", # Finish when shortest input (audio usually, but here we looped video) ends? No, shortest of (looped video, audio) = audio
                        "-map", "0:v:0", "-map", "1:a:0",
                        "-c:v", "libx264", "-c:a", "aac",
                        str(out_clip)
                    ]
                    subprocess.run(cmd, check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
                    f.write(f"file '{out_clip.name}'\n")

            # Concat all clips
            final_output = tmp_dir / "final_ad.mp4"
            subprocess.run([
                "ffmpeg", "-y", "-f", "concat", "-safe", "0",
                "-i", str(concat_list_path),
                "-c", "copy",
                str(final_output)
            ], check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)

            # 6. Upload Final Video
            final_url = upload_from_file(f"jobs/{job_id}/final_ad.mp4", str(final_output), "video/mp4")
            
            update_job(job_id, "completed", {
                "final_video_url": final_url,
                "selected_script_id": selected_script["script_id"],
                "selected_character_image": selected_char_url
            })

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

        # Offload to background task since video gen takes minutes
        # In Modal, BackgroundTasks run in the same container.
        # Ideally we'd spawn a separate Modal function, but this works for MVP.
        background_tasks.add_task(process_full_pipeline, job_id, payload, x_api_key)

        return JobResponse(
            job_id=job_id,
            status="processing_started",
        )

    return web_app
