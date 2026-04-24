"""Config, provider, model, mode, and compact endpoints (v0.2)."""

from fastapi import APIRouter, Depends, HTTPException

from ..deps import get_app
from ..schemas import (
    ConfigResponse,
    ProviderInfo,
    ModelSwitchRequest,
    ProviderSwitchRequest,
    ProviderAddRequest,
    ProviderUpdateRequest,
    ModelAddRequest,
    ModelRemoveRequest,
    CompactResponse,
    MessageResponse,
)
from ...app import App

router = APIRouter(prefix="/api", tags=["config"])


def _build_config_response(app: App) -> ConfigResponse:
    providers = {}
    for key, p in app.providers.items():
        providers[key] = ProviderInfo(
            name=p.name,
            base_url=p.base_url,
            api_key=p.api_key or "",
            api_key_set=bool(p.api_key),
            models=list(p.models),
        )
    return ConfigResponse(
        current_provider=app.current_provider,
        current_model=app.current_model,
        providers=providers,
        max_tokens=app.config.max_tokens,
        tool_result_limit=app.config.tool_result_limit,
    )


@router.get("/config", response_model=ConfigResponse)
async def get_config(app: App = Depends(get_app)):
    """Read config."""
    return _build_config_response(app)


@router.put("/config/model", response_model=ConfigResponse)
async def switch_model(req: ModelSwitchRequest, app: App = Depends(get_app)):
    """Switch current model."""
    app.set_model(req.model, req.provider)
    return _build_config_response(app)


@router.put("/config/provider", response_model=ConfigResponse)
async def switch_provider(req: ProviderSwitchRequest, app: App = Depends(get_app)):
    """Switch current provider."""
    try:
        app.set_provider(req.provider, req.model)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    return _build_config_response(app)


@router.post("/config/providers", response_model=ConfigResponse)
async def add_provider(req: ProviderAddRequest, app: App = Depends(get_app)):
    """Add a new provider."""
    try:
        app.add_provider(
            key=req.key,
            name=req.name,
            base_url=req.base_url,
            api_key=req.api_key,
            models=req.models,
        )
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    return _build_config_response(app)


@router.put("/config/providers/{key}", response_model=ConfigResponse)
async def update_provider(
    key: str, req: ProviderUpdateRequest, app: App = Depends(get_app)
):
    """Update provider configuration."""
    try:
        app.update_provider(
            key=key,
            name=req.name,
            base_url=req.base_url,
            api_key=req.api_key,
            models=req.models,
        )
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    return _build_config_response(app)


@router.delete("/config/providers/{key}", response_model=ConfigResponse)
async def remove_provider(key: str, app: App = Depends(get_app)):
    """Remove a provider."""
    try:
        app.remove_provider(key)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    return _build_config_response(app)


@router.post("/config/models", response_model=ConfigResponse)
async def add_model(req: ModelAddRequest, app: App = Depends(get_app)):
    """Add model to provider."""
    try:
        app.add_model(req.model, req.provider)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    return _build_config_response(app)


@router.delete("/config/models/{model}", response_model=ConfigResponse)
async def remove_model(model: str, app: App = Depends(get_app)):
    """Remove model from provider."""
    try:
        app.remove_model(model)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    return _build_config_response(app)


@router.post("/compact", response_model=CompactResponse)
async def compact(app: App = Depends(get_app)):
    """Trigger context compaction."""
    if app.is_agent_busy:
        raise HTTPException(status_code=409, detail="Agent is busy")
    result = await app.compact()
    return CompactResponse(**result)
