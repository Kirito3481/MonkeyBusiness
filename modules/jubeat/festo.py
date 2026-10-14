"""jubeat festo (L44 builds 2018-09-05 .. 2022-05-24): the requests of the service "local". The work is done by
JubeatGame (base.py); JubeatFestoGame below is what festo answers differently, and the version's settings are here.
Which profile a request gets is decided by the game version in its model (core_common: L44 2018-09-05 .. 2022-05-24
= 10). The service "lobby" is festo_lobby.py, "slocal" and "netlog" are festo_lab.py.

festo's modules have no suffix (gametop, not gametop_ave2). Apart from that it differs from beyond the Ave in the
nodes of the shop info and of the player data - jubeat.dll 2022-05-24: shop info parser sub_10164590, player parser
sub_100E84E0, gameend player sender sub_100EF8A0; the module tables (off_102BC5F0 gametop, off_102BC580 gameend,
off_102BC370 demodata, off_102BC710 lobby, off_102BC82C shopinfo, off_102BC7BC logger, off_102BC7F4 recommend,
off_102BC458 jbox, off_102BC2B0 lab) name the methods. No LIGHT CHAT; instead TUNE RUN courses, the festo dungeon, the
EMO shop, team battle, hike event, travel and stamp events. The scores (get_mdata, parser sub_100F2410), the save
(gameend.regist, tune sender sub_100EF040) and the lobby have the same nodes as beyond the Ave's. What bemaniutils'
festo serves - it runs on real cabinets - is the reference for which nodes are sent: like there, team_battle,
hike_event, travel, stamp, emo_list and tip_list are served empty, which keeps those events off."""
import datetime
import json
import os

from fastapi import APIRouter, Request

from core_common import E
from modules.jubeat.base import JubeatGame, _int, _weekly_music_node

# jubeat.dll registers every module but lobby (service "lobby") and lab (service "slocal") on the service "local"
router = APIRouter(prefix="/local", tags=["local"])
router.model_whitelist = ["L44"]

# The events of the version by type: whether the event is open. Type 15 is open in bemaniutils' festo too ("something
# to do with maintenance mode?"); none of the others is served here (they need event data this server does not have).
EVENTS = {5: False, 6: False, 15: True, 22: False, 23: False, 33: False, 101: False, 102: False, 103: False, 104: False, 105: False, 106: False, 107: False, 108: False, 109: False}
EVENT_STATUS_OPEN = 0x1  # the player's event state: 0x1 open, 0x2 completed

# jubility pickup songs (64 x s32 bit flags over music_info pos_index), the list bemaniutils' festo has
HOT_MUSIC_LIST = [0] * 37 + [-4194304, -2080769, -1, -17, -3, -33554433, -242, -268435473, 1073741823, -1073748992, 15] + [0] * 16

# TUNE RUN courses: data/10/courses.json, the 52 courses of the 2022-05-24 data as bemaniutils' festo lists them
# ({id, name, course_type 1 permanent / 2 time based, clear_type 1 score per tune / 2 combined score / 3 hazard,
# difficulty, score, hard, hazard_type, music: 3 tunes, each a list of (music id, chart) the player picks from}).
# A time based course is always served as running until the end of next week.
COURSE_TYPE_TIME_BASED = 2
COURSE_STATUS_SEEN, COURSE_STATUS_PLAYED, COURSE_STATUS_CLEARED = 0x01, 0x02, 0x04
try:
    with open(os.path.join(os.path.dirname(os.path.abspath(__file__)), "data", "10", "courses.json"), encoding="utf-8") as _f:
        COURSES = json.load(_f)
except (OSError, ValueError):
    COURSES = []


def _course_etime(course):
    """When a course ends, in ms: the end of next week for a time based one, 0 (never) for a permanent one."""
    if course["course_type"] != COURSE_TYPE_TIME_BASED:
        return 0
    today = datetime.date.today()
    end = datetime.datetime.combine(today - datetime.timedelta(days=today.weekday()) + datetime.timedelta(days=14), datetime.time())
    return int(end.timestamp()) * 1000


