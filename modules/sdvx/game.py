import xml.etree.ElementTree as ET
from os import path

from tinydb import Query, where

import config
import datetime
import random
import time

from fastapi import APIRouter, Request, Response

from core_common import core_process_request, core_prepare_response, E
from core_database import get_db

# soundvoltex.dll registers "game" on "local2" up to EXCEED GEAR and on "local" in NABLA
# (sv7); ess.dll (eventlog) stays on "local2". Both names lead to this router.
router = APIRouter(prefix="/local2", tags=["local2", "local"])
router.model_whitelist = ["KFC"]


def get_profile(cid):
    return get_db().table("sdvx_profile").get(where("card") == cid)


def get_game_profile(cid, game_version):
    profile = get_profile(cid)
    if profile is None:
        return None  # a card this game has never seen: the game registers it with sv_new

    return profile["version"].get(str(game_version), None)


def player_card(root):
    # The player record is keyed by the card number. The game sends it as refid and, since
    # cardmng hands the same value out as dataid, as dataid too - but refid is the one that is
    # always the card (cardmng.bindmodel used to answer dataid 1), so it goes first.
    return root.findtext("refid") or root.findtext("dataid")


# Play statistics. The game only reads them (sv_load: play_count, day_count, today_count,
# play_chain, max_play_chain, week_count, week_play_count, week_chain, max_week_chain) and never
# sends them back, so they are kept here: one credit is counted per sv_save, the request the
# game sends once when a credit ends. Days are server days, weeks run Monday to Sunday.
PLAY_STATS_REPEAT_SECONDS = 5  # a save repeated this soon is the same credit sent again


def _monday(day):
    return day - datetime.timedelta(days=day.weekday())


def _last_play_day(stats):
    try:
        return datetime.date.fromisoformat(stats.get("last_date", ""))
    except ValueError:
        return None


def play_stats_now(game_profile, today=None):
    """The counters as they stand today: a day, week or chain that is over reads 0."""
    today = today or datetime.date.today()
    stats = dict((game_profile or {}).get("play_stats", {}))
    last = _last_play_day(stats)
    days_ago = (today - last).days if last else None
    weeks_ago = (_monday(today) - _monday(last)).days // 7 if last else None
    if days_ago != 0:
        stats["today_count"] = 0
    if days_ago is None or days_ago > 1:
        stats["play_chain"] = 0
    if weeks_ago != 0:
        stats["week_play_count"] = 0
    if weeks_ago is None or weeks_ago > 1:
        stats["week_chain"] = 0
    return {
        k: int(stats.get(k, 0))
        for k in ("play_count", "day_count", "today_count", "play_chain", "max_play_chain",
                  "week_count", "week_play_count", "week_chain", "max_week_chain")
    }


def count_play(game_profile, today=None, now=None):
    """Count one credit. Returns False when it was the same credit sent again."""
    today = today or datetime.date.today()
    now = time.time() if now is None else now
    stored = game_profile.get("play_stats", {})
    if now - stored.get("counted_at", 0) < PLAY_STATS_REPEAT_SECONDS:
        return False

    last = _last_play_day(stored)
    stats = play_stats_now(game_profile, today)
    stats["play_count"] += 1
    stats["today_count"] += 1
    stats["week_play_count"] += 1
    if last != today:  # first credit of the day
        stats["day_count"] += 1
        stats["play_chain"] += 1
    if last is None or _monday(last) != _monday(today):  # first credit of the week
        stats["week_count"] += 1
        stats["week_chain"] += 1
    stats["max_play_chain"] = max(stats["max_play_chain"], stats["play_chain"])
    stats["max_week_chain"] = max(stats["max_week_chain"], stats["week_chain"])
    stats["last_date"] = today.isoformat()
    stats["counted_at"] = now
    game_profile["play_stats"] = stats
    return True


# ARENA BATTLE. Three places carry it (soundvoltex.dll, the same in EXCEED GEAR and NABLA):
#   sv_common  arena{season s32, rule s32 (0..2), rank_match_target s32[] (each 0..2),
#                    time_start time_end shop_start shop_end u64 in ms, is_open is_shop bool,
#                    catalog*{catalog_id catalog_type price item_type item_id param s32}}
#              (parser sub_180125BF0) - without the node the game has no season and ARENA stays shut
#   sv_load    arena{last_play_season rank_point shop_point ultimate_rate ultimate_rank_num
#                    rank_play_cnt ultimate_play_cnt megamix_rate s32}
#   sv_save    arena{season, earned_rank_point earned_shop_point earned_ultimate_rate
#                    earned_megamix_rate s32, rank_play ultimate_play bool}  (writer sub_180324B10),
#              only sent after an arena credit; the game works the point changes out itself
#              (Arena::*FluctuationCalculator), the server adds them up.
# Opponents are found through sv_entry_s like any other match, there is no separate module.
# What the rule numbers mean is not confirmed; rank_match_target lists the rules a rank match may use.
ARENA_SEASON = 1
ARENA_RULE = 0
ARENA_RANK_MATCH_RULES = [0, 1, 2]
ARENA_OPEN = (1640995200, 2145884400)  # 2022-01-01 .. 2037-12-31, seconds (the game keeps 32 bits)
ARENA_SHOP_OPEN = ARENA_OPEN


