"""Pydantic request/response models for the web API."""

from pydantic import BaseModel


# --- Chat ---

class ChatRequest(BaseModel):
    message: str
    media: list[str] | None = None


class StatusResponse(BaseModel):
    busy: bool
    model: str
    provider: str


class MessageResponse(BaseModel):
    ok: bool


# --- Permission ---

class PermissionResolveRequest(BaseModel):
    response: str


# --- Sessions ---

class SessionSummary(BaseModel):
    id: str
    created_at: str
    updated_at: str
    workdir: str
    model: str
    provider: str
    message_count: int


class SessionDetail(SessionSummary):
    messages: list[dict]


class SessionListResponse(BaseModel):
    sessions: list[SessionSummary]


class SessionSaveResponse(BaseModel):
    session: SessionSummary


class HistoryResponse(BaseModel):
    messages: list[dict]


# --- Config ---

class ModelSwitchRequest(BaseModel):
    model: str
    provider: str | None = None


class ProviderSwitchRequest(BaseModel):
    provider: str
    model: str | None = None


class ModeSwitchRequest(BaseModel):
    mode: str


class ProviderAddRequest(BaseModel):
    key: str
    name: str
    base_url: str
    api_key: str = ""
    models: list[str] | None = None


class ProviderUpdateRequest(BaseModel):
    name: str | None = None
    base_url: str | None = None
    api_key: str | None = None


class ModelAddRequest(BaseModel):
    model: str
    provider: str | None = None


class ModelRemoveRequest(BaseModel):
    model: str
    provider: str | None = None


class ProviderInfo(BaseModel):
    name: str
    base_url: str
    api_key: str  # 实际 API key 值（仅用于编辑表单回显）
    api_key_set: bool  # 是否已设置 key
    models: list[str]


class ConfigResponse(BaseModel):
    current_provider: str
    current_model: str
    providers: dict[str, ProviderInfo]
    max_tokens: int
    tool_result_limit: int
    mode: str


class CompactResponse(BaseModel):
    action: str
    old_count: int
    new_count: int
