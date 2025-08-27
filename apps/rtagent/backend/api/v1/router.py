"""
API V1 Router
=============

Main router for API v1 endpoints.
"""

from fastapi import APIRouter
from .endpoints import calls, health, media, realtime, voice_live

# Create v1 router
v1_router = APIRouter(prefix="/api/v1")

# Include endpoint routers with specific tags for better organization
# see the api/swagger_docs.py for the swagger tags configuration
v1_router.include_router(health.router, tags=["health"])
v1_router.include_router(calls.router, prefix="/calls", tags=["Call Management"])
v1_router.include_router(
    media.router, prefix="/media", tags=["ACS Media Session", "WebSocket"]
)
v1_router.include_router(
    realtime.router, prefix="/realtime", tags=["Real-time Communication", "WebSocket"]
)
v1_router.include_router(
    voice_live.router, prefix="/voice-live", tags=["Voice Live", "WebSocket"]
)
