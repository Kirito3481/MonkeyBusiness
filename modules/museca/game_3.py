import random
import re
import time
import xml.etree.ElementTree as ET
from os import path, stat

from tinydb import where

from fastapi import APIRouter, Request, Response

from core_common import core_process_request, core_prepare_response, E
from core_database import get_db

router = APIRouter(prefix="/local", tags=["local"])
router.model_whitelist = ["PIX"]

# MÚSECA 1+1/2 (PIX) game_3 module, ported from bemaniutils museca1plus with
# museca-plus (omnimix) events enabled. Request nodes are children of <game_3>.

MUSIC_DB_PATHS = (
    path.join("modules", "museca", "music_db.xml"),  # copy of data_mods/museca-plus/museca/xml/music-info-b.xml
    "music_db.xml",
)

CHARTS = {"novice": 0, "advanced": 1, "exhaust": 2, "infinite": 3}

LIMITED_LOCKED = 1
LIMITED_UNLOCKABLE = 2
LIMITED_UNLOCKED = 3

CATALOG_TYPE_SONG = 0

EVENT_IDS = [
    143,  # Matching enabled
    1,  # Extended pedal options
    56,  # Generator grafica icon (print 1)
    83,  # Paseli Light Start
    86,  # Generator grafica icon (print 2)
    98,  # Caption 2 notice
    105,  # "Number of Layers" option visible
    130,  # Curator Rank
    141,  # Coconatsu & Mukipara grafica effects
    145, 146, 147, 148, 149,  # MUKIPARA unlocks
    195,  # Fictional Curator (foot pedal options)
    140,  # museca plus: Agetta Moratta (vmlink_phase 3)
    211,  # museca plus: News 1
    212,  # museca plus: News 2
]

_music_cache = {"key": None, "value": {}}


# ---------------------------------------------------------------- music db
def load_music_db():
    # {music_id: {chart: {"difnum": int, "limited": int}}}, cached until the file changes.
    f = next((p for p in MUSIC_DB_PATHS if path.exists(p)), None)
    if f is None:
        return {}

    key = (f, stat(f).st_mtime)
    if _music_cache["key"] == key:
        return _music_cache["value"]

    raw = open(f, "rb").read().decode("shift_jisx0213", errors="replace")
    raw = re.sub(r"^\s*<\?xml[^>]*\?>", "", raw)
    root = ET.fromstring(raw)

    songs = {}
    for music in root.findall("music"):
        mid = int(music.get("id"))
        charts = {}
        diff = music.find("difficulty")
        if diff is None:
            continue
        for name, chart in CHARTS.items():
            node = diff.find(name)
            if node is None:
                continue
            charts[chart] = {
                "difnum": int(node.findtext("difnum") or 0),
                "limited": int(node.findtext("limited") or 0),
            }
        songs[mid] = charts

    _music_cache["key"] = key
    _music_cache["value"] = songs
    return songs


# ---------------------------------------------------------------- helpers
def _text(node, tag, default=None):
    found = node.find(tag) if node is not None else None
    if found is None or found.text is None:
        return default
    return found.text


def _int(node, tag, default=0):
    try:
        return int(_text(node, tag))
    except (TypeError, ValueError):
        return default


def _int_list(node, tag):
    text = _text(node, tag)
    if text is None:
        return None
    return [int(v) for v in text.split() if v.strip()]


def format_code(museca_id):
    s = "%08d" % int(museca_id)
    return f"{s[:4]}-{s[4:]}"


def get_card(refid):
    return get_db().table("museca_profile").get(where("card") == refid)


def get_game_profile(refid, game_version):
    card = get_card(refid)
    if card is None:
        return None, None
    stored = card["version"].get(str(game_version))
    if stored is None:
        return card, None
    return card, merge_defaults(new_game_profile(game_version, stored.get("name", ""), stored.get("loc", "")), stored)


def merge_defaults(defaults, stored):
    merged = dict(defaults)
    for k, v in stored.items():
        if isinstance(v, dict) and isinstance(merged.get(k), dict):
            merged[k] = merge_defaults(merged[k], v)
        else:
            merged[k] = v
    return merged


def save_game_profile(refid, game_version, profile):
    db = get_db().table("museca_profile")
    card = db.get(where("card") == refid)
    if card is None:
        return
    card["version"][str(game_version)] = profile
    db.upsert(card, where("card") == refid)


