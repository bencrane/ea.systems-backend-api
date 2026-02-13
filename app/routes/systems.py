from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select
from typing import List
import httpx
from app.database import get_db
from app.models.system import SystemCreate, SystemUpdate, SystemResponse, SystemDB
from app.services.system_service import create_system, deploy_system, update_system, delete_system
from app.services.chat_service import ChatRequest, ChatResponse, IntroResponse, handle_chat_message, handle_intro_message

router = APIRouter(prefix="/systems", tags=["systems"])


@router.post("/create", response_model=SystemResponse, status_code=201)
async def create_system_endpoint(
    system_data: SystemCreate,
    db: AsyncSession = Depends(get_db)
):
    """
    Create a new automation system
    
    Creates:
    - Directory structure at /systems/{slug}/
    - Templated main.py (Modal function stub)
    - README.md with system documentation
    - Database record with status 'scaffold'
    - Auto-generated API key for authentication
    """
    try:
        system = await create_system(db, system_data)
        return system
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Failed to create system: {str(e)}")


@router.post("/{slug}/deploy", response_model=SystemResponse)
async def deploy_system_endpoint(
    slug: str,
    db: AsyncSession = Depends(get_db)
):
    """
    Deploy a system to Modal
    
    - Deploys the system's main.py to Modal as a serverless function
    - Captures the Modal endpoint URL
    - Updates database record with modal_url and status 'deployed'
    
    Requires:
    - System must exist (status 'scaffold')
    - Modal credentials configured (MODAL_TOKEN_ID, MODAL_TOKEN_SECRET)
    """
    try:
        system = await deploy_system(db, slug)
        return system
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e))
    except RuntimeError as e:
        raise HTTPException(status_code=500, detail=str(e))
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Deployment failed: {str(e)}")


@router.post("/{slug}/chat", response_model=ChatResponse)
async def chat_with_system(
    slug: str,
    payload: ChatRequest,
    db: AsyncSession = Depends(get_db),
):
    """
    AI-powered intake chat for a system.

    Fetches the system's input schema from its Modal OpenAPI spec,
    then uses Gemini to conversationally collect all required fields.

    When all inputs are gathered and confirmed, the response will contain:
    {"ready": true, "payload": {...}}
    """
    try:
        return await handle_chat_message(db, slug, payload)
    except ValueError as e:
        error_msg = str(e)
        if "not found" in error_msg:
            raise HTTPException(status_code=404, detail=error_msg)
        raise HTTPException(status_code=400, detail=error_msg)
    except httpx.HTTPStatusError as e:
        raise HTTPException(
            status_code=502,
            detail=f"Failed to fetch system schema from Modal: {e.response.status_code}",
        )
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Chat failed: {str(e)}")


@router.get("/{slug}/intro", response_model=IntroResponse)
async def get_system_intro(
    slug: str,
    db: AsyncSession = Depends(get_db),
):
    """
    Get a welcome message for a system's chat interface.

    Returns a generated intro message based on the system's chat_context and schema.
    Call this when the chat page loads to display the first message.
    """
    try:
        return await handle_intro_message(db, slug)
    except ValueError as e:
        error_msg = str(e)
        if "not found" in error_msg:
            raise HTTPException(status_code=404, detail=error_msg)
        raise HTTPException(status_code=400, detail=error_msg)
    except httpx.HTTPStatusError as e:
        raise HTTPException(
            status_code=502,
            detail=f"Failed to fetch system schema from Modal: {e.response.status_code}",
        )
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Intro generation failed: {str(e)}")


@router.get("", response_model=List[SystemResponse])
async def list_systems(
    db: AsyncSession = Depends(get_db),
    status: str = None
):
    """
    List all systems with optional filtering

    Query parameters:
    - status: Filter by status (scaffold, deployed, active, inactive)
    """
    query = select(SystemDB)

    if status:
        query = query.where(SystemDB.status == status)

    result = await db.execute(query)
    systems = result.scalars().all()

    return [SystemResponse.model_validate(s) for s in systems]


@router.get("/{slug}", response_model=SystemResponse)
async def get_system(
    slug: str,
    db: AsyncSession = Depends(get_db)
):
    """
    Get a single system by slug
    """
    result = await db.execute(
        select(SystemDB).where(SystemDB.slug == slug)
    )
    system = result.scalar_one_or_none()
    
    if not system:
        raise HTTPException(status_code=404, detail=f"System '{slug}' not found")
    
    return SystemResponse.model_validate(system)


@router.patch("/{slug}", response_model=SystemResponse)
async def update_system_endpoint(
    slug: str,
    update_data: SystemUpdate,
    db: AsyncSession = Depends(get_db)
):
    """
    Update system metadata

    Updatable fields:
    - name: Display name
    - description: System description
    - status: System status (scaffold, deployed, active, inactive)

    Note: slug and api_key cannot be changed
    """
    try:
        system = await update_system(db, slug, update_data)
        return system
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e))
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Update failed: {str(e)}")


@router.delete("/{slug}")
async def delete_system_endpoint(
    slug: str,
    undeploy: bool = Query(False, description="Attempt to undeploy from Modal before deleting"),
    db: AsyncSession = Depends(get_db)
):
    """
    Delete a system
    
    - Removes system directory and files
    - Deletes database record
    - Optionally undeploys from Modal (if undeploy=true)
    
    Warning: This action cannot be undone
    """
    try:
        result = await delete_system(db, slug, undeploy)
        return result
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e))
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Deletion failed: {str(e)}")
