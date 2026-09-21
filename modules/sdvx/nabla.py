"""SOUND VOLTEX NABLA: game.sv7_*. The work is done by SdvxGame (base.py); what belongs to this version only
is set here (the event flags; its skill courses are data, data/7/skill_courses.json). Which profile and which
data a request gets is decided by the game version in its model (core_common), not by these method names."""
from fastapi import APIRouter, Request

from modules.sdvx.base import SdvxGame

# NABLA's soundvoltex.dll registers "game" on the service "local"; the address stays /local2 like EXCEED GEAR's.
router = APIRouter(prefix="/local2", tags=["local"])
router.model_whitelist = ["KFC"]

# sv_common event/info/event_id: what is switched on in this version ("NAME\tparameters" where a flag takes some)
EVENTS = [
    "DEMOGAME_PLAY",
    "MATCHING_MODE",
    "MATCHING_MODE_FREE_IP",
    "LEVEL_LIMIT_EASING",
    "ACHIEVEMENT_ENABLE",
    "APICAGACHADRAW\t30",
    "VOLFORCE_ENABLE",
    "AKANAME_ENABLE",
    "CONTINUATION",
    # "APPEAL_CARD_GEN_PRICE\t100",
    # "APPEAL_CARD_GEN_NEW_PRICE\t200",
    # "APPEAL_CARD_UNLOCK\t0,20170914,0,20171014,0,20171116,0,20180201,0,20180607,0,20181206,0,20200326,0,20200611,4,10140732,6,10150431",
    "FAVORITE_APPEALCARD_MAX\t200",
    "FAVORITE_MUSIC_MAX\t200",
    "EVENTDATE_APRILFOOL",
    "KONAMI_50TH_LOGO",
    "OMEGA_ARS_ENABLE",
    "DISABLE_MONITOR_ID_CHECK",
    "SKILL_ANALYZER_ABLE",
    "BLASTER_ABLE",
    "STANDARD_UNLOCK_ENABLE",
    "PLAYERJUDGEADJ_ENABLE",
    "MIXID_INPUT_ENABLE",
    "EVENTDATE_ONIGO",
    "EVENTDATE_GOTT",
    "GENERATOR_ABLE",
    "CREW_SELECT_ABLE",
    "PREMIUM_TIME_ENABLE",
    "OMEGA_ENABLE\t1,2,3,4,5,6,7,8,9",
    "HEXA_ENABLE\t1,2,3,4,5,6,7,8,9,10,11",
    "HEXA_OVERDRIVE_ENABLE\t8",
    "MEGAMIX_ENABLE",
    "S_PUC_EFFECT_ENABLE",
    "SUPER_RANDOM_ACTIVE",
    "PLAYER_RADAR_ENABLE",
    "APRIL_RAINBOW_LINE_ACTIVE",
    "USE_CUDA_VIDEO_PRESENTER",
    "CHARACTER_IGNORE_DISABLE\t122,123,131,139,140,143,149,160,162,163,164,167,170,174",
    "STAMP_IGNORE_DISABLE\t273~312,773~820,993~1032,1245~1284,1469~1508,1585~1632,1633~1672,1737~1776,1777~1816,1897~1936",
    "SUBBG_IGNORE_DISABLE\t166~185,281~346,369~381,419~438,464~482,515~552,595~616,660~673,714~727",
]

game = SdvxGame(version="7", events=EVENTS)


@router.post("/{gameinfo}/game/sv7_common")
async def game_sv7_common(request: Request):
    return await game.common(request)


@router.post("/{gameinfo}/game/sv7_new")
async def game_sv7_new(request: Request):
    return await game.new(request)


@router.post("/{gameinfo}/game/sv7_load")
async def game_sv7_load(request: Request):
    return await game.load(request)


@router.post("/{gameinfo}/game/sv7_load_m")
async def game_sv7_load_m(request: Request):
    return await game.load_m(request)


@router.post("/{gameinfo}/game/sv7_save")
async def game_sv7_save(request: Request):
    return await game.save(request)


@router.post("/{gameinfo}/game/sv7_save_m")
async def game_sv7_save_m(request: Request):
    return await game.save_m(request)


@router.post("/{gameinfo}/game/sv7_hiscore")
async def game_sv7_hiscore(request: Request):
    return await game.hiscore(request)


@router.post("/{gameinfo}/game/sv7_lounge")
async def game_sv7_lounge(request: Request):
    return await game.lounge(request)


@router.post("/{gameinfo}/game/sv7_shop")
async def game_sv7_shop(request: Request):
    return await game.shop(request)


@router.post("/{gameinfo}/game/sv7_load_r")
async def game_sv7_load_r(request: Request):
    return await game.load_r(request)


@router.post("/{gameinfo}/game/sv7_frozen")
async def game_sv7_frozen(request: Request):
    return await game.frozen(request)


@router.post("/{gameinfo}/game/sv7_save_c")
async def game_sv7_save_c(request: Request):
    return await game.save_c(request)


@router.post("/{gameinfo}/game/sv7_save_e")
async def game_sv7_save_e(request: Request):
    return await game.save_e(request)


@router.post("/{gameinfo}/game/sv7_save_mega")
async def game_sv7_save_mega(request: Request):
    return await game.save_mega(request)


@router.post("/{gameinfo}/game/sv7_play_e")
async def game_sv7_play_e(request: Request):
    return await game.play_e(request)


@router.post("/{gameinfo}/game/sv7_play_s")
async def game_sv7_play_s(request: Request):
    return await game.play_s(request)


@router.post("/{gameinfo}/game/sv7_entry_s")
async def game_sv7_entry_s(request: Request):
    return await game.entry_s(request)


@router.post("/{gameinfo}/game/sv7_entry_e")
async def game_sv7_entry_e(request: Request):
    return await game.entry_e(request)


@router.post("/{gameinfo}/game/sv7_log")
async def game_sv7_log(request: Request):
    return await game.log(request)
