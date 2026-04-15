"""Config, provider, model, mode, and compact endpoints."""

from dataclasses import asdict

from fastapi import APIRouter, Depends, HTTPException

from ..deps import get_core
from ..schemas import (
    ConfigResponse,
    ProviderInfo,
    ModelSwitchRequest,
    ProviderSwitchRequest,
    ModeSwitchRequest,
    ProviderAddRequest,
    ProviderUpdateRequest,
    ModelAddRequest,
    ModelRemoveRequest,
    CompactResponse,
    MessageResponse,
)

router = APIRouter(prefix="/api", tags=["config"])


def _build_config_response(core) -> ConfigResponse:
    providers = {}
    for key, p in core.providers.items():
        providers[key] = ProviderInfo(
            name=p.name,
            base_url=p.base_url,
            api_key=p.api_key or "",  # 返回实际 API key（若为空则返回空字符串）
            api_key_set=bool(p.api_key),
            models=list(p.models),
        )
    return ConfigResponse(
        current_provider=core.current_provider,
        current_model=core.current_model,
        providers=providers,
        max_tokens=core.config.max_tokens,
        tool_result_limit=core.config.tool_result_limit,
        mode=core.config.current_mode,
    )


@router.get("/config", response_model=ConfigResponse)
async def get_config(core=Depends(get_core)):
    """Read config (api_key masked)."""
    return _build_config_response(core)


@router.put("/config/model", response_model=ConfigResponse)
async def switch_model(req: ModelSwitchRequest, core=Depends(get_core)):
    """Switch current model."""
    core.set_model(req.model, req.provider)
    return _build_config_response(core)


@router.put("/config/provider", response_model=ConfigResponse)
async def switch_provider(req: ProviderSwitchRequest, core=Depends(get_core)):
    """Switch current provider."""
    try:
        core.set_provider(req.provider, req.model)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    return _build_config_response(core)


@router.put("/config/mode", response_model=ConfigResponse)
async def switch_mode(req: ModeSwitchRequest, core=Depends(get_core)):
    """Switch mode (normal/yolo)."""
    if not core.config.set_mode(req.mode):
        raise HTTPException(status_code=400, detail=f"Unknown mode: {req.mode}")
    return _build_config_response(core)


@router.post("/config/providers", response_model=ConfigResponse)
async def add_provider(req: ProviderAddRequest, core=Depends(get_core)):
    """Add a new provider."""
    try:
        core.add_provider(
            key=req.key,
            name=req.name,
            base_url=req.base_url,
            api_key=req.api_key,
            models=req.models,
        )
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    return _build_config_response(core)


@router.put("/config/providers/{key}", response_model=ConfigResponse)
async def update_provider(
    key: str, req: ProviderUpdateRequest, core=Depends(get_core)
):
    """Update provider configuration."""
    try:
        core.update_provider(
            key=key,
            name=req.name,
            base_url=req.base_url,
            api_key=req.api_key,
        )
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    return _build_config_response(core)


@router.delete("/config/providers/{key}", response_model=ConfigResponse)
async def remove_provider(key: str, core=Depends(get_core)):
    """Remove a provider."""
    try:
        core.remove_provider(key)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    return _build_config_response(core)


@router.post("/config/models", response_model=ConfigResponse)
async def add_model(req: ModelAddRequest, core=Depends(get_core)):
    """Add model to provider."""
    try:
        core.add_model(req.model, req.provider)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    return _build_config_response(core)


@router.delete("/config/models/{model}", response_model=ConfigResponse)
async def remove_model(model: str, core=Depends(get_core)):
    """Remove model from provider."""
    try:
        core.remove_model(model)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    return _build_config_response(core)


@router.post("/compact", response_model=CompactResponse)
async def compact(core=Depends(get_core)):
    """Trigger context compaction."""
    if core.is_agent_busy:
        raise HTTPException(status_code=409, detail="Agent is busy")
    result = await core.compact()
    return CompactResponse(**result)
