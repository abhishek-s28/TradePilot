"""Research & analyst feed API."""
from __future__ import annotations

from fastapi import APIRouter

from app.services.research_service import ResearchService

router = APIRouter(prefix="/research", tags=["research"])
_svc = ResearchService()


@router.get("/brief")
async def get_brief():
    """Full market research brief from all 10 analyst perspectives."""
    return await _svc.generate_brief()


@router.get("/analysts")
async def get_analysts():
    """List all research analysts and their specialties."""
    return await _svc.get_analysts()
