"""jubeat beyond the Ave: the matching lobby (service "lobby"). The work is done by JubeatGame (base.py); what belongs to this version only is set
here. Which profile a request gets is decided by the game version in its model (core_common: L44 since 2023-09-20 = 12)."""
from fastapi import APIRouter, Request

from modules.jubeat.base import JubeatGame

# jubeat.dll registers lobby_ave2 on the service "lobby", an address of its own
router = APIRouter(prefix="/lobby", tags=["lobby"])
router.model_whitelist = ["L44"]

game = JubeatGame(lightchat_maps={})  # the lobby needs no settings; the rooms live in base.py, shared


@router.post("/{gameinfo}/lobby_ave2/check")
async def lobby_ave2_check(request: Request):
    return await game.lobby_check(request)


@router.post("/{gameinfo}/lobby_ave2/entry")
async def lobby_ave2_entry(request: Request):
    return await game.lobby_entry(request)


@router.post("/{gameinfo}/lobby_ave2/refresh")
async def lobby_ave2_refresh(request: Request):
    return await game.lobby_refresh(request)


@router.post("/{gameinfo}/lobby_ave2/report")
async def lobby_ave2_report(request: Request):
    return await game.lobby_report(request)
