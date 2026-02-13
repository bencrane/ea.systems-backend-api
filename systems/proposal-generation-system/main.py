import modal
from fastapi import FastAPI, Header, HTTPException
from pydantic import BaseModel
from typing import Optional, Any

app = modal.App("proposal-generation-system")
web_app = FastAPI()


class SystemRequest(BaseModel):
    client_id: str
    # Add additional fields as needed


class SystemResponse(BaseModel):
    status: str
    system: str
    client_id: str
    data: Optional[Any] = None


@web_app.post("/")
async def handler(
    payload: SystemRequest,
    x_api_key: Optional[str] = Header(None)
) -> SystemResponse:
    """
    proposal-generation-system system handler

    Expected payload:
    {
        "client_id": "uuid",
        ...other fields
    }

    Authentication: X-API-Key header must match: sk_ihTjGTejXQE4f62yU7m6uQjkLTMzyEAJV1Z1b7CEV8I
    """
    # Validate API key
    if x_api_key != "sk_ihTjGTejXQE4f62yU7m6uQjkLTMzyEAJV1Z1b7CEV8I":
        raise HTTPException(status_code=401, detail="Invalid or missing API key")

    # TODO: Add your system logic here

    return SystemResponse(
        status="ok",
        system="proposal-generation-system",
        client_id=payload.client_id,
        data=None
    )


@app.function()
@modal.asgi_app()
def fastapi_app():
    return web_app
