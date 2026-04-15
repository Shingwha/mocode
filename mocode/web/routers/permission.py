"""Permission approval endpoint."""

from fastapi import APIRouter, Depends, HTTPException

from ..deps import get_permission_handler
from ..schemas import PermissionResolveRequest, MessageResponse

router = APIRouter(prefix="/api", tags=["permission"])


@router.post("/permission/{request_id}", response_model=MessageResponse)
async def resolve_permission(
    request_id: str,
    req: PermissionResolveRequest,
    handler=Depends(get_permission_handler),
):
    """Approve or deny a pending permission request."""
    if not handler.resolve(request_id, req.response):
        raise HTTPException(status_code=404, detail="Permission request not found")
    return MessageResponse(ok=True)