def arena_season_node():
    return E.arena(
        E.season(ARENA_SEASON, __type="s32"),
        E.rule(ARENA_RULE, __type="s32"),
        E.rank_match_target(ARENA_RANK_MATCH_RULES, __type="s32"),
        E.time_start(ARENA_OPEN[0] * 1000, __type="u64"),
        E.time_end(ARENA_OPEN[1] * 1000, __type="u64"),
        E.shop_start(ARENA_SHOP_OPEN[0] * 1000, __type="u64"),
        E.shop_end(ARENA_SHOP_OPEN[1] * 1000, __type="u64"),
        E.is_open(1, __type="bool"),
        E.is_shop(1, __type="bool"),
    )


def arena_rank_of(sdvx_id, game_version, ultimate_rate):
    """Place among the players who have played ULTIMATE matches this season, 0 without any."""
    if ultimate_rate <= 0:
        return 0
    better = 0
    for card in get_db().table("sdvx_profile").all():
        if card.get("sdvx_id") == sdvx_id:
            continue
        other = card.get("version", {}).get(str(game_version), {}).get("arena", {})
        if other.get("last_play_season") == ARENA_SEASON and other.get("ultimate_rate", 0) > ultimate_rate:
            better += 1
    return better + 1


def arena_profile_node(game_profile, sdvx_id, game_version):
    arena = game_profile.get("arena", {})
    this_season = arena.get("last_play_season") == ARENA_SEASON
    rate = arena.get("ultimate_rate", 0) if this_season else 0
    return E.arena(
        E.last_play_season(arena.get("last_play_season", 0), __type="s32"),
        E.rank_point(arena.get("rank_point", 0) if this_season else 0, __type="s32"),
        E.shop_point(arena.get("shop_point", 0), __type="s32"),
        E.ultimate_rate(rate, __type="s32"),
        E.ultimate_rank_num(arena_rank_of(sdvx_id, game_version, rate), __type="s32"),
        E.rank_play_cnt(arena.get("rank_play_cnt", 0) if this_season else 0, __type="s32"),
        E.ultimate_play_cnt(arena.get("ultimate_play_cnt", 0) if this_season else 0, __type="s32"),
        E.megamix_rate(arena.get("megamix_rate", 0), __type="s32"),
    )


def save_arena(game_profile, node):
    """Add up what the game earned in an arena credit. Rank and rate belong to a season, shop points stay."""
    if node is None:
        return

    def number(name):
        text = node.findtext(name)
        return int(text) if text not in (None, "") else 0

    arena = game_profile.setdefault("arena", {})
    season = number("season") or ARENA_SEASON
    if arena.get("last_play_season") != season:
        for k in ("rank_point", "ultimate_rate", "rank_play_cnt", "ultimate_play_cnt"):
            arena[k] = 0
    arena["last_play_season"] = season
    arena["rank_point"] = max(0, arena.get("rank_point", 0) + number("earned_rank_point"))
    arena["shop_point"] = max(0, arena.get("shop_point", 0) + number("earned_shop_point"))
    arena["ultimate_rate"] = max(0, arena.get("ultimate_rate", 0) + number("earned_ultimate_rate"))
    arena["megamix_rate"] = max(0, arena.get("megamix_rate", 0) + number("earned_megamix_rate"))
    arena["rank_play_cnt"] = arena.get("rank_play_cnt", 0) + (1 if number("rank_play") else 0)
    arena["ultimate_play_cnt"] = arena.get("ultimate_play_cnt", 0) + (1 if number("ultimate_play") else 0)


def get_id_from_profile(cid):
    profile = get_db().table("sdvx_profile").get(where("card") == cid)

    djid = "%08d" % profile["sdvx_id"]
    djid_split = "-".join([djid[:4], djid[4:]])

    return profile["sdvx_id"], djid_split


