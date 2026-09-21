import xml.etree.ElementTree as ET
from os import path

from tinydb import Query, where

import config
import datetime
import json
import random
import time

from fastapi import Response

from core_common import core_process_request, core_prepare_response, E
from core_database import get_db

# What every SOUND VOLTEX version shares. The versions themselves are exceedgear.py (game.sv6_*) and nabla.py
# (game.sv7_*): each owns its router, its routes and its settings and hands the requests to an SdvxGame.
# This file has no router and no public coroutine on purpose: modules/__init__.py runs every file of this
# folder as a nameless module and would register them a second time.


def get_profile(cid):
    return get_db().table("sdvx_profile").get(where("card") == cid)


def get_game_profile(cid, game_version):
    profile = get_profile(cid)
    if profile is None:
        return None  # a card this game has never seen: the game registers it with sv_new

    return profile["version"].get(str(game_version), None)


# A game version keeps its own profile (sdvx_profile: version/<N>) and its own best scores (game_version N). N is
# core_common's game version, worked out from the build date in the model (6 EXCEED GEAR, 7 NABLA) - not the N of
# the game.svN_* method a request uses. A card that meets a version for the first time brings along what it had in
# the newest earlier one, the way the real service carries data over; from then on the two are separate.
# (Until 2026-09 every build since 2020 counted as 6.)
CARRY_OVER_PROFILES = True


def carry_over_profile(cid, game_version):
    """Give the card a profile for game_version out of its newest earlier one. True when that happened."""
    if not CARRY_OVER_PROFILES:
        return False
    profile = get_profile(cid)
    if profile is None or str(game_version) in profile.get("version", {}):
        return False
    earlier = [int(v) for v in profile.get("version", {}) if v.isdigit() and int(v) < game_version]
    if not earlier:
        return False
    source = max(earlier)
    carried = json.loads(json.dumps(profile["version"][str(source)]))  # a copy of its own, not the same lists
    carried["game_version"] = game_version
    profile["version"][str(game_version)] = carried
    get_db().table("sdvx_profile").upsert(profile, where("card") == cid)

    best = get_db().table("sdvx_scores_best")
    rows = best.search((where("game_version") == source) & (where("sdvx_id") == profile["sdvx_id"]))
    best.insert_multiple([{**row, "game_version": game_version} for row in rows])
    print(f"sdvx: card {cid} carried over from version {source} to {game_version} ({len(rows)} best scores)")
    return True


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


def get_id_from_profile(cid):
    profile = get_db().table("sdvx_profile").get(where("card") == cid)

    djid = "%08d" % profile["sdvx_id"]
    djid_split = "-".join([djid[:4], djid[4:]])

    return profile["sdvx_id"], djid_split


# ---------------------------------------------------------------- every song unlocked
# A chart can be shut in two ways and both are opened here:
#   sv_common music_limited  info{music_id, music_type, limited}: limited 3 = on sale to everybody. music_db.xml has
#                            1 / 2 for charts that wait for an event, a gate or a date.
#   sv_load item type 0      {id = music id, param = bit mask of the charts the player has INPUT (bought with blocks):
#                            1 NOV, 2 ADV, 4 EXH, 8 INF/GRV/HVN/VVD/XCD, 16 MXM, 32 ULT}. The game sends the same
#                            item back in sv_save when a chart is bought (seen: [2146, 0, 3]).
# NABLA's highest music id is 2403 (EXCEED GEAR 2342): the limit leaves some room for updates without making
# sv_common much bigger than the 12000 entries the game is known to take. The sixth chart type (5, ULT) exists for
# one song only, 636 Everlasting Message, so it is listed by hand instead of adding 2500 more entries.
FORCE_UNLOCK_SONGS = True
SONG_ID_LIMIT = 2500
SONG_CHART_TYPES = 5  # music_type 0..4 for every id
SONG_EXTRA_CHARTS = [[636, 5]]
SONG_ALL_CHARTS = 63


