"""``GET /v1/health`` — liveness + server ECDH public key + Langfuse status."""

from __future__ import annotations

from fastapi import APIRouter, Request

router = APIRouter()


@router.get("/health")
async def health(request: Request) -> dict[str, str]:
    ecdh = request.app.state.ecdh  # SessionECDH set in lifespan
    langfuse = "disabled"
    settings = request.app.state.settings
    if settings.langfuse_secret_key and settings.langfuse_public_key:
        langfuse = "ok"
    return {
        "status": "ok",
        "langfuse": langfuse,
        "ecdh_pub": ecdh.pub_b64,
    }
