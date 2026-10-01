from fastapi import FastAPI
from pydantic import BaseModel

from app.api.triage import router as triage_router
from app.config import get_settings

_docs_off = get_settings().app_env != "local"
app = FastAPI(
    title="Priority Agent",
    docs_url=None if _docs_off else "/docs",
    redoc_url=None if _docs_off else "/redoc",
    openapi_url=None if _docs_off else "/openapi.json",
)
app.include_router(triage_router)


class HealthResponse(BaseModel):
    status: str


@app.get("/health", response_model=HealthResponse)
async def health() -> HealthResponse:
    return HealthResponse(status="ok")
