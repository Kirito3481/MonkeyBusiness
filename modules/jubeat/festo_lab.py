"""jubeat festo: the services "slocal" and "netlog". jubeat.dll 2022-05-24 registers its lab module (the jubeat lab,
player made charts: get_fumen, get_ranking, set_music_result) on "slocal", and avs2-ea3 posts ins.netlog to "netlog"
when ea3-config switches its netlog appender on (bemaniutils found festo dying at coin-in when that service is not
offered). A router's tags are the service names it is offered under, so this one serves both. Only lab.get_ranking
and ins.netlog are answered (JubeatGame, base.py). With url_slash 0 ins.netlog goes to modules/core/ins.py, so the
route here keeps another name."""
from fastapi import APIRouter, Request

from modules.jubeat.base import JubeatGame

router = APIRouter(prefix="/slocal", tags=["slocal", "netlog"])
router.model_whitelist = ["L44"]

game = JubeatGame(lightchat_maps={}, module_suffix="")


@router.post("/{gameinfo}/lab/get_ranking")
async def lab_get_ranking(request: Request):
    return await game.lab_get_ranking(request)


@router.post("/{gameinfo}/ins/netlog")
async def festo_ins_netlog(request: Request):
    return await game.ins_netlog(request)