@router.post("/{gameinfo}/game/sv{ver}_common")
async def game_sv_common(ver: str, request: Request):
    request_info = await core_process_request(request)

    event = [
        "DEMOGAME_PLAY",
        "MATCHING_MODE",
        "MATCHING_MODE_FREE_IP",
        "LEVEL_LIMIT_EASING",
        "ACHIEVEMENT_ENABLE",
        "APICAGACHADRAW\t30",
        "VOLFORCE_ENABLE",
        "AKANAME_ENABLE",
        "PAUSE_ONLINEUPDATE",
        "CONTINUATION",
        "TENKAICHI_MODE",
        "QC_MODE",
        "KAC_MODE",
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
        "VALGENE_ENABLE",
        "ARENA_ENABLE",
        "ARENA_LOCAL_TO_ONLINE_ENABLE",
        "ARENA_ALTER_MODE_WINDOW_ENABLE",
        "ARENA_PASS_MATCH_WINDOW_ENABLE",
        "DEMOLOOP_PASELI_FESTIVAL_2022",
        "DISABLED_MUSIC_IN_ARENA_ONLINE",
        "ARENA_VOTE_MODE_ENABLE",
        "DISP_PASELI_BANNER",
        "S_PUC_EFFECT_ENABLE",
        "SUPER_RANDOM_ACTIVE",
        "PLAYER_RADAR_ENABLE",
        "APRIL_RAINBOW_LINE_ACTIVE",
        "USE_CUDA_VIDEO_PRESENTER",
        "CHARACTER_IGNORE_DISABLE\t122,123,131,139,140,143,149,160,162,163,164,167,170,174",
        "STAMP_IGNORE_DISABLE\t273~312,773~820,993~1032,1245~1284,1469~1508,1585~1632,1633~1672,1737~1776,1777~1816,1897~1936",
        "SUBBG_IGNORE_DISABLE\t166~185,281~346,369~381,419~438,464~482,515~552,595~616,660~673,714~727",
    ]

    unlock = []

    for f in (
        path.join("modules", "sdvx", "music_db.xml"),
        path.join("music_db.xml"),
    ):
        if path.exists(f):
            with open(f, "r", encoding="shift_jisx0213") as fp:

                tree = ET.parse(fp, ET.XMLParser())
                mdb = tree.getroot()

                for entry in mdb:
                    mid = entry.get("id")
                    # print(mid)
                    difficulties = {
                        0: "novice",
                        1: "advanced",
                        2: "exhaust",
                        3: "infinite",
                        4: "maximum",
                        5: "ultimate",
                    }
                    for k in difficulties:
                        d = entry.find("difficulty").find(difficulties[k])
                        if d is not None:
                            limit = int(d.find("limited").text)
                            if limit != 3:
                                # print(mid, difficulties[k], limit)
                                unlock.append([mid, k])
                break

    if unlock == []:
        for i in range(2400):
            for j in range(0, 5):
                unlock.append([i, j])


    response = E.response(
        E.game(
            E.event(
                *[
                    E.info(
                        E.event_id(s, __type="str"),
                    )
                    for s in event
                ],
            ),
            E.music_limited(
                *[
                    E.info(
                        E.music_id(s[0], __type="s32"),
                        E.music_type(s[1], __type="u8"),
                        E.limited(3, __type="u8"),
                    )
                    for s in unlock
                ],
            ),
            arena_season_node(),
        )
    )

    response_body, response_headers = await core_prepare_response(request, response)
    return Response(content=response_body, headers=response_headers)


@router.post("/{gameinfo}/game/sv{ver}_new")
async def game_sv_new(ver: str, request: Request):
    request_info = await core_process_request(request)
    game_version = request_info["game_version"]

    root = request_info["root"][0]

    dataid = player_card(root)
    cardno = root.find("cardno").text
    name = root.find("name").text

    db = get_db().table("sdvx_profile")
    all_profiles_for_card = db.get(Query().card == dataid)

    if all_profiles_for_card is None:
        all_profiles_for_card = {"card": dataid, "version": {}}

    if "sdvx_id" not in all_profiles_for_card:
        sdvx_id = random.randint(10000000, 99999999)
        all_profiles_for_card["sdvx_id"] = sdvx_id

    all_profiles_for_card["version"][str(game_version)] = {
        "game_version": game_version,
        "name": name,
        "appeal_id": 0,
        "skill_level": 0,
        "skill_base_id": 0,
        "skill_name_id": 0,
        "earned_gamecoin_packet": 0,
        "earned_gamecoin_block": 0,
        "earned_blaster_energy": 0,
        "earned_extrack_energy": 0,
        "used_packet_booster": 0,
        "used_block_booster": 0,
        "hispeed": 0,
        "lanespeed": 0,
        "gauge_option": 0,
        "ars_option": 0,
        "notes_option": 0,
        "early_late_disp": 0,
        "draw_adjust": 0,
        "eff_c_left": 0,
        "eff_c_right": 1,
        "music_id": 0,
        "music_type": 0,
        "sort_type": 0,
        "narrow_down": 0,
        "headphone": 1,
        "print_count": 0,
        "start_option": 0,
        "bgm": 0,
        "submonitor": 0,
        "nemsys": 0,
        "stampA": 0,
        "stampB": 0,
        "stampC": 0,
        "stampD": 0,
        "items": [],
        "params": [],
    }

    db.upsert(all_profiles_for_card, where("card") == dataid)

    response = E.response(
        E.game(
            E.result(0, __type="u8"),
        ),
    )

    response_body, response_headers = await core_prepare_response(request, response)
    return Response(content=response_body, headers=response_headers)


