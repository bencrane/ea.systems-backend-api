import httpx
from app.config import settings


async def call_modal(endpoint_url: str, payload: dict) -> dict:
    """
    Generic wrapper to call a Modal endpoint.

    Usage:
        result = await call_modal("https://your-modal-endpoint.modal.run", {"input": "data"})
    """
    async with httpx.AsyncClient() as client:
        response = await client.post(
            endpoint_url,
            json=payload,
            headers={
                "Authorization": f"Token {settings.modal_token_id}:{settings.modal_token_secret}"
            },
            timeout=300.0,
        )
        response.raise_for_status()
        return response.json()
