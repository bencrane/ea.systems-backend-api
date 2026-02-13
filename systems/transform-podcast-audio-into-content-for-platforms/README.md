# Transform Podcast Audio into Content for Platforms

**Slug:** `transform-podcast-audio-into-content-for-platforms`
**Modal App:** `ea-transform-podcast`

## Description

This system accepts a podcast audio file (URL), transcribes it using Gemini 1.5/2.0, and automatically generates social media content for multiple platforms (LinkedIn, Twitter, Instagram, Newsletter).

## Input Payload

```json
{
  "client_id": "string (required)",
  "audio_url": "string (required, public URL)",
  "platforms": ["linkedin", "twitter", "instagram", "newsletter"],
  "tone": "professional",
  "episode_title": "optional string",
  "guest_name": "optional string"
}
```

## Output

Returns a Job ID immediately. Results are stored in the Supabase `jobs` table under the `result` column.

```json
{
  "job_id": "uuid",
  "status": "processing_started"
}
```

The final result in the database will look like:

```json
{
  "content": {
    "linkedin": ["..."],
    "twitter": ["..."],
    "instagram": ["..."],
    "newsletter": "...",
    "key_quotes": ["..."],
    "topics": ["..."]
  }
}
```

## Setup & Deployment

1.  Ensure `ea-secrets` is created in Modal with:
    -   `GOOGLE_API_KEY`
    -   `DATABASE_URL`
    -   `SUPABASE_URL`
    -   `SUPABASE_SERVICE_KEY`

2.  Deploy:
    ```bash
    modal deploy systems/transform-podcast-audio-into-content-for-platforms/main.py
    ```

## Development

Run locally (dev):
```bash
modal serve systems/transform-podcast-audio-into-content-for-platforms/main.py
```
