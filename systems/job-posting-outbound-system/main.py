import modal
import os
from fastapi import FastAPI, Header, HTTPException
from pydantic import BaseModel
from typing import Optional, Any

app = modal.App("job-posting-outbound-system")
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
    job-posting-outbound-system system handler

    Expected payload:
    {
        "client_id": "uuid",
        ...other fields
    }

    Authentication: X-API-Key header must match: sk_sm6_FzbBfHjWNmgRV4aqthYXSavbEyJUJzukAGnMYEs
    """
    # Validate API key
    if x_api_key != "sk_sm6_FzbBfHjWNmgRV4aqthYXSavbEyJUJzukAGnMYEs":
        raise HTTPException(status_code=401, detail="Invalid or missing API key")

    # TODO: Add your system logic here

    return SystemResponse(
        status="ok",
        system="job-posting-outbound-system",
        client_id=payload.client_id,
        data=None
    )


@app.function(secrets=[modal.Secret.from_name("ea-secrets")])
@modal.asgi_app()
def fastapi_app():
    return web_app
