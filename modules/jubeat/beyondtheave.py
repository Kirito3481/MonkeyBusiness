"""jubeat beyond the Ave: the requests of the service "local". The work is done by JubeatGame (base.py); what belongs to this version only is set
here. Which profile a request gets is decided by the game version in its model (core_common: L44 since 2023-09-20 = 12)."""
from fastapi import APIRouter, Request

from modules.jubeat.base import EVENT_CHAT, EVENT_ENDLESS, REWARD_BONUS_TUNE_GAUGE, REWARD_MUSIC, JubeatGame

# jubeat.dll registers every module but the lobby on the service "local"
router = APIRouter(prefix="/local", tags=["local"])
router.model_whitelist = ["L44"]

# LIGHT CHAT maps: map id -> event id -> (event type, sections (id, tube text, required jwatt, reward type, reward
# param, dialogue)); what the numbers mean is written next to EVENT_* / REWARD_* in base.py.
# Map 21 is the welcome map with its single 10 jwatt section. On 2026-09-22 that section got cleared and from the next
# credit on the game died when the music select came up (EXCEPTION thread-task.c:55): with map 21 done there was
# nothing left to go to. Map 99 (the game calls it CONCIERGE) is what comes after it.
# NOTE: once the last section of the last map is cleared there is again no map in progress.
LIGHTCHAT_MAPS = {
    21: {1: (EVENT_CHAT, [(1, "01BC00", 10, REWARD_MUSIC, 11000105, "jubeat beyond the Ave.へようこそ！")])},
    99: {1: (EVENT_ENDLESS, [(1, "0E7640", 300, REWARD_BONUS_TUNE_GAUGE, 100, "こちらをどうぞ！")])},
}

game = JubeatGame(lightchat_maps=LIGHTCHAT_MAPS)


@router.post("/{gameinfo}/shopinfo_ave2/regist")
async def shopinfo_ave2_regist(request: Request):
    return await game.shopinfo_regist(request)


@router.post("/{gameinfo}/gametop_ave2/get_info")
async def gametop_ave2_get_info(request: Request):
    return await game.gametop_get_info(request)


@router.post("/{gameinfo}/gametop_ave2/regist")
async def gametop_ave2_regist(request: Request):
    return await game.gametop_regist(request)


@router.post("/{gameinfo}/gametop_ave2/get_pdata")
async def gametop_ave2_get_pdata(request: Request):
    return await game.gametop_get_pdata(request)


@router.post("/{gameinfo}/gametop_ave2/get_mdata")
async def gametop_ave2_get_mdata(request: Request):
    return await game.gametop_get_mdata(request)


@router.post("/{gameinfo}/gametop_ave2/get_meeting")
async def gametop_ave2_get_meeting(request: Request):
    return await game.gametop_get_meeting(request)


@router.post("/{gameinfo}/gameend_ave2/regist")
async def gameend_ave2_regist(request: Request):
    return await game.gameend_regist(request)


@router.post("/{gameinfo}/gameend_ave2/final")
async def gameend_ave2_final(request: Request):
    return await game.gameend_final(request)


@router.post("/{gameinfo}/recommend_ave2/get_recommend")
async def recommend_ave2_get_recommend(request: Request):
    return await game.recommend_get_recommend(request)


@router.post("/{gameinfo}/demodata_ave2/get_info")
async def demodata_ave2_get_info(request: Request):
    return await game.demodata_get_info(request)


@router.post("/{gameinfo}/demodata_ave2/get_jbox_list")
async def demodata_ave2_get_jbox_list(request: Request):
    return await game.demodata_get_jbox_list(request)


@router.post("/{gameinfo}/demodata_ave2/get_news")
async def demodata_ave2_get_news(request: Request):
    return await game.demodata_get_news(request)


@router.post("/{gameinfo}/demodata_ave2/get_hitchart")
async def demodata_ave2_get_hitchart(request: Request):
    return await game.demodata_get_hitchart(request)


@router.post("/{gameinfo}/logger_ave2/report")
async def logger_ave2_report(request: Request):
    return await game.logger_report(request)


@router.post("/{gameinfo}/jbox_ave2/get_list")
async def jbox_ave2_get_list(request: Request):
    return await game.jbox_get_list(request)


@router.post("/{gameinfo}/jbox_ave2/get_agreement")
async def jbox_ave2_get_agreement(request: Request):
    return await game.jbox_get_agreement(request)