@router.post("/{gameinfo}/game/sv{ver}_load")
async def game_sv_load(ver: str, request: Request):
    request_info = await core_process_request(request)
    game_version = request_info["game_version"]

    dataid = player_card(request_info["root"][0])
    profile = get_game_profile(dataid, game_version)

    if profile:
        djid, djid_split = get_id_from_profile(dataid)

        unlock = []
        for i in range(301):
            unlock.append([i, 11, 15])
        for i in range(6001):
            unlock.append([i, 1, 1])
        unlock.append([599, 4, 10])
        for item in profile["items"]:
            unlock.append(item)

        customize = [
            [
                2,
                2,
                [
                    profile["bgm"],
                    profile["submonitor"],
                    profile["nemsys"],
                    profile["stampA"],
                    profile["stampB"],
                    profile["stampC"],
                    profile["stampD"],
                ],
            ]
        ]
        for item in profile["params"]:
            customize.append(item)

        response = E.response(
            E.game(
                E.result(0, __type="u8"),
                E.name(profile["name"], __type="str"),
                E.code(djid_split, __type="str"),
                E.sdvx_id(djid_split, __type="str"),
                E.appeal_id(profile["appeal_id"], __type="u16"),
                E.skill_level(profile["skill_level"], __type="s16"),
                E.skill_base_id(profile["skill_base_id"], __type="s16"),
                E.skill_name_id(profile["skill_name_id"], __type="s16"),
                E.gamecoin_packet(profile["earned_gamecoin_packet"], __type="u32"),
                E.gamecoin_block(profile["earned_gamecoin_block"], __type="u32"),
                E.blaster_energy(profile["earned_blaster_energy"], __type="u32"),
                E.blaster_count(9999, __type="u32"),
                E.extrack_energy(profile["earned_extrack_energy"], __type="u16"),
                *[E(name, value, __type="u32") for name, value in play_stats_now(profile).items()],
                E.creator_id(1, __type="u32"),
                E.eaappli(E.relation(1, __type="s8")),
                E.ea_shop(
                    E.blaster_pass_enable(1, __type="bool"),
                    E.blaster_pass_limit_date(1605871200, __type="u64"),
                ),
                E.kac_id(profile["name"], __type="str"),
                E.block_no(0, __type="s32"),
                E.volte_factory(
                    *[
                        E.info(
                            E.goods_id(s, __type="s32"),
                            E.status(1, __type="s32"),
                        )
                        for s in range(1, 999)
                    ],
                ),
                *[
                    E.campaign(
                        E.campaign_id(s, __type="s32"),
                        E.jackpot_flg(1, __type="bool"),
                    )
                    for s in range(99)
                ],
                E.cloud(E.relation(1, __type="s8")),
                E.something(
                    *[
                        E.info(
                            E.ranking_id(s[0], __type="s32"),
                            E.value(s[1], __type="s64"),
                        )
                        for s in [[1402, 20000]]
                    ],
                ),
                E.festival(
                    E.fes_id(1, __type="s32"),
                    E.live_energy(1000000, __type="s32"),
                    *[
                        E.bonus(
                            E.energy_type(s, __type="s32"),
                            E.live_energy(1000000, __type="s32"),
                        )
                        for s in range(1, 6)
                    ],
                ),
                E.valgene_ticket(
                    E.ticket_num(0, __type="s32"),
                    E.limit_date(1605871200, __type="u64"),
                ),
                arena_profile_node(profile, djid, game_version),
                E.hispeed(profile["hispeed"], __type="s32"),
                E.lanespeed(profile["lanespeed"], __type="u32"),
                E.gauge_option(profile["gauge_option"], __type="u8"),
                E.ars_option(profile["ars_option"], __type="u8"),
                E.notes_option(profile["notes_option"], __type="u8"),
                E.early_late_disp(profile["early_late_disp"], __type="u8"),
                E.draw_adjust(profile["draw_adjust"], __type="s32"),
                E.eff_c_left(profile["eff_c_left"], __type="u8"),
                E.eff_c_right(profile["eff_c_right"], __type="u8"),
                E.last_music_id(profile["music_id"], __type="s32"),
                E.last_music_type(profile["music_type"], __type="u8"),
                E.sort_type(profile["sort_type"], __type="u8"),
                E.narrow_down(profile["narrow_down"], __type="u8"),
                E.headphone(profile["headphone"], __type="u8"),
                E.item(
                    *[
                        E.info(
                            E.id(s[0], __type="u32"),
                            E.type(s[1], __type="u8"),
                            E.param(s[2], __type="u32"),
                        )
                        for s in unlock
                    ],
                ),
                E.param(
                    *[
                        E.info(
                            E.type(s[0], __type="s32"),
                            E.id(s[1], __type="s32"),
                            E.param(s[2], __type="s32", __count=len(s[2])),
                        )
                        for s in customize
                    ],
                ),
            ),
        )

    else:
        response = E.response(
            E.game(
                E.result(1, __type="u8"),
            )
        )

    response_body, response_headers = await core_prepare_response(request, response)
    return Response(content=response_body, headers=response_headers)


