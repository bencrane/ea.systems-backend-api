import os
import secrets
import subprocess
import shutil
from datetime import datetime
from pathlib import Path
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select
from app.models.system import SystemDB, SystemCreate, SystemUpdate, SystemResponse
from app.config import settings


def generate_api_key() -> str:
    """Generate a random API key for system authentication"""
    return f"sk_{secrets.token_urlsafe(32)}"


def create_system_directory(slug: str) -> Path:
    """Create directory structure for a system"""
    system_dir = Path(f"systems/{slug}")
    system_dir.mkdir(parents=True, exist_ok=True)
    return system_dir


def generate_main_py_template(slug: str, api_key: str) -> str:
    """Generate templated main.py for Modal function"""
    return f'''import modal
from fastapi import FastAPI, Header, HTTPException, Request
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


def generate_readme_template(slug: str, name: str, description: str, category: str) -> str:
    """Generate templated README.md for system"""
    return f'''# {name}

**Category:** {category}  
**Slug:** `{slug}`

## Description

{description}

## Usage

This system is deployed as a Modal serverless function. It accepts POST requests with the following structure:

### Request

```json
{{
  "client_id": "uuid-here",
  // additional fields specific to this system
}}
```

### Headers

- `X-API-Key`: Required authentication header (contact admin for key)

### Response

```json
{{
  "status": "ok",
  "system": "{slug}",
  "client_id": "uuid-here",
  "data": {{
    // system-specific response data
  }}
}}
```

## Development

1. Edit `main.py` to implement system logic
2. Test locally with `modal serve main.py`
3. Deploy with `modal deploy main.py`

## Integration

This system is designed to be triggered by n8n or Pipedream workflows. The Modal endpoint URL will be provided after deployment.
'''


async def create_system(db: AsyncSession, system_data: SystemCreate) -> SystemResponse:
    """
    Create a new system: directory structure, database record
    """
    # Check if slug already exists
    result = await db.execute(
        select(SystemDB).where(SystemDB.slug == system_data.slug)
    )
    existing = result.scalar_one_or_none()
    if existing:
        raise ValueError(f"System with slug '{system_data.slug}' already exists")
    
    # Generate API key
    api_key = generate_api_key()
    
    # Create directory structure
    system_dir = create_system_directory(system_data.slug)
    
    # Create main.py
    main_py_content = generate_main_py_template(system_data.slug, api_key)
    (system_dir / "main.py").write_text(main_py_content)
    
    # Create README.md
    readme_content = generate_readme_template(
        system_data.slug,
        system_data.name,
        system_data.description,
        system_data.category
    )
    (system_dir / "README.md").write_text(readme_content)
    
    # Create database record
    db_system = SystemDB(
        slug=system_data.slug,
        name=system_data.name,
        category=system_data.category,
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
    """
    Deploy a system to Modal and capture the endpoint URL
    """
    # Get system from database
    result = await db.execute(
        select(SystemDB).where(SystemDB.slug == slug)
    )
    system = result.scalar_one_or_none()
    if not system:
        raise ValueError(f"System with slug '{slug}' not found")
    
    # Check if system directory exists
    system_dir = Path(f"systems/{slug}")
    main_py_path = system_dir / "main.py"
    if not main_py_path.exists():
        raise ValueError(f"System directory or main.py not found for '{slug}'")
    
    # Set Modal token environment variables for deployment
    env = os.environ.copy()
    env["MODAL_TOKEN_ID"] = settings.modal_token_id
    env["MODAL_TOKEN_SECRET"] = settings.modal_token_secret
    
    try:
        # Deploy to Modal using CLI
        # This will deploy the app and return the deployment info
        result = subprocess.run(
            ["modal", "deploy", str(main_py_path)],
            cwd=str(system_dir.parent.parent),
            env=env,
            capture_output=True,
            text=True,
            timeout=120
        )
        
        if result.returncode != 0:
            raise RuntimeError(f"Modal deployment failed: {result.stderr}")
        
        # Parse the output to extract the web endpoint URL
        # Modal deploy output includes the URL in format: https://<workspace>--<app>-<function>.modal.run
        output = result.stdout
        modal_url = None
        
        for line in output.split('\n'):
            if 'https://' in line and 'modal.run' in line:
                # Extract URL from the line
                parts = line.split('https://')
                if len(parts) > 1:
                    url_part = 'https://' + parts[1].split()[0]
                    modal_url = url_part.rstrip('.,')
                    break
        
        if not modal_url:
            # If we can't parse the URL, construct it based on Modal's URL pattern
            # Format: https://<workspace>--<slug>-handler.modal.run
            # We'll need to make a best guess or require manual entry
            raise RuntimeError("Could not parse Modal URL from deployment output. Please check Modal dashboard.")
        
        # Update database record
        system.modal_url = modal_url
        system.status = "deployed"
        await db.flush()
        await db.refresh(system)
        
        return SystemResponse.model_validate(system)
        
    except subprocess.TimeoutExpired:
        raise RuntimeError("Modal deployment timed out after 120 seconds")
    except Exception as e:
        raise RuntimeError(f"Deployment error: {str(e)}")


async def update_system(db: AsyncSession, slug: str, update_data: SystemUpdate) -> SystemResponse:
    """
    Update system metadata (name, category, description, status)
    Note: slug and api_key cannot be changed
    """
    # Get system from database
    result = await db.execute(
        select(SystemDB).where(SystemDB.slug == slug)
    )
    system = result.scalar_one_or_none()
    if not system:
        raise ValueError(f"System with slug '{slug}' not found")
    
    # Update fields that are provided
    update_dict = update_data.model_dump(exclude_unset=True)
    
    for field, value in update_dict.items():
        setattr(system, field, value)
    
    # Update timestamp
    system.updated_at = datetime.utcnow()
    
    await db.flush()
    await db.refresh(system)
    
    return SystemResponse.model_validate(system)


async def delete_system(db: AsyncSession, slug: str, undeploy: bool = False) -> dict:
    """
    Delete a system from database and filesystem
    
    Args:
        slug: System slug to delete
        undeploy: If True, attempt to undeploy from Modal before deleting
    
    Returns:
        dict with deletion status
    """
    # Get system from database
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
            
            # Delete the Modal app
            result = subprocess.run(
                ["modal", "app", "stop", slug],
                env=env,
                capture_output=True,
                text=True,
                timeout=30
            )
            
            if result.returncode == 0:
                undeploy_status = "success"
            else:
                undeploy_status = f"failed: {result.stderr}"
        except Exception as e:
            undeploy_status = f"error: {str(e)}"
    
    # Delete directory structure
    system_dir = Path(f"systems/{slug}")
    if system_dir.exists():
        shutil.rmtree(system_dir)
        filesystem_deleted = True
    else:
        filesystem_deleted = False
    
    # Delete from database
    await db.delete(system)
    await db.flush()
    
    return {
        "status": "deleted",
        "slug": slug,
        "filesystem_deleted": filesystem_deleted,
        "undeploy_status": undeploy_status
    }