def _course_nodes():
    # shop info parser sub_10164590, course_list/course: the attributes and every child below are read
    return [
        E.course(
            E.difficulty(course["difficulty"], __type="s32"),
            E.etime(_course_etime(course), __type="u64"),
            E.name(course["name"], __type="str"),
            E.tune_list(
                *[
                    E.tune(
                        E.seq_list(
                            *[
                                E.seq(
                                    E.music_id(music_id, __type="s32"),
                                    E.difficulty(chart, __type="s32"),
                                    E.is_secret(False, __type="bool"),
                                )
                                for music_id, chart in charts
                            ]
                        ),
                        no=str(number),
                    )
                    for number, charts in enumerate(course["music"], start=1)
                ]
            ),
            E.clear(
                E.ex_option(
                    E.is_hard(bool(course.get("hard")), __type="bool"),
                    E.hazard_type(course.get("hazard_type", 0), __type="s32"),
                ),
                E.score(course.get("score", 0), __type="s32"),
                E.reward_list(),
                type=str(course["clear_type"]),
            ),
            release_code="2022052400",
            version_id="0",
            id=str(course["id"]),
            course_type=str(course["course_type"]),
        )
        for course in COURSES
    ]


class JubeatFestoGame(JubeatGame):
    """festo's answers where they differ from beyond the Ave's (JubeatGame)."""

    MODULE_SUFFIX = ""

    def global_info(self, game_version):
        # The lists are 64 x s32 bit flags over music_info pos_index (16 x s32 for markers and themes): every song,
        # marker and theme allowed, nothing censored, no song shared or added by the server.
        return E.info(
            E.event_info(*[E.event(E.state(1 if enabled else 0, __type="u8"), type=str(event_type)) for event_type, enabled in EVENTS.items()]),
            E.genre_def_music(),
            E.black_jacket_list([0] * 64, __type="s32"),
            E.white_music_list([-1] * 64, __type="s32"),
            E.white_marker_list([-1] * 16, __type="s32"),
            E.white_theme_list([-1] * 16, __type="s32"),
            E.open_music_list([0] * 64, __type="s32"),
            E.shareable_music_list([0] * 64, __type="s32"),
            E.hot_music_list(HOT_MUSIC_LIST, __type="s32"),
            E.jbox(
                E.point(0, __type="s32"),
                E.emblem(E.normal(E.index(2, __type="s16")), E.premium(E.index(1, __type="s16"))),
            ),
            E.born(E.status(0, __type="s8"), E.year(0, __type="s16")),
            E.konami_logo_50th(E.is_available(True, __type="bool")),
            E.expert_option(E.is_available(True, __type="bool")),
            E.all_music_matching(E.is_available(True, __type="bool")),
            E.department(E.shop_list()),
            E.question_list(),
            E.course_list(*_course_nodes()),
            E.share_music(),
            _weekly_music_node(game_version),
            E.add_default_music_list([0] * 64, __type="s32"),
            E.team_battle(),
            E.emo_list(),
            E.hike_event(),
            E.tip_list(),
            E.travel(),
            E.stamp(),
        )

    def shop_nodes(self):
        return [E.tax_phase(1, __type="u8")]  # read when present, 1..3 (shopinfo receiver sub_10162040)

    def player_event_nodes(self, profile):
        return [E.event(E.state(EVENT_STATUS_OPEN if enabled else 0, __type="u8"), type=str(event_type)) for event_type, enabled in EVENTS.items()]

    def player_nodes(self, profile):
        # The player parser (sub_100E84E0) reads these after the shared nodes. course_list carries the status of every
        # course (seen / played / cleared bits) and the category_list the categories of the TUNE RUN select.
        dungeon = profile.get("festo_dungeon") or {}
        status = profile.get("course_status") or {}
        return [
            E.rivallist(),
            E.lab_edit_seq(count="0"),
            E.official_news(E.news_list()),
            E.history(count="0"),
            E.emo_list(),
            E.course_list(
                *[E.course(E.status(int(status.get(str(course["id"]), 0)), __type="s8"), id=str(course["id"])) for course in COURSES],
                E.category_list(*[E.category(E.is_display(True, __type="bool"), id=str(category)) for category in range(1, 7)]),
            ),
            E.department(E.shop_list()),
            E.stamp(E.sheet_list()),
            E.festo_dungeon(
                E.phase(int(dungeon.get("phase", 0)), __type="s32"),
                E.clear_flag(int(dungeon.get("clear_flag", 0)), __type="s32"),
            ),
        ]

    def save_player_nodes(self, profile, player):
        # gameend.regist's data/player: course_list/course@id/status (seen, played) and select_course@id/is_cleared
        # for the course of this credit, festo_dungeon/phase and clear_flag
        status = profile.setdefault("course_status", {})
        for course in player.findall("course_list/course"):
            if course.get("id") is not None:
                status[course.get("id")] = (int(status.get(course.get("id"), 0)) | _int(course, "status")) & 0x07
        select_course = player.find("select_course")
        if select_course is not None and select_course.get("id") is not None and _int(select_course, "is_cleared"):
            status[select_course.get("id")] = int(status.get(select_course.get("id"), 0)) | COURSE_STATUS_CLEARED
        dungeon = player.find("festo_dungeon")
        if dungeon is not None:
            profile["festo_dungeon"] = {"phase": _int(dungeon, "phase"), "clear_flag": _int(dungeon, "clear_flag")}