@router.post("/{gameinfo}/game/sv{ver}_load_m")
async def game_sv_load_m(ver: str, request: Request):
    request_info = await core_process_request(request)
    game_version = request_info["game_version"]

    dataid = request_info["root"][0].find("refid").text
    profile = get_game_profile(dataid, game_version)
    djid, djid_split = get_id_from_profile(dataid)

    best_scores = []
    db = get_db()
    for record in db.table("sdvx_scores_best").search(
        (where("game_version") == game_version) & (where("sdvx_id") == djid)
    ):
        best_scores.append(
            [
                record["music_id"],
                record["music_type"],
                record["score"],
                record["exscore"],
                record["clear_type"],
                record["score_grade"],
                0,
                0,
                record["btn_rate"],
                record["long_rate"],
                record["vol_rate"],
                0,
                0,
                0,
                0,
                0,
                0,
                0,
                0,
                0,
                0
            ]
        )

        if int(ver) == 7:
            best_scores[-1].extend([0, 0, 0, 0, 0])

    response = E.response(
        E.game(
            E.music(
                *[
                    E.info(
                        E.param(x, __type="u32"),
                    )
                    for x in best_scores
                ],
            ),
        ),
    )

    response_body, response_headers = await core_prepare_response(request, response)
    return Response(content=response_body, headers=response_headers)