def new_game_profile(game_version, name, loc):
    return {
        "game_version": game_version,
        "name": name,
        "loc": loc,
        "packet": 0,
        "block": 0,
        "blaster_energy": 0,
        "blaster_count": 0,
        "skill_name_id": -1,
        "hidden_param": [0] * 20,
        "play_count": 0,
        "daily_count": 0,
        "play_chain": 0,
        "last_play_day": "",
        "last": {
            "music_id": -1,
            "music_type": 0,
            "sort_type": 0,
            "narrow_down": 0,
            "headphone": 0,
            "appeal_id": 1001,
            "comment_id": 0,
            "gauge_option": 0,
        },
        "items": {},  # "type:id" -> {"param": int, "diff_param": int|None}
    }


def _today():
    return time.strftime("%Y-%m-%d")


def _yesterday():
    return time.strftime("%Y-%m-%d", time.localtime(time.time() - 86400))


def update_play_statistics(profile):
    today = _today()
    profile["play_count"] += 1
    if profile["last_play_day"] == today:
        profile["daily_count"] += 1
    else:
        profile["daily_count"] = 1
        if profile["last_play_day"] == _yesterday():
            profile["play_chain"] += 1
        else:
            profile["play_chain"] = 1
        profile["last_play_day"] = today


# ---------------------------------------------------------------- profile format
def format_profile(museca_id, profile):
    last = profile["last"]

    items = []
    for key, item in profile["items"].items():
        itype, iid = (int(v) for v in key.split(":"))
        if itype == CATALOG_TYPE_SONG:
            continue  # songs are force-unlocked below
        children = [
            E("type", itype, __type="u8"),
            E("id", iid, __type="u32"),
            E.param(item.get("param", 0), __type="u32"),
        ]
        if item.get("diff_param") is not None:
            children.append(E.diff_param(item["diff_param"], __type="s32"))
        items.append(E.info(*children))

    # Force unlock every song: param = bitmask of charts that exist.
    for mid, charts in sorted(load_music_db().items()):
        mask = 0
        for chart, info in charts.items():
            if info["difnum"] > 0:
                mask |= 1 << chart
        if mask:
            items.append(
                E.info(
                    E("type", CATALOG_TYPE_SONG, __type="u8"),
                    E("id", mid, __type="u32"),
                    E.param(mask, __type="u32"),
                )
            )

    return E.response(
        E.game_3(
            E.name(profile["name"], __type="str"),
            E.code(format_code(museca_id), __type="str"),
            E.gamecoin_packet(profile["packet"], __type="u32"),
            E.gamecoin_block(profile["block"], __type="u32"),
            E.skill_name_id(profile["skill_name_id"], __type="s16"),
            E.hidden_param(profile["hidden_param"], __type="s32"),
            E.blaster_energy(profile["blaster_energy"], __type="u32"),
            E.blaster_count(profile["blaster_count"], __type="u32"),
            E.ryusei_festa(
                E.ryusei_festa_trigger(True, __type="bool"),
            ),
            E.play_count(profile["play_count"], __type="u32"),
            E.daily_count(profile["daily_count"], __type="u32"),
            E.play_chain(profile["play_chain"], __type="u32"),
            E.last(
                E.music_id(last["music_id"], __type="s32"),
                E.music_type(last["music_type"], __type="u8"),
                E.sort_type(last["sort_type"], __type="u8"),
                E.narrow_down(last["narrow_down"], __type="u8"),
                E.headphone(last["headphone"], __type="u8"),
                E.appeal_id(last["appeal_id"], __type="u16"),
                E.comment_id(last["comment_id"], __type="u16"),
                E.gauge_option(last["gauge_option"], __type="u8"),
            ),
            E.item(*items),
        )
    )


def update_profile_from_save(profile, root):
    for req_key, key in (
        ("earned_gamecoin_packet", "packet"),
        ("earned_gamecoin_block", "block"),
        ("earned_blaster_energy", "blaster_energy"),
    ):
        if root.find(req_key) is not None:
            profile[key] += _int(root, req_key)

    if root.find("blaster_count") is not None:
        profile["blaster_count"] = _int(root, "blaster_count")
    if root.find("skill_name_id") is not None:
        profile["skill_name_id"] = _int(root, "skill_name_id", -1)
    hidden = _int_list(root, "hidden_param")
    if hidden is not None and len(hidden) == 20:
        profile["hidden_param"] = hidden

    item = root.find("item")
    if item is not None:
        for info in item.findall("info"):
            itype = _int(info, "type")
            if itype == CATALOG_TYPE_SONG:
                continue  # force-unlocked, never persist
            entry = {"param": _int(info, "param")}
            if info.find("diff_param") is not None:
                entry["diff_param"] = _int(info, "diff_param")
            profile["items"][f"{itype}:{_int(info, 'id')}"] = entry

    last = profile["last"]
    for key in ("headphone", "appeal_id", "comment_id", "music_id", "music_type", "sort_type", "narrow_down", "gauge_option"):
        if root.find(key) is not None:
            last[key] = _int(root, key, last[key])

    update_play_statistics(profile)
    return profile


