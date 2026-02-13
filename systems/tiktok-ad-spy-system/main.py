import modal
import os
from fastapi import FastAPI, Header, HTTPException
from pydantic import BaseModel
from typing import Optional, Any

app = modal.App("tiktok-ad-spy-system")
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
    tiktok-ad-spy-system system handler

    Expected payload:
    {
        "client_id": "uuid",
        ...other fields
    }

    Authentication: X-API-Key header must match: sk_o3VLYO_STr_G1kebDc9Ta2GWxkabQRhm5HlWT2wPtno
    """
    # Validate API key
    if x_api_key != "sk_o3VLYO_STr_G1kebDc9Ta2GWxkabQRhm5HlWT2wPtno":
        raise HTTPException(status_code=401, detail="Invalid or missing API key")

    # TODO: Add your system logic here

    return SystemResponse(
        status="ok",
        system="tiktok-ad-spy-system",
        client_id=payload.client_id,
        data=None
    )


@app.function(secrets=[modal.Secret.from_name("ea-secrets")])
@modal.asgi_app()
def fastapi_app():
    return web_app