@router.post("/{gameinfo}/game/sv{ver}_save")
async def game_sv_save(ver: str, request: Request):
    request_info = await core_process_request(request)
    game_version = request_info["game_version"]

    dataid = request_info["root"][0].find("refid").text

    profile = get_profile(dataid)
    game_profile = profile["version"].get(str(game_version), {})

    root = request_info["root"][0]

    game_profile["appeal_id"] = int(root.find("appeal_id").text)

    nodes = [
        "appeal_id",
        "skill_level",
        "skill_base_id",
        "skill_name_id",
        "skill_type",
        "earned_gamecoin_packet",
        "earned_gamecoin_block",
        "earned_blaster_energy",
        "earned_extrack_energy",  #
        "hispeed",
        "lanespeed",
        "gauge_option",
        "ars_option",
        "notes_option",
        "early_late_disp",
        "draw_adjust",
        "eff_c_left",
        "eff_c_right",
        "music_id",
        "music_type",
        "sort_type",
        "narrow_down",
        "headphone",
        "start_option",
    ]

    for node in nodes:
        n = root.find(node)
        if n is not None:
            if node.startswith("earned_"):
                game_profile[node] += int(n.text)
            else:
                game_profile[node] = int(n.text)

    game_profile["used_packet_booster"] = int(root.find("ea_shop")[0].text)
    game_profile["used_block_booster"] = int(root.find("ea_shop")[1].text)
    game_profile["print_count"] = int(root.find("print")[0].text)

    # item fix (copied from drs, this is regarded)
    old_items = game_profile["items"]
    items = {}

    for old in old_items:
        t = str(old[0])
        i = str(old[1])
        p = str(old[2])
        if t not in items:
            items[t] = {}
            if i not in items[t]:
                items[t][i] = {}
        items[t][i] = p

    for info in root.find("item"):
        t = info.find("id").text
        i = info.find("type").text
        p = info.find("param").text

        if t not in items:
            items[t] = {}
            if i not in items[t]:
                items[t][i] = {}
        items[t][i] = p

    items_list = []

    for t in items:
        for i in items[t]:
            items_list.append([int(t), int(i), int(items[t][i])])

    game_profile["items"] = items_list

    # param fix (copied from drs, this is regarded)
    old_params = game_profile["params"]
    params = {}

    for old in old_params:
        t = str(old[0])
        i = str(old[1])
        p = old[2]
        if t not in params:
            params[t] = {}
            if i not in params[t]:
                params[t][i] = {}
        params[t][i] = p

    for info in root.find("param"):
        t = info.find("type").text
        i = info.find("id").text
        p = info.find("param")

        if t not in params:
            params[t] = {}
            if i not in params[t]:
                params[t][i] = {}
        params[t][i] = [int(x) for x in p.text.split(" ")]

    params_list = []

    for t in params:
        for i in params[t]:
            params_list.append([int(t), int(i), params[t][i]])

    game_profile["params"] = params_list

    if count_play(game_profile):  # not the same credit sent twice
        save_arena(game_profile, root.find("arena"))

    profile["version"][str(game_version)] = game_profile

    get_db().table("sdvx_profile").upsert(profile, where("card") == dataid)

    response = E.response(
        E.game(),
    )

    response_body, response_headers = await core_prepare_response(request, response)
    return Response(content=response_body, headers=response_headers)


@router.post("/{gameinfo}/game/sv{ver}_save_m")
async def game_sv_save_m(ver: str, request: Request):
    request_info = await core_process_request(request)
    game_version = request_info["game_version"]

    timestamp = time.time()

    root = request_info["root"][0]

    dataid = player_card(root)
    profile = get_game_profile(dataid, game_version)
    djid, djid_split = get_id_from_profile(dataid)

    track = root.find("track")
    play_id = int(track.find("play_id").text)
    music_id = int(track.find("music_id").text)
    music_type = int(track.find("music_type").text)
    score = int(track.find("score").text)
    exscore = int(track.find("exscore").text)
    clear_type = int(track.find("clear_type").text)
    score_grade = int(track.find("score_grade").text)
    max_chain = int(track.find("max_chain").text)
    just = int(track.find("just").text)
    critical = int(track.find("critical").text)
    near = int(track.find("near").text)
    error = int(track.find("error").text)
    effective_rate = int(track.find("effective_rate").text)
    btn_rate = int(track.find("btn_rate").text)
    long_rate = int(track.find("long_rate").text)
    vol_rate = int(track.find("vol_rate").text)
    mode = int(track.find("mode").text)
    gauge_type = int(track.find("gauge_type").text)
    notes_option = int(track.find("notes_option").text)
    online_num = int(track.find("online_num").text)
    local_num = int(track.find("local_num").text)
    challenge_type = int(track.find("challenge_type").text)
    retry_cnt = int(track.find("retry_cnt").text)
    judge = [int(x) for x in track.find("judge").text.split(" ")]

    db = get_db()
    db.table("sdvx_scores").insert(
        {
            "timestamp": timestamp,
            "game_version": game_version,
            "sdvx_id": djid,
            "play_id": play_id,
            "music_id": music_id,
            "music_type": music_type,
            "score": score,
            "exscore": exscore,
            "clear_type": clear_type,
            "score_grade": score_grade,
            "max_chain": max_chain,
            "just": just,
            "critical": critical,
            "near": near,
            "error": error,
            "effective_rate": effective_rate,
            "btn_rate": btn_rate,
            "long_rate": long_rate,
            "vol_rate": vol_rate,
            "mode": mode,
            "gauge_type": gauge_type,
            "notes_option": notes_option,
            "online_num": online_num,
            "local_num": local_num,
            "challenge_type": challenge_type,
            "retry_cnt": retry_cnt,
            "judge": judge,
        },
    )

    best = db.table("sdvx_scores_best").get(
        (where("sdvx_id") == djid)
        & (where("game_version") == game_version)
        & (where("music_id") == music_id)
        & (where("music_type") == music_type)
    )
    best = {} if best is None else best

    best_score_data = {
        "game_version": game_version,
        "sdvx_id": djid,
        "name": profile["name"],
        "music_id": music_id,
        "music_type": music_type,
        "score": max(score, best.get("score", score)),
        "exscore": max(exscore, best.get("exscore", exscore)),
        "clear_type": max(clear_type, best.get("clear_type", clear_type)),
        "score_grade": max(score_grade, best.get("score_grade", score_grade)),
        "btn_rate": max(btn_rate, best.get("btn_rate", btn_rate)),
        "long_rate": max(long_rate, best.get("long_rate", long_rate)),
        "vol_rate": max(vol_rate, best.get("vol_rate", vol_rate)),
    }

    db.table("sdvx_scores_best").upsert(
        best_score_data,
        (where("sdvx_id") == djid)
        & (where("game_version") == game_version)
        & (where("music_id") == music_id)
        & (where("music_type") == music_type),
    )

    response = E.response(
        E.game(),
    )

    response_body, response_headers = await core_prepare_response(request, response)
    return Response(content=response_body, headers=response_headers)