# ---------------------------------------------------------------- scores
def scores_table():
    return get_db().table("museca_scores_best")


def save_score(museca_id, game_version, root):
    music_id = _int(root, "music_id", -1)
    music_type = _int(root, "music_type", -1)
    if music_id < 0 or music_type < 0:
        return

    cond = (
        (where("museca_id") == int(museca_id))
        & (where("game_version") == game_version)
        & (where("music_id") == music_id)
        & (where("music_type") == music_type)
    )
    old = scores_table().get(cond) or {}

    score = _int(root, "score")
    row = {
        "museca_id": int(museca_id),
        "game_version": game_version,
        "music_id": music_id,
        "music_type": music_type,
        "score": max(score, old.get("score", 0)),
        "cnt": old.get("cnt", 0) + 1,
        "combo": max(_int(root, "max_chain"), old.get("combo", 0)),
        "clear_type": max(_int(root, "clear_type"), old.get("clear_type", 0)),
        "score_grade": max(_int(root, "score_grade"), old.get("score_grade", 0)),
        "timestamp": int(time.time()),
    }
    stats_keys = ("btn_rate", "long_rate", "vol_rate", "critical", "near", "error")
    if score >= old.get("score", 0):
        for k in stats_keys:
            row[k] = _int(root, k)
    else:
        for k in stats_keys:
            row[k] = old.get(k, 0)

    scores_table().upsert(row, cond)


def format_scores(museca_id, game_version):
    rows = scores_table().search(
        (where("museca_id") == int(museca_id)) & (where("game_version") == game_version)
    )
    return E.response(
        E.game_3(
            E.new(
                *[
                    E.music(
                        E.music_id(r["music_id"], __type="u32"),
                        E.music_type(r["music_type"], __type="u32"),
                        E.score(r["score"], __type="u32"),
                        E.cnt(r["cnt"], __type="u32"),
                        E.combo(r["combo"], __type="u32"),
                        E.clear_type(r["clear_type"], __type="u32"),
                        E.score_grade(r["score_grade"], __type="u32"),
                        E.btn_rate(r.get("btn_rate", 0), __type="u32"),
                        E.long_rate(r.get("long_rate", 0), __type="u32"),
                        E.vol_rate(r.get("vol_rate", 0), __type="u32"),
                    )
                    for r in rows
                ]
            ),
        )
    )


def format_hiscore(game_version):
    rows = scores_table().search(where("game_version") == game_version)

    names = {}
    for card in get_db().table("museca_profile").all():
        prof = card.get("version", {}).get(str(game_version))
        if prof is not None and "museca_id" in card:
            names[card["museca_id"]] = prof.get("name", "")

    hits = {}
    best = {}
    for r in rows:
        hits[r["music_id"]] = hits.get(r["music_id"], 0) + r["cnt"]
        key = (r["music_id"], r["music_type"])
        if key not in best or r["score"] > best[key]["score"]:
            best[key] = r

    def record(r):
        return E.info(
            E("id", r["music_id"], __type="u32"),
            E("type", r["music_type"], __type="u32"),
            E.name(names.get(r["museca_id"], ""), __type="str"),
            E.seq(format_code(r["museca_id"]), __type="str"),
            E.score(r["score"], __type="u32"),
        )

    ordered = [best[k] for k in sorted(best)]
    return E.response(
        E.game_3(
            E.hitchart(
                *[
                    E.info(E("id", mid, __type="u32"), E.cnt(cnt, __type="u32"))
                    for mid, cnt in sorted(hits.items(), key=lambda kv: -kv[1])
                ]
            ),
            E.hiscore_allover(*[record(r) for r in ordered]),
            E.hiscore_location(*[record(r) for r in ordered]),
            E.clear_rate(),
        )
    )


# ---------------------------------------------------------------- handlers
async def _respond(request, response):
    response_body, response_headers = await core_prepare_response(request, response)
    return Response(content=response_body, headers=response_headers)