# ---------------------------------------------------------------- SKILL ANALYZER
# soundvoltex.dll (EXCEED GEAR), three places:
#   sv_common  skill_course/info: season_id s32 (0..63), season_name str, season_new_flg bool, course_id s16 (>= 1),
#              course_name str, course_type s16 (0 = on its level's page, 3 = the special page), skill_level s16 (1..12,
#              12 = the infinity level), skill_name_id s16 (the title a pass gives), matching_assist bool,
#              clear_rate s32, avg_score u32, skill_type s16 (0 normal, 1 GOD mode: the game keeps two course lists) and
#              exactly three track{track_no s16 (0..2!), music_id s32, music_type s8}. Parser sub_1802BF010: a missing
#              node drops that course and all after it, a track_no outside 0..2 corrupts the game's stack, a course with
#              a chart the player cannot select is dropped. Names may be 63 Shift-JIS bytes at most.
#   sv_load    skill_type s16 next to skill_level / skill_name_id, and skill/course{ssnid crsid st ct gr ar cnt s16,
#              sc ex s32}: the best result per (st, ssnid, crsid), all nine nodes required (sub_1802C2D80).
#              ct 1 failed, 2 passed, 3 all UC, 4 all PUC; gr 1..10; ar 0..20000 (hundredths of a percent).
#   sv_save    course{ssnid crsid st s16, sc ex u32, ct gr s16, jr cr nr er cm u32, ar s16, tr x3} after a course
#              (sub_1802C4F00); a guest sends the same as game.svN_save_c. The answers are not read.
# The courses themselves are data, one file per game version: modules/sdvx/data/6/skill_courses.json is EXCEED GEAR's
# (the official courses; the "titles" in it are there for editing). A version without a file has no SKILL ANALYZER:
# its music ids, removed songs and even the protocol may differ, so nothing is borrowed from another version.
SKILL_COURSE_FILE = path.join("modules", "sdvx", "data", "{ver}", "skill_courses.json")  # {ver}: the game version
SKILL_COURSE_ASCII_NAMES = True  # the English names of skill_courses.json ("ascii"); False = the Japanese ones
# The game decodes the season / course names as UTF-8 although its ea3-config says SHIFT_JIS: in a Shift-JIS
# answer anything that is not ASCII (the infinity sign of "Level.∞") shows up as "?". So sv_common goes out as UTF-8.
# Only versions that have a course list get this; the others keep the usual Shift-JIS answer.
SV_COMMON_ENCODING = "UTF-8"
SKILL_GOD_MODE = True  # also offer every normal course in GOD mode (skill_type 1)
SKILL_MATCHING_ASSIST_MAX_LEVEL = 7


_skill_seasons = {}  # ver ("6", "7", ...) -> seasons, read once


def skill_seasons_of(ver):
    ver = str(ver)
    if ver not in _skill_seasons:
        file = SKILL_COURSE_FILE.format(ver=ver) if ver.isdigit() else None
        try:
            with open(file, "r", encoding="utf-8") as fp:
                _skill_seasons[ver] = json.load(fp)
        except (OSError, TypeError, ValueError) as e:
            if file and path.exists(file):
                print(f"sdvx: {file} is not usable ({e})")
            _skill_seasons[ver] = []
    return _skill_seasons[ver]


def _skill_name(entry):
    name = entry["ascii"] if SKILL_COURSE_ASCII_NAMES else entry["name"]
    return name.encode("cp932", "replace")[:63].decode("cp932", "ignore")