@router.post("/{gameinfo}/game/sv{ver}_hiscore")
async def game_sv_hiscore(ver: str, request: Request):
    request_info = await core_process_request(request)
    game_version = request_info["game_version"]

    best_scores = []
    db = get_db()
    for record in db.table("sdvx_scores_best").search(
        (where("game_version") == game_version)
    ):
        best_scores.append(
            [
                record["music_id"],
                record["music_type"],
                record["sdvx_id"],
                record["name"],
                record["score"],
            ]
        )

    response = E.response(
        E.game(
            E.sc(
                *[
                    E.d(
                        E.id(s[0], __type="u32"),
                        E.ty(s[1], __type="u32"),
                        E.a_sq(s[2], __type="str"),
                        E.a_nm(s[3], __type="str"),
                        E.a_sc(s[4], __type="u32"),
                        E.l_sq(s[2], __type="str"),
                        E.l_nm(s[3], __type="str"),
                        E.l_sc(s[4], __type="u32"),
                    )
                    for s in best_scores
                ],
            ),
        ),
    )

    response_body, response_headers = await core_prepare_response(request, response)
    return Response(content=response_body, headers=response_headers)


@router.post("/{gameinfo}/game/sv{ver}_lounge")
async def game_sv_lounge(ver: str, request: Request):
    request_info = await core_process_request(request)

    response = E.response(
        E.game(E.interval(LOBBY_INTERVAL, __type="u32")),
    )

    response_body, response_headers = await core_prepare_response(request, response)
    return Response(content=response_body, headers=response_headers)


@router.post("/{gameinfo}/game/sv{ver}_shop")
async def game_sv_shop(ver: str, request: Request):
    request_info = await core_process_request(request)

    response = E.response(
        E.game(E.nxt_time(1000 * 5 * 60, __type="u32")),
    )

    response_body, response_headers = await core_prepare_response(request, response)
    return Response(content=response_body, headers=response_headers)


@router.post("/{gameinfo}/game/sv{ver}_load_r")
async def game_sv_load_r(ver: str, request: Request):
    request_info = await core_process_request(request)

    response = E.response(
        E.game(),
    )

    response_body, response_headers = await core_prepare_response(request, response)
    return Response(content=response_body, headers=response_headers)


@router.post("/{gameinfo}/game/sv{ver}_frozen")
async def game_sv_frozen(ver: str, request: Request):
    request_info = await core_process_request(request)

    response = E.response(
        E.game(),
    )

    response_body, response_headers = await core_prepare_response(request, response)
    return Response(content=response_body, headers=response_headers)


@router.post("/{gameinfo}/game/sv{ver}_save_e")
async def game_sv_save_e(ver: str, request: Request):
    request_info = await core_process_request(request)

    response = E.response(
        E.game(),
    )

    response_body, response_headers = await core_prepare_response(request, response)
    return Response(content=response_body, headers=response_headers)


@router.post("/{gameinfo}/game/sv{ver}_save_mega")
async def game_sv_save_mega(ver: str, request: Request):
    request_info = await core_process_request(request)

    response = E.response(
        E.game(),
    )

    response_body, response_headers = await core_prepare_response(request, response)
    return Response(content=response_body, headers=response_headers)


@router.post("/{gameinfo}/game/sv{ver}_play_e")
async def game_sv_play_e(ver: str, request: Request):
    request_info = await core_process_request(request)

    response = E.response(
        E.game(),
    )

    response_body, response_headers = await core_prepare_response(request, response)
    return Response(content=response_body, headers=response_headers)


@router.post("/{gameinfo}/game/sv{ver}_play_s")
async def game_sv_play_s(ver: str, request: Request):
    request_info = await core_process_request(request)

    response = E.response(
        E.game(),
    )

    response_body, response_headers = await core_prepare_response(request, response)
    return Response(content=response_body, headers=response_headers)