@router.post("/{gameinfo}/game_3/common")
async def museca_game_3_common(request: Request):
    request_info = await core_process_request(request)

    limited = []
    for mid, charts in sorted(load_music_db().items()):
        for chart, info in sorted(charts.items()):
            if info["difnum"] > 0 and info["limited"] != LIMITED_UNLOCKED:
                limited.append(
                    E.info(
                        E.music_id(mid, __type="s32"),
                        E.music_type(chart, __type="u8"),
                        E.limited(LIMITED_UNLOCKED, __type="u8"),
                    )
                )

    def extend(extend_id, nums, strs):
        return E.info(
            E.extend_id(extend_id, __type="u32"),
            E.extend_type(9, __type="u32"),
            *[E(f"param_num_{i}", n, __type="s32") for i, n in enumerate(nums, start=1)],
            *[E(f"param_str_{i}", s, __type="str") for i, s in enumerate(strs, start=1)],
        )

    response = E.response(
        E.game_3(
            E.music_limited(*limited),
            E.event(*[E.info(E.event_id(eid, __type="u32")) for eid in EVENT_IDS]),
            E.extend(
                # Special missions on grafica that have them.
                extend(1, [2, 50, 59, 64, 86], ["available_ex: 1"] * 5),
                # museca plus extension.
                extend(2, [210, 0, 0, 0, 0], [""] * 5),
            ),
        )
    )
    return await _respond(request, response)


@router.post("/{gameinfo}/game_3/shop")
async def museca_game_3_shop(request: Request):
    request_info = await core_process_request(request)
    response = E.response(E.game_3(E.nxt_time(1000 * 5 * 60, __type="u32")))
    return await _respond(request, response)


@router.post("/{gameinfo}/game_3/hiscore")
async def museca_game_3_hiscore(request: Request):
    request_info = await core_process_request(request)
    return await _respond(request, format_hiscore(request_info["game_version"]))


@router.post("/{gameinfo}/game_3/frozen")
async def museca_game_3_frozen(request: Request):
    request_info = await core_process_request(request)
    response = E.response(E.game_3(E.result(0, __type="u8")))
    return await _respond(request, response)


@router.post("/{gameinfo}/game_3/lounge")
async def museca_game_3_lounge(request: Request):
    request_info = await core_process_request(request)
    response = E.response(E.game_3(E.interval(10, __type="u32")))
    return await _respond(request, response)


@router.post("/{gameinfo}/game_3/exception")
async def museca_game_3_exception(request: Request):
    request_info = await core_process_request(request)
    return await _respond(request, E.response(E.game_3()))


@router.post("/{gameinfo}/game_3/play_e")
async def museca_game_3_play_e(request: Request):
    request_info = await core_process_request(request)
    return await _respond(request, E.response(E.game_3()))


@router.post("/{gameinfo}/game_3/new")
async def museca_game_3_new(request: Request):
    request_info = await core_process_request(request)
    game_version = request_info["game_version"]
    root = request_info["root"][0]

    refid = _text(root, "refid")
    name = _text(root, "name") or "NONAME"
    loc = _text(root, "locid") or ""

    db = get_db().table("museca_profile")
    card = db.get(where("card") == refid) or {"card": refid, "version": {}}
    if "museca_id" not in card:
        card["museca_id"] = random.randint(10000000, 99999999)
    card["version"][str(game_version)] = new_game_profile(game_version, name, loc)
    db.upsert(card, where("card") == refid)

    return await _respond(request, E.response(E.game_3()))


@router.post("/{gameinfo}/game_3/load")
async def museca_game_3_load(request: Request):
    request_info = await core_process_request(request)
    game_version = request_info["game_version"]

    refid = _text(request_info["root"][0], "refid")
    card, profile = get_game_profile(refid, game_version) if refid else (None, None)

    if profile is None:
        # result 1 = no profile, game goes to name entry and calls game_3.new
        response = E.response(E.game_3(E.result(1, __type="u8")))
    else:
        response = format_profile(card["museca_id"], profile)
    return await _respond(request, response)


@router.post("/{gameinfo}/game_3/load_m")
async def museca_game_3_load_m(request: Request):
    request_info = await core_process_request(request)
    game_version = request_info["game_version"]

    refid = _text(request_info["root"][0], "dataid")
    card = get_card(refid) if refid else None

    if card is None:
        response = E.response(E.game_3(E.new()))
    else:
        response = format_scores(card["museca_id"], game_version)
    return await _respond(request, response)


@router.post("/{gameinfo}/game_3/save")
async def museca_game_3_save(request: Request):
    request_info = await core_process_request(request)
    game_version = request_info["game_version"]
    root = request_info["root"][0]

    refid = _text(root, "refid")
    card, profile = get_game_profile(refid, game_version) if refid else (None, None)
    if profile is not None:
        update_profile_from_save(profile, root)
        save_game_profile(refid, game_version, profile)

    return await _respond(request, E.response(E.game_3()))


@router.post("/{gameinfo}/game_3/save_m")
async def museca_game_3_save_m(request: Request):
    request_info = await core_process_request(request)
    game_version = request_info["game_version"]
    root = request_info["root"][0]

    refid = _text(root, "refid")
    card = get_card(refid) if refid else None
    if card is not None:
        save_score(card["museca_id"], game_version, root)

    return await _respond(request, E.response(E.game_3()))