game = JubeatFestoGame(lightchat_maps={})  # festo has no LIGHT CHAT


@router.post("/{gameinfo}/shopinfo/regist")
async def shopinfo_regist(request: Request):
    return await game.shopinfo_regist(request)


@router.post("/{gameinfo}/gametop/get_info")
async def gametop_get_info(request: Request):
    return await game.gametop_get_info(request)


@router.post("/{gameinfo}/gametop/regist")
async def gametop_regist(request: Request):
    return await game.gametop_regist(request)


@router.post("/{gameinfo}/gametop/get_pdata")
async def gametop_get_pdata(request: Request):
    return await game.gametop_get_pdata(request)


@router.post("/{gameinfo}/gametop/get_mdata")
async def gametop_get_mdata(request: Request):
    return await game.gametop_get_mdata(request)


@router.post("/{gameinfo}/gametop/get_meeting")
async def gametop_get_meeting(request: Request):
    return await game.gametop_get_meeting(request)


@router.post("/{gameinfo}/gameend/regist")
async def gameend_regist(request: Request):
    return await game.gameend_regist(request)


@router.post("/{gameinfo}/gameend/final")
async def gameend_final(request: Request):
    return await game.gameend_final(request)


@router.post("/{gameinfo}/recommend/get_recommend")
async def recommend_get_recommend(request: Request):
    return await game.recommend_get_recommend(request)


@router.post("/{gameinfo}/demodata/get_info")
async def demodata_get_info(request: Request):
    return await game.demodata_get_info(request)


@router.post("/{gameinfo}/demodata/get_jbox_list")
async def demodata_get_jbox_list(request: Request):
    return await game.demodata_get_jbox_list(request)


@router.post("/{gameinfo}/demodata/get_news")
async def demodata_get_news(request: Request):
    return await game.demodata_get_news(request)


@router.post("/{gameinfo}/demodata/get_hitchart")
async def demodata_get_hitchart(request: Request):
    return await game.demodata_get_hitchart(request)


@router.post("/{gameinfo}/logger/report")
async def logger_report(request: Request):
    return await game.logger_report(request)


@router.post("/{gameinfo}/jbox/get_list")
async def jbox_get_list(request: Request):
    return await game.jbox_get_list(request)


@router.post("/{gameinfo}/jbox/get_agreement")
async def jbox_get_agreement(request: Request):
    return await game.jbox_get_agreement(request)