# ---------------------------------------------------------------- matching lobby
# Local matching (soundvoltex.dll game.sv7_entry_s / entry_e, same shape since sv4).
# entry_s request: c_ver/p_num/p_rest/filter/claim u8, mid/sec/entry_id u32, port u16,
# gip/lip ip4. Response: entry_id u32 + entry*{port u16, gip ip4, lip ip4} for the
# other cabinets to connect to (max 16, the game keeps a 200 byte table). The game
# polls entry_s every <interval> seconds from sv*_lounge and sends entry_e{eid} to leave.
LOBBY_INTERVAL = 10  # seconds between entry_s polls
LOBBY_TIMEOUT = LOBBY_INTERVAL * 3  # drop entries that stopped polling

lobby_entries = {}  # entry_id -> entry dict
_next_entry_id = [1]


def _lobby_int(node, name, default=0):
    found = node.find(name)
    try:
        return int(found.text)
    except (AttributeError, TypeError, ValueError):
        return default


def _lobby_text(node, name, default=""):
    found = node.find(name)
    return found.text if found is not None and found.text else default


def _lobby_prune(now):
    for eid in [k for k, v in lobby_entries.items() if now - v["time"] > LOBBY_TIMEOUT]:
        del lobby_entries[eid]


def _lobby_matches(mine, other):
    # Same game version and matching type only; a different music/segment on the
    # other side is left to the game (it negotiates over UDP once connected).
    return other["c_ver"] == mine["c_ver"] and other["filter"] == mine["filter"]


@router.post("/{gameinfo}/game/sv{ver}_entry_s")
async def game_sv_entry_s(ver: str, request: Request):
    request_info = await core_process_request(request)
    root = request_info["root"][0]
    now = time.time()
    _lobby_prune(now)

    entry = {
        "c_ver": _lobby_int(root, "c_ver"),
        "p_num": _lobby_int(root, "p_num"),
        "p_rest": _lobby_int(root, "p_rest"),
        "filter": _lobby_int(root, "filter"),
        "mid": _lobby_int(root, "mid"),
        "sec": _lobby_int(root, "sec"),
        "port": _lobby_int(root, "port", 5700),
        "gip": _lobby_text(root, "gip", "0.0.0.0"),
        "lip": _lobby_text(root, "lip", "0.0.0.0"),
        "claim": _lobby_int(root, "claim"),
        "time": now,
    }

    entry_id = _lobby_int(root, "entry_id")
    if entry_id not in lobby_entries:
        # A cabinet that re-enters after a timeout, or a fresh one: give it a new id.
        # (Stale ids from the same lip/port are replaced so a reboot does not leave a ghost.)
        for eid in [k for k, v in lobby_entries.items() if v["lip"] == entry["lip"] and v["port"] == entry["port"]]:
            del lobby_entries[eid]
        entry_id = _next_entry_id[0]
        _next_entry_id[0] = _next_entry_id[0] % 0xFFFFFFFF + 1
    lobby_entries[entry_id] = entry

    others = [
        (eid, e)
        for eid, e in sorted(lobby_entries.items(), key=lambda kv: kv[1]["time"])
        if eid != entry_id and _lobby_matches(entry, e)
    ][:16]

    response = E.response(
        E.game(
            E.entry_id(entry_id, __type="u32"),
            *[
                E.entry(
                    E.port(e["port"], __type="u16"),
                    E.gip(e["gip"], __type="ip4"),
                    E.lip(e["lip"], __type="ip4"),
                )
                for eid, e in others
            ],
        ),
    )

    response_body, response_headers = await core_prepare_response(request, response)
    return Response(content=response_body, headers=response_headers)


@router.post("/{gameinfo}/game/sv{ver}_entry_e")
async def game_sv_entry_e(ver: str, request: Request):
    request_info = await core_process_request(request)
    root = request_info["root"][0]

    lobby_entries.pop(_lobby_int(root, "eid"), None)
    _lobby_prune(time.time())

    response = E.response(
        E.game(),
    )

    response_body, response_headers = await core_prepare_response(request, response)
    return Response(content=response_body, headers=response_headers)


@router.post("/{gameinfo}/game/sv{ver}_log")
async def game_sv_log(ver: str, request: Request):
    request_info = await core_process_request(request)

    response = E.response(
        E.game(),
    )

    response_body, response_headers = await core_prepare_response(request, response)
    return Response(content=response_body, headers=response_headers)