def skill_course_nodes(ver):
    skill_seasons = skill_seasons_of(ver)
    numbered = [s["id"] for s in skill_seasons if not s.get("special")]
    newest = max(numbered) if numbered else None
    nodes = []
    for season in skill_seasons:
        if not 0 <= season["id"] <= 63:
            continue
        for course in season["courses"]:
            if len(course["tracks"]) != 3 or course["id"] < 1:
                continue
            normal = course["type"] != 3
            for skill_type in (0, 1) if SKILL_GOD_MODE and normal else (0,):
                nodes.append(
                    E.info(
                        E.season_id(season["id"], __type="s32"),
                        E.season_name(_skill_name(season), __type="str"),
                        E.season_new_flg(1 if season["id"] == newest else 0, __type="bool"),
                        E.course_id(course["id"], __type="s16"),
                        E.course_name(_skill_name(course), __type="str"),
                        E.course_type(course["type"], __type="s16"),
                        E.skill_level(course["level"], __type="s16"),
                        E.skill_name_id(course["name_id"], __type="s16"),
                        E.matching_assist(1 if normal and course["level"] <= SKILL_MATCHING_ASSIST_MAX_LEVEL else 0, __type="bool"),
                        E.clear_rate(0, __type="s32"),
                        E.avg_score(0, __type="u32"),
                        E.skill_type(skill_type, __type="s16"),
                        *[
                            E.track(
                                E.track_no(no, __type="s16"),
                                E.music_id(music_id, __type="s32"),
                                E.music_type(music_type, __type="s8"),
                            )
                            for no, (music_id, music_type) in enumerate(course["tracks"])
                        ],
                    )
                )
    return nodes


def skill_record_nodes(game_profile):
    return [
        E.course(
            E.ssnid(r["ssnid"], __type="s16"),
            E.crsid(r["crsid"], __type="s16"),
            E.st(r["st"], __type="s16"),
            E.sc(r["sc"], __type="s32"),
            E.ex(r["ex"], __type="s32"),
            E.ct(r["ct"], __type="s16"),
            E.gr(r["gr"], __type="s16"),
            E.ar(r["ar"], __type="s16"),
            E.cnt(r["cnt"], __type="s16"),
        )
        for r in game_profile.get("skill_courses", [])
        if r["st"] in (0, 1)
    ]


def save_skill_course(game_profile, node):
    """Keep the best of every value per (skill type, season, course) and count the attempt."""
    if node is None or node.find("ssnid") is None or node.find("crsid") is None:
        return

    def number(name):
        found = node.find(name)
        try:
            return int(found.text)
        except (AttributeError, TypeError, ValueError):
            return 0

    key = {"st": number("st"), "ssnid": number("ssnid"), "crsid": number("crsid")}
    records = game_profile.setdefault("skill_courses", [])
    record = next((r for r in records if all(r[k] == v for k, v in key.items())), None)
    if record is None:
        record = {**key, "sc": 0, "ex": 0, "ct": 0, "gr": 0, "ar": 0, "cnt": 0}
        records.append(record)
    for name, limit in (("sc", 0x7FFFFFFF), ("ex", 0x7FFFFFFF), ("ct", 4), ("gr", 10), ("ar", 20000)):
        value = number(name)
        if 0 <= value <= limit:
            record[name] = max(record[name], value)
    record["cnt"] = min(record["cnt"] + 1, 32767)


# ---------------------------------------------------------------- matching lobby
# Local matching (soundvoltex.dll game.sv7_entry_s / entry_e, same shape since sv4).
# entry_s request: c_ver/p_num/p_rest/filter/claim u8, mid/sec/entry_id u32, port u16,
# gip/lip 4u8. Response: entry_id u32 + entry*{port u16, gip 4u8, lip 4u8} for the
# other cabinets to connect to (max 16, the game keeps a 200 byte table). The addresses
# are 4u8 and not ip4 (request builder sub_18060F950, response reader sub_18060FB70 in
# NABLA's soundvoltex.dll both use property type 37): an ip4 answer is not read at all. The game
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


def _lobby_address(node, name):
    # "192 168 0 5" (4u8, what the game sends) or "192.168.0.5" -> "192 168 0 5"
    parts = _lobby_text(node, name).replace(".", " ").split()
    if len(parts) == 4 and all(p.isdigit() and int(p) < 256 for p in parts):
        return " ".join(str(int(p)) for p in parts)
    return "0 0 0 0"


def _lobby_prune(now):
    for eid in [k for k, v in lobby_entries.items() if now - v["time"] > LOBBY_TIMEOUT]:
        del lobby_entries[eid]


