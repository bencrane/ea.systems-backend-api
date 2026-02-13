# Generate AI Video Ads

**Slug:** `generate-ai-video-ads`
**Modal App:** `ea-generate-ai-video-ads`

## Description

Accepts product information and generates a complete AI UGC video ad.

**Pipeline:**
1.  **Script Generation:** Google Gemini 2.5 Flash generates 3 distinct scripts (Emotional, Practical, Social Proof).
2.  **Character Image:** Fal (Flux Ultra) generates 3 photorealistic character variations based on product photos and brief.
3.  **Audio Generation:** Fal (F5-TTS) generates voiceovers for the selected script.
4.  **Video Generation:** Fal (Kling 1.0) animates the character image for each script chunk.
5.  **Assembly:** `ffmpeg` merges audio/video, loops video to match audio length, and concatenates clips.
6.  **Delivery:** Final `.mp4` uploaded to Supabase Storage.

## Payload

```json
{
  "client_id": "uuid",
  "product_photos": ["https://... or base64"],
  "product_brief": "Short description of the product",
  "product_interaction": "wearing | holding | using",
  "camera_angle": "full_body | waist_up | close_up",
  "target_audience": "optional string",
  "brand_reference_url": "optional url"
}
```

## Response

Immediate response:
```json
{
  "job_id": "uuid",
  "status": "processing_started"
}
```

Poll the database `jobs` table for status updates:
- `received`
- `scripts_generated`
- `images_generated`
- `completed` (contains `final_video_url` in `result` JSON)
- `failed` (contains `error`)

## Authentication

`X-API-Key` header — validated against the `systems` table in Supabase.

## Deployment

```bash
modal deploy systems/generate-ai-video-ads/main.py
```
