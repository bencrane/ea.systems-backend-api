import os
import secrets
import subprocess
import tempfile
import base64
from datetime import datetime
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select
import httpx
from app.models.system import SystemDB, SystemCreate, SystemUpdate, SystemResponse
from app.config import settings


def generate_api_key() -> str:
    """Generate a random API key for system authentication"""
    return f"sk_{secrets.token_urlsafe(32)}"


def generate_main_py_template(slug: str, api_key: str) -> str:
    """Generate templated main.py for Modal function"""
    return f'''import modal
from fastapi import FastAPI, Header, HTTPException
from pydantic import BaseModel
from typing import Optional, Any

app = modal.App("{slug}")
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
    {slug} system handler

    Expected payload:
    {{
        "client_id": "uuid",
        ...other fields
    }}

    Authentication: X-API-Key header must match: {api_key}
    """
    # Validate API key
    if x_api_key != "{api_key}":
        raise HTTPException(status_code=401, detail="Invalid or missing API key")

    # TODO: Add your system logic here

    return SystemResponse(
        status="ok",
        system="{slug}",
        client_id=payload.client_id,
        data=None
    )


@app.function()
@modal.asgi_app()
def fastapi_app():
    return web_app
'''


def generate_readme_template(slug: str, name: str, description: str) -> str:
    """Generate templated README.md for system"""
    return f'''# {name}

**Slug:** `{slug}`

## Description

{description}

## Usage

POST request with JSON body and X-API-Key header.

## Development

1. Edit `main.py` to implement system logic
2. Deploy via API: POST /systems/{slug}/deploy
'''


async def create_github_file(path: str, content: str, message: str) -> bool:
    """Create or update a file in GitHub repo"""
    url = f"https://api.github.com/repos/{settings.github_repo}/contents/{path}"
    headers = {
        "Authorization": f"token {settings.github_token}",
        "Accept": "application/vnd.github.v3+json"
    }

    async with httpx.AsyncClient() as client:
        # Check if file exists (to get SHA for update)
        existing = await client.get(url, headers=headers)

        data = {
            "message": message,
            "content": base64.b64encode(content.encode()).decode()
        }

        if existing.status_code == 200:
            data["sha"] = existing.json()["sha"]

        response = await client.put(url, headers=headers, json=data)
        return response.status_code in [200, 201]


async def get_github_file(path: str) -> str:
    """Get file content from GitHub repo"""
    url = f"https://api.github.com/repos/{settings.github_repo}/contents/{path}"
    headers = {
        "Authorization": f"token {settings.github_token}",
        "Accept": "application/vnd.github.v3+json"
    }

    async with httpx.AsyncClient() as client:
        response = await client.get(url, headers=headers)
        if response.status_code != 200:
            raise ValueError(f"File not found: {path}")

        content = response.json()["content"]
        return base64.b64decode(content).decode()


async def delete_github_file(path: str, message: str) -> bool:
    """Delete a file from GitHub repo"""
    url = f"https://api.github.com/repos/{settings.github_repo}/contents/{path}"
    headers = {
        "Authorization": f"token {settings.github_token}",
        "Accept": "application/vnd.github.v3+json"
    }

    async with httpx.AsyncClient() as client:
        # Get SHA first
        existing = await client.get(url, headers=headers)
        if existing.status_code != 200:
            return False

        sha = existing.json()["sha"]
        response = await client.delete(url, headers=headers, json={"message": message, "sha": sha})
        return response.status_code == 200


async def create_system(db: AsyncSession, system_data: SystemCreate) -> SystemResponse:
    """Create a new system: GitHub files + database record"""
    # Check if slug already exists
    result = await db.execute(
        select(SystemDB).where(SystemDB.slug == system_data.slug)
    )
    existing = result.scalar_one_or_none()
    if existing:
        raise ValueError(f"System with slug '{system_data.slug}' already exists")

    # Generate API key
    api_key = generate_api_key()

    # Create files in GitHub
    main_py_content = generate_main_py_template(system_data.slug, api_key)
    readme_content = generate_readme_template(
        system_data.slug,
        system_data.name,
        system_data.description
    )

    main_created = await create_github_file(
        f"systems/{system_data.slug}/main.py",
        main_py_content,
        f"Create {system_data.slug} system scaffold"
    )

    if not main_created:
        raise RuntimeError("Failed to create main.py in GitHub")

    await create_github_file(
        f"systems/{system_data.slug}/README.md",
        readme_content,
        f"Add README for {system_data.slug}"
    )

    # Create database record
    db_system = SystemDB(
        slug=system_data.slug,
        name=system_data.name,
        description=system_data.description,
        api_key=api_key,
        status="scaffold",
        modal_url=None
    )

    db.add(db_system)
    await db.flush()
    await db.refresh(db_system)

    return SystemResponse.model_validate(db_system)


