"""jubeat festo: the matching lobby (service "lobby"). The work is done by JubeatGame (base.py), whose lobby answers
festo reads as they are (jubeat.dll 2022-05-24 lobby parsers sub_10160280 check, sub_10160880 entry, sub_10160DB0
refresh, sub_10161170 report: the nodes of beyond the Ave's; festo only has no matching password). The rooms live in
base.py, shared, and a room only takes cabinets of the same game version."""
from fastapi import APIRouter, Request

from modules.jubeat.base import JubeatGame

# jubeat.dll registers lobby on the service "lobby", an address of its own
router = APIRouter(prefix="/lobby", tags=["lobby"])
router.model_whitelist = ["L44"]

game = JubeatGame(lightchat_maps={}, module_suffix="")  # the lobby needs no settings


@router.post("/{gameinfo}/lobby/check")
async def lobby_check(request: Request):
    return await game.lobby_check(request)


@router.post("/{gameinfo}/lobby/entry")
async def lobby_entry(request: Request):
    return await game.lobby_entry(request)


@router.post("/{gameinfo}/lobby/refresh")
async def lobby_refresh(request: Request):
    return await game.lobby_refresh(request)


@router.post("/{gameinfo}/lobby/report")
async def lobby_report(request: Request):
    return await game.lobby_report(request)