def _lobby_matches(mine, other):
    # Same game version and matching type only; a different music/segment on the
    # other side is left to the game (it negotiates over UDP once connected).
    return other["c_ver"] == mine["c_ver"] and other["filter"] == mine["filter"]

class SdvxGame:
    """The handlers of one game version. version: the N of game.svN_*; events: the event flags of sv_common."""

    def __init__(self, version, events):
        self.version = str(version)
        self.events = list(events)

    async def common(self, request):
        ver = self.version  # noqa: F841 (not every handler looks at it)
        request_info = await core_process_request(request)

        event = self.events

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
            for i in range(SONG_ID_LIMIT):
                for j in range(SONG_CHART_TYPES):
                    unlock.append([i, j])
            unlock.extend(SONG_EXTRA_CHARTS)


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
                E.skill_course(*skill_course_nodes(request_info["game_version"])),
            )
        )

        encoding = SV_COMMON_ENCODING if skill_seasons_of(request_info["game_version"]) else None
        response_body, response_headers = await core_prepare_response(request, response, encoding=encoding)
        return Response(content=response_body, headers=response_headers)

    async def new(self, request):
        ver = self.version  # noqa: F841 (not every handler looks at it)
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

    async def load(self, request):
        ver = self.version  # noqa: F841 (not every handler looks at it)
        request_info = await core_process_request(request)
        game_version = request_info["game_version"]

        dataid = player_card(request_info["root"][0])
        carry_over_profile(dataid, game_version)
        profile = get_game_profile(dataid, game_version)

        if profile:
            djid, djid_split = get_id_from_profile(dataid)

            unlock = []
            for i in range(301):
                unlock.append([i, 11, 15])
            for i in range(6001):
                unlock.append([i, 1, 1])
            unlock.append([599, 4, 10])
            if FORCE_UNLOCK_SONGS:
                for i in range(SONG_ID_LIMIT):
                    unlock.append([i, 0, SONG_ALL_CHARTS])
            for item in profile["items"]:
                if FORCE_UNLOCK_SONGS and item[1] == 0:
                    continue  # what the player bought is part of "everything"; a second, smaller mask must not follow
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
                    E.skill_type(profile.get("skill_type", 0), __type="s16"),
                    E.skill(*skill_record_nodes(profile)),
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
                    E.arena(
                        E.last_play_season(0, __type="s32"),
                        E.rank_point(0, __type="s32"),
                        E.shop_point(0, __type="s32"),
                        E.ultimate_rate(0, __type="s32"),
                        E.ultimate_rank_num(0, __type="s32"),
                        E.rank_play_cnt(0, __type="s32"),
                        E.ultimate_play_cnt(0, __type="s32"),
                    ),
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

    async def load_m(self, request):
        ver = self.version  # noqa: F841 (not every handler looks at it)
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

    async def save(self, request):
        ver = self.version  # noqa: F841 (not every handler looks at it)
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

        count_play(game_profile)
        save_skill_course(game_profile, root.find("course"))

        profile["version"][str(game_version)] = game_profile

        get_db().table("sdvx_profile").upsert(profile, where("card") == dataid)

        response = E.response(
            E.game(),
        )

        response_body, response_headers = await core_prepare_response(request, response)
        return Response(content=response_body, headers=response_headers)

    async def save_m(self, request):
        ver = self.version  # noqa: F841 (not every handler looks at it)
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

    async def hiscore(self, request):
        ver = self.version  # noqa: F841 (not every handler looks at it)
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

    async def lounge(self, request):
        ver = self.version  # noqa: F841 (not every handler looks at it)
        request_info = await core_process_request(request)

        response = E.response(
            E.game(E.interval(LOBBY_INTERVAL, __type="u32")),
        )

        response_body, response_headers = await core_prepare_response(request, response)
        return Response(content=response_body, headers=response_headers)

    async def shop(self, request):
        ver = self.version  # noqa: F841 (not every handler looks at it)
        request_info = await core_process_request(request)

        response = E.response(
            E.game(E.nxt_time(1000 * 5 * 60, __type="u32")),
        )

        response_body, response_headers = await core_prepare_response(request, response)
        return Response(content=response_body, headers=response_headers)

    async def load_r(self, request):
        ver = self.version  # noqa: F841 (not every handler looks at it)
        request_info = await core_process_request(request)

        response = E.response(
            E.game(),
        )

        response_body, response_headers = await core_prepare_response(request, response)
        return Response(content=response_body, headers=response_headers)

    async def frozen(self, request):
        ver = self.version  # noqa: F841 (not every handler looks at it)
        request_info = await core_process_request(request)

        response = E.response(
            E.game(),
        )

        response_body, response_headers = await core_prepare_response(request, response)
        return Response(content=response_body, headers=response_headers)

    async def save_c(self, request):
        ver = self.version  # noqa: F841 (not every handler looks at it)
        # A skill course result outside sv_save: guests send it (nothing to keep), a card player's goes with sv_save.
        request_info = await core_process_request(request)
        game_version = request_info["game_version"]
        root = request_info["root"][0]

        dataid = root.findtext("refid")
        profile = get_profile(dataid) if dataid else None
        if profile and str(game_version) in profile.get("version", {}):
            save_skill_course(profile["version"][str(game_version)], root)
            get_db().table("sdvx_profile").upsert(profile, where("card") == dataid)

        response = E.response(
            E.game(),
        )

        response_body, response_headers = await core_prepare_response(request, response)
        return Response(content=response_body, headers=response_headers)

    async def save_e(self, request):
        ver = self.version  # noqa: F841 (not every handler looks at it)
        request_info = await core_process_request(request)

        response = E.response(
            E.game(),
        )

        response_body, response_headers = await core_prepare_response(request, response)
        return Response(content=response_body, headers=response_headers)

    async def save_mega(self, request):
        ver = self.version  # noqa: F841 (not every handler looks at it)
        request_info = await core_process_request(request)

        response = E.response(
            E.game(),
        )

        response_body, response_headers = await core_prepare_response(request, response)
        return Response(content=response_body, headers=response_headers)

    async def play_e(self, request):
        ver = self.version  # noqa: F841 (not every handler looks at it)
        request_info = await core_process_request(request)

        response = E.response(
            E.game(),
        )

        response_body, response_headers = await core_prepare_response(request, response)
        return Response(content=response_body, headers=response_headers)

    async def play_s(self, request):
        ver = self.version  # noqa: F841 (not every handler looks at it)
        request_info = await core_process_request(request)

        response = E.response(
            E.game(),
        )

        response_body, response_headers = await core_prepare_response(request, response)
        return Response(content=response_body, headers=response_headers)

    async def entry_s(self, request):
        ver = self.version  # noqa: F841 (not every handler looks at it)
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
            "gip": _lobby_address(root, "gip"),
            "lip": _lobby_address(root, "lip"),
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
                        E.gip(e["gip"], __type="4u8"),
                        E.lip(e["lip"], __type="4u8"),
                    )
                    for eid, e in others
                ],
            ),
        )

        response_body, response_headers = await core_prepare_response(request, response)
        return Response(content=response_body, headers=response_headers)

    async def entry_e(self, request):
        ver = self.version  # noqa: F841 (not every handler looks at it)
        request_info = await core_process_request(request)
        root = request_info["root"][0]

        lobby_entries.pop(_lobby_int(root, "eid"), None)
        _lobby_prune(time.time())

        response = E.response(
            E.game(),
        )

        response_body, response_headers = await core_prepare_response(request, response)
        return Response(content=response_body, headers=response_headers)

    async def log(self, request):
        ver = self.version  # noqa: F841 (not every handler looks at it)
        request_info = await core_process_request(request)

        response = E.response(
            E.game(),
        )

        response_body, response_headers = await core_prepare_response(request, response)
        return Response(content=response_body, headers=response_headers)