async def deploy_system(db: AsyncSession, slug: str) -> SystemResponse:
    """Deploy a system to Modal from GitHub"""
    # Get system from database
    result = await db.execute(
        select(SystemDB).where(SystemDB.slug == slug)
    )
    system = result.scalar_one_or_none()
    if not system:
        raise ValueError(f"System with slug '{slug}' not found")

    # Get main.py from GitHub
    try:
        main_py_content = await get_github_file(f"systems/{slug}/main.py")
    except ValueError:
        raise ValueError(f"main.py not found in GitHub for '{slug}'")

    # Write to temp file and deploy
    with tempfile.NamedTemporaryFile(mode='w', suffix='.py', delete=False) as f:
        f.write(main_py_content)
        temp_path = f.name

    try:
        env = os.environ.copy()
        env["MODAL_TOKEN_ID"] = settings.modal_token_id
        env["MODAL_TOKEN_SECRET"] = settings.modal_token_secret

        result = subprocess.run(
            ["modal", "deploy", temp_path],
            env=env,
            capture_output=True,
            text=True,
            timeout=120
        )

        if result.returncode != 0:
            raise RuntimeError(f"Modal deployment failed: {result.stderr}")

        # Parse Modal URL from output
        modal_url = None
        for line in result.stdout.split('\n'):
            if 'https://' in line and 'modal.run' in line:
                parts = line.split('https://')
                if len(parts) > 1:
                    url_part = 'https://' + parts[1].split()[0]
                    modal_url = url_part.rstrip('.,')
                    break

        if not modal_url:
            raise RuntimeError("Could not parse Modal URL from deployment output")

        # Update database
        system.modal_url = modal_url
        system.status = "deployed"
        await db.flush()
        await db.refresh(system)

        return SystemResponse.model_validate(system)

    finally:
        os.unlink(temp_path)


async def update_system(db: AsyncSession, slug: str, update_data: SystemUpdate) -> SystemResponse:
    """Update system metadata"""
    result = await db.execute(
        select(SystemDB).where(SystemDB.slug == slug)
    )
    system = result.scalar_one_or_none()
    if not system:
        raise ValueError(f"System with slug '{slug}' not found")

    update_dict = update_data.model_dump(exclude_unset=True)
    for field, value in update_dict.items():
        setattr(system, field, value)

    system.updated_at = datetime.utcnow()
    await db.flush()
    await db.refresh(system)

    return SystemResponse.model_validate(system)


async def delete_system(db: AsyncSession, slug: str, undeploy: bool = False) -> dict:
    """Delete a system from database and GitHub"""
    result = await db.execute(
        select(SystemDB).where(SystemDB.slug == slug)
    )
    system = result.scalar_one_or_none()
    if not system:
        raise ValueError(f"System with slug '{slug}' not found")

    undeploy_status = None

    # Optionally undeploy from Modal
    if undeploy and system.modal_url:
        try:
            env = os.environ.copy()
            env["MODAL_TOKEN_ID"] = settings.modal_token_id
            env["MODAL_TOKEN_SECRET"] = settings.modal_token_secret

            result = subprocess.run(
                ["modal", "app", "stop", slug],
                env=env,
                capture_output=True,
                text=True,
                timeout=30
            )
            undeploy_status = "success" if result.returncode == 0 else f"failed: {result.stderr}"
        except Exception as e:
            undeploy_status = f"error: {str(e)}"

    # Delete files from GitHub
    await delete_github_file(f"systems/{slug}/main.py", f"Delete {slug} system")
    await delete_github_file(f"systems/{slug}/README.md", f"Delete {slug} README")

    # Delete from database
    await db.delete(system)
    await db.flush()

    return {
        "status": "deleted",
        "slug": slug,
        "undeploy_status": undeploy_status
    }
