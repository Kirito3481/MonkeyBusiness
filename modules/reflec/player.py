import random
import time

from tinydb import where

from fastapi import APIRouter, Request, Response

from core_common import core_process_request, core_prepare_response, E
from core_database import get_db

router = APIRouter(prefix="/local", tags=["local"])
router.model_whitelist = ["MBR"]

# REFLEC BEAT VOLZZA 2 "player" module, ported from bemaniutils volzzabase.py /
# volzza2.py. Handler names must stay player_rb5_<method> for the url_slash 0
# forwarder. Score and clear/combo values are stored as the game sends them.

CHART_BASIC, CHART_MEDIUM, CHART_HARD, CHART_SPECIAL = 0, 1, 2, 3

GAME_CLEAR_NO_PLAY = 0
GAME_CLEAR_EARLY_FAILED = 1
GAME_CLEAR_FAILED = 2
GAME_CLEAR_CLEARED = 9
GAME_CLEAR_HARD = 10
GAME_CLEAR_S_HARD = 11

GAME_COMBO_NONE = 0
GAME_COMBO_FULL_COMBO = 1
GAME_COMBO_ALL_JUST = 2
GAME_COMBO_FULL_COMBO_ALL_JUST = 3

CONFIG_INTS = (
    "msel_bgm", "narrowdown_type", "icon_id", "byword_0", "byword_1", "mrec_type", "tab_sel",
    "card_disp", "score_tab_disp", "last_music_id", "last_note_grade", "sort_type",
    "rival_panel_type", "random_entry_work", "custom_folder_work", "folder_type", "folder_lamp_type",
)
CONFIG_BOOLS = ("is_auto_byword_0", "is_auto_byword_1", "is_tweet", "is_link_twitter")
CUSTOM_INTS = (
    "st_shot", "st_frame", "st_expl", "st_bg", "st_shot_vol", "st_bg_bri", "st_obj_size", "st_jr_gauge",
    "st_clr_gauge", "st_rnd", "st_gr_gauge_type", "voice_message_set", "same_time_note_disp",
    "st_score_disp_type", "st_bonus_type", "st_rivalnote_type", "st_topassist_type", "high_speed",
    "st_hazard", "st_clr_cond", "voice_message_volume",
)
BASE_INTS = ("mg", "ap", "uattr", "money", "class", "class_ar", "skill_point")

# refid -> {"plyid", "ga", "gp", "la", "pnid", "time"}; cleared on player_end / server restart
play_sessions = {}


# ---------------------------------------------------------------- helpers
def _text(node, path, default=None):
    found = node.find(path) if node is not None else None
    if found is None or found.text is None:
        return default
    return found.text


def _int(node, path, default=0):
    try:
        return int(_text(node, path))
    except (TypeError, ValueError):
        return default


def _int_list(node, path, default=None):
    text = _text(node, path)
    if text is None:
        return default
    return [int(v) for v in text.split() if v.strip()]


def _pad(values, size, fill=0):
    values = list(values or [])[:size]
    return values + [fill] * (size - len(values))


async def _respond(request, response):
    response_body, response_headers = await core_prepare_response(request, response)
    return Response(content=response_body, headers=response_headers)


# ---------------------------------------------------------------- storage
def profiles_table():
    return get_db().table("reflec_profile")


def scores_table():
    return get_db().table("reflec_scores_best")


def get_card(refid):
    if refid is None:
        return None
    return profiles_table().get(where("card") == refid)


def get_card_by_extid(extid):
    try:
        return profiles_table().get(where("reflec_id") == int(extid))
    except (TypeError, ValueError):
        return None


def ensure_card(refid):
    db = profiles_table()
    card = db.get(where("card") == refid) or {"card": refid, "version": {}}
    if "reflec_id" not in card:
        card["reflec_id"] = random.randint(10000000, 99999999)
    db.upsert(card, where("card") == refid)
    return card


def merge_defaults(defaults, stored):
    merged = dict(defaults)
    for k, v in stored.items():
        if isinstance(v, dict) and isinstance(merged.get(k), dict):
            merged[k] = merge_defaults(merged[k], v)
        else:
            merged[k] = v
    return merged


def get_game_profile(refid, game_version):
    card = get_card(refid)
    if card is None:
        return None, None
    stored = card["version"].get(str(game_version))
    if stored is None:
        return card, None
    return card, merge_defaults(new_game_profile(game_version, stored.get("name", "")), stored)


def save_game_profile(refid, game_version, profile):
    card = ensure_card(refid)
    card["version"][str(game_version)] = profile
    profiles_table().upsert(card, where("card") == refid)


def new_game_profile(game_version, name):
    return {
        "game_version": game_version,
        "name": name,
        "lid": "",
        "mg": 0,
        "ap": 0,
        "uattr": 0,
        "money": 0,
        "class": 0,
        "class_ar": 0,
        "skill_point": 0,
        "mgid": 0,
        "mgsc": 0,
        "favorites": [-1] * 30,
        "config": {k: 0 for k in CONFIG_INTS} | {"last_music_id": -1} | {k: 0 for k in CONFIG_BOOLS},
        "custom": {k: 0 for k in CUSTOM_INTS} | {"voice_message_set": -1},
        "released": {},  # "type:id" -> {"param", "time"}
        "announce": {},  # "type:id" -> {"param", "need"}
        "dojo": {},  # class -> {clear_type, ar, score, plays, play_timestamp, record_timestamp}
        "player_param": {},  # "type:bank" -> [256 ints]
        "yurukome": [],
        "mycourse": {},
        "rivals": [],  # extids
        "play_count": 0,
        "daily_count": 0,
        "total_days": 0,
        "last_play_day": "",
    }


def update_play_statistics(profile):
    today = time.strftime("%Y-%m-%d")
    profile["play_count"] += 1
    if profile["last_play_day"] == today:
        profile["daily_count"] += 1
    else:
        profile["daily_count"] = 1
        profile["total_days"] += 1
        profile["last_play_day"] = today


# ---------------------------------------------------------------- event info (shared with info module)
# event_ctrl/data{type index value value2 start_time end_time} is applied by
# reflecbeat.dll sub_1008A730. Type 1 = CEventPhaseGameData phase slots
# (sub_10090890, index 0..11, value clamped by a per-index max). The class
# select scene (sub_100B8960 -> sub_10090F40) needs phase index 4:
#   class 10 (零) visible when phase >= 1
#   class 11 (極) visible when phase >= 2 and the class 10 dojo record has clear_type 2
# Index 4 max is 3, so value 2 is the highest meaningful phase.
EVENT_CTRL = [
    {"type": 1, "index": 4, "value": 2, "value2": 2},
]


def event_info_nodes():
    return [
        E.event_ctrl(
            *[
                E.data(
                    E("type", ev["type"], __type="s32"),
                    E.index(ev["index"], __type="s32"),
                    E.value(ev["value"], __type="s32"),
                    E.value2(ev["value2"], __type="s32"),
                    E.start_time(0, __type="s32"),
                    E.end_time(0x7FFFFFFF, __type="s32"),
                )
                for ev in EVENT_CTRL
            ]
        ),
        E.item_lock_ctrl(),
        E.mycourse_ctrl(),
    ]


# ---------------------------------------------------------------- profile format
def _mycourse_fields(mc):
    nodes = []
    for i in (1, 2, 3, 4):
        nodes += [
            E(f"music_id_{i}", mc.get(f"music_id_{i}", -1), __type="s32"),
            E(f"note_grade_{i}", mc.get(f"note_grade_{i}", -1), __type="s16"),
            E(f"score_{i}", mc.get(f"score_{i}", -1), __type="s32"),
        ]
    nodes.append(E.insert_time(mc.get("insert_time", -1), __type="s32"))
    return nodes


def _rival_cards(profile):
    cards = []
    for extid in profile.get("rivals", []):
        card = get_card_by_extid(extid)
        if card is None:
            continue
        rprof = next(iter(card.get("version", {}).values()), None)
        if rprof is not None:
            cards.append((card, merge_defaults(new_game_profile(profile["game_version"], ""), rprof)))
    return cards


def format_profile(extid, profile):
    now = int(time.time())
    cfg = profile["config"]
    cus = profile["custom"]
    rivals = _rival_cards(profile)

    released = [
        E.info(
            E("type", int(k.split(":")[0]), __type="u8"),
            E("id", int(k.split(":")[1]), __type="u16"),
            E.param(v.get("param", 0), __type="u16"),
            E.insert_time(v.get("time", now), __type="s32"),
        )
        for k, v in profile["released"].items()
    ]
    announce = [
        E.info(
            E("type", int(k.split(":")[0]), __type="u8"),
            E("id", int(k.split(":")[1]), __type="u16"),
            E.param(v.get("param", 0), __type="u16"),
            E.bneedannounce(bool(v.get("need", 0)), __type="bool"),
        )
        for k, v in profile["announce"].items()
    ]
    dojo = [
        E.rec(
            E("class", int(cls), __type="s32"),
            E.clear_type(d.get("clear_type", 0), __type="s32"),
            E.total_ar(d.get("ar", 0), __type="s32"),
            E.total_score(d.get("score", 0), __type="s32"),
            E.play_count(d.get("plays", 0), __type="s32"),
            E.last_play_time(d.get("play_timestamp", 0), __type="s32"),
            E.record_update_time(d.get("record_timestamp", 0), __type="s32"),
            E.rank(0, __type="s32"),
        )
        for cls, d in profile["dojo"].items()
    ]
    player_param = [
        E.item(
            E("type", int(k.split(":")[0]), __type="s32"),
            E.bank(int(k.split(":")[1]), __type="s32"),
            E.data(_pad(v, 256), __type="s32"),
        )
        for k, v in profile["player_param"].items()
    ]
    rival_nodes = []
    for slot, (card, rprof) in enumerate(rivals):
        session = next((s for r, s in play_sessions.items() if s.get("extid") == card["reflec_id"]), {})
        rival_nodes.append(
            E.r(
                E.slot_id(slot, __type="s32"),
                E("id", card["reflec_id"], __type="s32"),
                E.name(rprof["name"], __type="str"),
                E.icon(rprof["config"]["icon_id"], __type="s32"),
                E("class", rprof["class"], __type="s32"),
                E.class_ar(rprof["class_ar"], __type="s32"),
                E.friend(True, __type="bool"),
                E.target(False, __type="bool"),
                E.time(session.get("time", 0), __type="u32"),
                E.ga(_pad(session.get("ga"), 4), __type="u8"),
                E.gp(session.get("gp", 0), __type="u16"),
                E.ipn(_pad(session.get("la"), 4), __type="u8"),
                E.pnid(_pad(session.get("pnid"), 16), __type="u8"),
            )
        )
    mycourse_f = [
        E.rec(
            E.rival_id(card["reflec_id"], __type="s32"),
            E.mycourse_id(1, __type="s16"),
            *_mycourse_fields(rprof.get("mycourse", {})),
        )
        for card, rprof in rivals
    ]

    return E.response(
        E.player(
            E.pdata(
                E.account(
                    E.usrid(extid, __type="s32"),
                    E.tpc(profile["play_count"], __type="s32"),
                    E.dpc(profile["daily_count"], __type="s32"),
                    E.crd(1, __type="s32"),
                    E.brd(1, __type="s32"),
                    E.tdc(profile["total_days"], __type="s32"),
                    E.intrvld(0, __type="s32"),
                    E.ver(0, __type="s16"),
                    E.succeed(False, __type="bool"),
                    E.pst(0, __type="u64"),
                    E.st(now * 1000, __type="u64"),
                    E.opc(0, __type="s32"),
                    E.lpc(0, __type="s32"),
                    E.cpc(0, __type="s32"),
                    E.mpc(0, __type="s32"),
                ),
                E.base(
                    E.name(profile["name"], __type="str"),
                    E.mg(profile["mg"], __type="s32"),
                    E.ap(profile["ap"], __type="s32"),
                    E.cmnt("", __type="str"),
                    E.uattr(profile["uattr"], __type="s32"),
                    E.money(profile["money"], __type="s32"),
                    E.tbs_5(-1, __type="s32"),
                    E.tbgs_5([-1] * 4, __type="s32"),
                    E.mlog([-1] * 20, __type="s16"),
                    E("class", profile["class"], __type="s32"),
                    E.class_ar(profile["class_ar"], __type="s32"),
                    E.skill_point(profile["skill_point"], __type="s32"),
                    E.meteor_flg(False, __type="bool"),
                ),
                E.rival(*rival_nodes),
                E.config(
                    E.msel_bgm(cfg["msel_bgm"], __type="u8"),
                    E.narrowdown_type(cfg["narrowdown_type"], __type="u8"),
                    E.icon_id(cfg["icon_id"], __type="s16"),
                    E.byword_0(cfg["byword_0"], __type="s16"),
                    E.byword_1(cfg["byword_1"], __type="s16"),
                    E.is_auto_byword_0(bool(cfg["is_auto_byword_0"]), __type="bool"),
                    E.is_auto_byword_1(bool(cfg["is_auto_byword_1"]), __type="bool"),
                    E.mrec_type(cfg["mrec_type"], __type="u8"),
                    E.tab_sel(cfg["tab_sel"], __type="u8"),
                    E.card_disp(cfg["card_disp"], __type="u8"),
                    E.score_tab_disp(cfg["score_tab_disp"], __type="u8"),
                    E.last_music_id(cfg["last_music_id"], __type="s16"),
                    E.last_note_grade(cfg["last_note_grade"], __type="u8"),
                    E.sort_type(cfg["sort_type"], __type="u8"),
                    E.rival_panel_type(cfg["rival_panel_type"], __type="u8"),
                    E.random_entry_work(cfg["random_entry_work"], __type="u64"),
                    E.custom_folder_work(cfg["custom_folder_work"], __type="u64"),
                    E.folder_type(cfg["folder_type"], __type="u8"),
                    E.folder_lamp_type(cfg["folder_lamp_type"], __type="u8"),
                    E.is_tweet(bool(cfg["is_tweet"]), __type="bool"),
                    E.is_link_twitter(bool(cfg["is_link_twitter"]), __type="bool"),
                ),
                E.custom(
                    *[
                        E(k, cus[k], __type="s16" if k == "voice_message_set" else "u8")
                        for k in CUSTOM_INTS
                    ]
                ),
                E.released(*released),
                E.announce(*announce),
                E.dojo(*dojo),
                E.player_param(*player_param),
                E.shop_score(
                    E.today(E.time(now - now % 86400, __type="s32")),
                    E.yesterday(E.time(now - now % 86400 - 86400, __type="s32")),
                ),
                E.mylist(
                    E.list(
                        E.idx(0, __type="s16"),
                        E.mlst(_pad(profile["favorites"], 30, -1), __type="s16"),
                    ),
                ),
                E.minigame(
                    E.mgid(profile["mgid"], __type="s8"),
                    E.sc(profile["mgsc"], __type="s32"),
                ),
                E.derby(E.is_open(False, __type="bool")),
                E.music_rank_point(),
                E.yurukome_list(
                    *[E.yurukome(E.yurukome_id(y, __type="s32")) for y in profile["yurukome"]]
                ),
                E.mycourse(
                    E.mycourse_id(1, __type="s16"),
                    *_mycourse_fields(profile["mycourse"]),
                    *[
                        n
                        for i in (1, 2, 3, 4)
                        for n in (
                            E(f"def_music_id_{i}", -1, __type="s32"),
                            E(f"def_note_grade_{i}", -1, __type="s16"),
                        )
                    ],
                ),
                E.mycourse_f(*mycourse_f),
            ),
        )
    )


# ---------------------------------------------------------------- profile save
def update_profile_from_write(profile, root):
    pdata = root.find("pdata")
    if pdata is None:
        return profile

    profile["lid"] = _text(pdata, "account/lid", profile["lid"]) or profile["lid"]
    base = pdata.find("base")
    if base is not None:
        profile["name"] = _text(base, "name", profile["name"]) or profile["name"]
        for k in BASE_INTS:
            if base.find(k) is not None:
                profile[k] = _int(base, k, profile[k])

    if pdata.find("minigame/mgid") is not None:
        profile["mgid"] = _int(pdata, "minigame/mgid", profile["mgid"])
    if pdata.find("minigame/sc") is not None:
        profile["mgsc"] = _int(pdata, "minigame/sc", profile["mgsc"])
    favorites = _int_list(pdata, "mylist/list/mlst")
    if favorites is not None:
        profile["favorites"] = _pad(favorites, 30, -1)

    config = pdata.find("config")
    if config is not None:
        for k in CONFIG_INTS + CONFIG_BOOLS:
            if config.find(k) is not None:
                profile["config"][k] = _int(config, k, profile["config"][k])

    custom = pdata.find("custom")
    if custom is not None:
        for k in CUSTOM_INTS:
            if custom.find(k) is not None:
                profile["custom"][k] = _int(custom, k, profile["custom"][k])

    params = pdata.find("player_param")
    if params is not None:
        for item in params.findall("item"):
            profile["player_param"][f"{_int(item, 'type')}:{_int(item, 'bank')}"] = _pad(_int_list(item, "data"), 256)

    released = pdata.find("released")
    if released is not None:
        for info in released.findall("info"):
            profile["released"][f"{_int(info, 'type')}:{_int(info, 'id')}"] = {
                "param": _int(info, "param"),
                "time": _int(info, "insert_time") or int(time.time()),
            }

    announce = pdata.find("announce")
    if announce is not None:
        for info in announce.findall("info"):
            profile["announce"][f"{_int(info, 'type')}:{_int(info, 'id')}"] = {
                "param": _int(info, "param"),
                "need": _int(info, "bneedannounce"),
            }

    rival = pdata.find("rival")
    if rival is not None:
        for r in rival.findall("r"):
            extid = _int(r, "id", -1)
            if extid > 0 and extid not in profile["rivals"] and get_card_by_extid(extid) is not None:
                profile["rivals"].append(extid)

    dojo = pdata.find("dojo")
    if dojo is not None and dojo.find("class") is not None:
        cls = str(_int(dojo, "class"))
        clear_type = _int(dojo, "clear_type")
        ar = _int(dojo, "t_ar")
        score = _int(dojo, "t_score")
        now = int(time.time())
        old = profile["dojo"].get(cls, {})
        if ar >= old.get("ar", 0):
            record_time = now
        else:
            record_time = old.get("record_timestamp", now)
            ar = old.get("ar", 0)
            score = max(score, old.get("score", 0))
            clear_type = max(clear_type, old.get("clear_type", 0))
        profile["dojo"][cls] = {
            "clear_type": clear_type,
            "ar": ar,
            "score": score,
            "plays": old.get("plays", 0) + 1,
            "play_timestamp": now,
            "record_timestamp": record_time,
        }

    yurukome_list = pdata.find("yurukome_list")
    if yurukome_list is not None:
        for y in yurukome_list.findall("yurukome"):
            yid = _int(y, "yurukome_id", -1)
            if yid >= 0 and yid not in profile["yurukome"]:
                profile["yurukome"].append(yid)

    mycourse = pdata.find("mycourse")
    if mycourse is not None:
        scores = [_int(mycourse, f"score_{i}", -1) for i in (1, 2, 3, 4)]
        total = sum(s for s in scores if s >= 0)
        old = profile["mycourse"]
        oldtotal = sum(max(old.get(f"score_{i}", 0), 0) for i in (1, 2, 3, 4))
        if total >= oldtotal:
            for i in (1, 2, 3, 4):
                old[f"music_id_{i}"] = _int(mycourse, f"music_id_{i}", -1)
                old[f"note_grade_{i}"] = _int(mycourse, f"note_grade_{i}", -1)
                old[f"score_{i}"] = scores[i - 1]
            old["insert_time"] = int(time.time())

    update_play_statistics(profile)
    return profile


# ---------------------------------------------------------------- scores
def save_stage_logs(extid, game_version, root):
    stglog = root.find("pdata/stglog")
    if stglog is None:
        return

    now = int(time.time())
    for log in stglog.findall("log"):
        music_id = _int(log, "mid")
        chart = _int(log, "ng")
        clear_type = _int(log, "ct", -1)
        if music_id == 0 and chart == 0 and clear_type == -1:
            continue  # dummy entry during profile creation

        points = _int(log, "sc")
        ar = _int(log, "ar")
        param = _int(log, "param")
        miss = _int(log, "jt_ms", -1)
        kflag = _int(log, "k_flag")
        combo = param & 0x3
        param = param ^ combo

        cond = (
            (where("reflec_id") == int(extid))
            & (where("game_version") == game_version)
            & (where("music_id") == music_id)
            & (where("note_grade") == chart)
        )
        old = scores_table().get(cond) or {}
        row = {
            "reflec_id": int(extid),
            "game_version": game_version,
            "music_id": music_id,
            "note_grade": chart,
            "score": max(points, old.get("score", 0)),
            "cnt": old.get("cnt", 0) + 1,
            "achievement_rate": old.get("achievement_rate", 0),
            "clear_type": old.get("clear_type", 0),
            "combo_type": max(combo, old.get("combo_type", 0)),
            "param": max(param, old.get("param", 0)),
            "kflag": max(kflag, old.get("kflag", 0)),
            "miss_count": old.get("miss_count", -1),
            "best_score_time": old.get("best_score_time", now),
            "best_ar_time": old.get("best_ar_time", now),
            "best_ct_time": old.get("best_ct_time", now),
            "best_ms_time": old.get("best_ms_time", now),
            "last_played_time": now,
            "timestamp": now,
        }
        if points >= old.get("score", 0):
            row["best_score_time"] = now
        if ar >= old.get("achievement_rate", 0):
            row["achievement_rate"] = ar
            row["best_ar_time"] = now
        if clear_type >= old.get("clear_type", 0):
            row["clear_type"] = clear_type
            row["best_ct_time"] = now
        if miss >= 0 and (old.get("miss_count", -1) < 0 or miss <= old.get("miss_count", 999999)):
            row["miss_count"] = miss
            row["best_ms_time"] = now
        scores_table().upsert(row, cond)


def format_scores(extid, game_version):
    rows = scores_table().search((where("reflec_id") == int(extid)) & (where("game_version") == game_version))
    return E.response(
        E.player(
            E.pdata(
                E.record(
                    *[
                        E.rec(
                            E.mid(r["music_id"], __type="s16"),
                            E.ntgrd(r["note_grade"], __type="s8"),
                            E.pc(r["cnt"], __type="s32"),
                            E.ct(r["clear_type"], __type="s8"),
                            E.ar(r["achievement_rate"], __type="s16"),
                            E.scr(r["score"], __type="s16"),
                            E.ms(r["miss_count"], __type="s16"),
                            E.param(r["combo_type"] + r["param"], __type="s16"),
                            E.bscrt(r["best_score_time"], __type="s32"),
                            E.bart(r["best_ar_time"], __type="s32"),
                            E.bctt(r["best_ct_time"], __type="s32"),
                            E.bmst(r["best_ms_time"], __type="s32"),
                            E.time(r["last_played_time"], __type="s32"),
                            E.k_flag(r["kflag"], __type="s32"),
                        )
                        for r in rows
                    ]
                ),
            ),
        )
    )


def compute_ranks(game_version, current_scores, current_minigame):
    rows = scores_table().search(where("game_version") == game_version)
    totals = {}
    for r in rows:
        if r["clear_type"] < GAME_CLEAR_CLEARED:
            continue
        t = totals.setdefault(r["reflec_id"], [0, 0, 0, 0, 0])
        t[0] += r["score"]
        if 0 <= r["note_grade"] <= 3:
            t[1 + r["note_grade"]] += r["score"]

    minigame = []
    for card in profiles_table().all():
        prof = card.get("version", {}).get(str(game_version))
        if prof is not None:
            minigame.append(prof.get("mgsc", 0))

    lists = [sorted((t[i] for t in totals.values()), reverse=True) + [0] for i in range(5)]
    lists.append(sorted(minigame, reverse=True) + [0])
    earned = _pad(current_scores, 5) + [current_minigame]
    places = []
    for i in range(6):
        place = 1
        for s in lists[i]:
            if earned[i] >= s:
                break
            place += 1
        places.append(place)
    return places[:5], places[5]


# ---------------------------------------------------------------- handlers
@router.post("/{gameinfo}/player/rb5_player_start")
async def player_rb5_player_start(request: Request):
    request_info = await core_process_request(request)
    root = request_info["root"][0]

    refid = _text(root, "rid")
    plyid = 0
    if refid:
        card = get_card(refid)
        plyid = random.randint(1, 0x7FFFFFFF)
        play_sessions[refid] = {
            "plyid": plyid,
            "extid": card["reflec_id"] if card and "reflec_id" in card else 0,
            "ga": _int_list(root, "ga", []),
            "gp": _int(root, "gp"),
            "la": _int_list(root, "la", []),
            "pnid": _int_list(root, "pnid", []),
            "time": int(time.time()),
        }

    response = E.response(
        E.player(
            E.plyid(plyid, __type="s32"),
            E.start_time(int(time.time()) * 1000, __type="u64"),
            *event_info_nodes(),
        )
    )
    return await _respond(request, response)


@router.post("/{gameinfo}/player/rb5_player_end")
async def player_rb5_player_end(request: Request):
    request_info = await core_process_request(request)
    refid = _text(request_info["root"][0], "rid")
    session = play_sessions.pop(refid, None)
    if session is not None:
        from modules.reflec.lobby import remove_lobbies_of  # local import: lobby imports this module

        remove_lobbies_of(session.get("extid", 0))
    return await _respond(request, E.response(E.player()))


@router.post("/{gameinfo}/player/rb5_player_delete")
async def player_rb5_player_delete(request: Request):
    request_info = await core_process_request(request)
    return await _respond(request, E.response(E.player()))


@router.post("/{gameinfo}/player/rb5_player_succeed")
async def player_rb5_player_succeed(request: Request):
    request_info = await core_process_request(request)
    # No previous-version data to inherit: tell the game this is a new player.
    response = E.response(
        E.player(
            E.name("", __type="str"),
            E.grd(-1, __type="s32"),
            E.ap(-1, __type="s32"),
            E.uattr(0, __type="s32"),
        )
    )
    return await _respond(request, response)


@router.post("/{gameinfo}/player/rb5_player_read")
async def player_rb5_player_read(request: Request):
    request_info = await core_process_request(request)
    game_version = request_info["game_version"]

    refid = _text(request_info["root"][0], "rid")
    card, profile = get_game_profile(refid, game_version)
    if profile is None:
        response = E.response(E.player())
    else:
        response = format_profile(card["reflec_id"], profile)
    return await _respond(request, response)


@router.post("/{gameinfo}/player/rb5_player_read_score_old_5")
async def player_rb5_player_read_score_old_5(request: Request):
    request_info = await core_process_request(request)
    return await _respond(request, E.response(E.player(E.pdata(E.record_old()))))


@router.post("/{gameinfo}/player/rb5_player_read_score_5")
async def player_rb5_player_read_score_5(request: Request):
    request_info = await core_process_request(request)
    game_version = request_info["game_version"]

    refid = _text(request_info["root"][0], "rid")
    card = get_card(refid)
    if card is None or "reflec_id" not in card:
        response = E.response(E.player(E.pdata(E.record())))
    else:
        response = format_scores(card["reflec_id"], game_version)
    return await _respond(request, response)


@router.post("/{gameinfo}/player/rb5_player_read_rival_score_5")
async def player_rb5_player_read_rival_score_5(request: Request):
    request_info = await core_process_request(request)
    game_version = request_info["game_version"]
    root = request_info["root"][0]

    extid = _int(root, "uid", -1)
    music_id = _int(root, "music_id")
    chart = _int(root, "note_grade")
    card = get_card_by_extid(extid)
    row = None
    if card is not None:
        row = scores_table().get(
            (where("reflec_id") == extid)
            & (where("game_version") == game_version)
            & (where("music_id") == music_id)
            & (where("note_grade") == chart)
        )

    children = []
    if row is not None:
        prof = card.get("version", {}).get(str(game_version), {})
        children.append(
            E.player_select_score(
                E.user_id(extid, __type="s32"),
                E.name(prof.get("name", ""), __type="str"),
                E.m_score(row["score"], __type="s32"),
                E.m_scoreTime(row["best_score_time"], __type="s32"),
                E.m_iconID(prof.get("config", {}).get("icon_id", 0), __type="s16"),
            )
        )
    return await _respond(request, E.response(E.player(*children)))


@router.post("/{gameinfo}/player/rb5_player_read_rival_ranking_data_5")
async def player_rb5_player_read_rival_ranking_data_5(request: Request):
    request_info = await core_process_request(request)
    game_version = request_info["game_version"]

    extid = _int(request_info["root"][0], "uid", -1)
    card = get_card_by_extid(extid)
    prof = card.get("version", {}).get(str(game_version)) if card else None

    rl_nodes = []
    for rival_extid in (prof or {}).get("rivals", []):
        rcard = get_card_by_extid(rival_extid)
        rprof = rcard.get("version", {}).get(str(game_version)) if rcard else None
        if rprof is None:
            continue
        by_music = {}
        for r in scores_table().search((where("reflec_id") == rival_extid) & (where("game_version") == game_version)):
            by_music.setdefault(r["music_id"], [None] * 4)
            if 0 <= r["note_grade"] <= 3:
                by_music[r["music_id"]][r["note_grade"]] = r
        rl_nodes.append(
            E.rl(
                E.uid(rival_extid, __type="s32"),
                E.nm(rprof.get("name", ""), __type="str"),
                E.ic(rprof.get("config", {}).get("icon_id", 0), __type="s16"),
                *[
                    E.sl(
                        E.mid(mid, __type="s16"),
                        E.m([(r["score"] << 32) if r else 0 for r in rows], __type="u64"),
                        E.t([r["best_score_time"] if r else 0 for r in rows], __type="u64"),
                    )
                    for mid, rows in sorted(by_music.items())
                ],
            )
        )
    return await _respond(request, E.response(E.player(E.rival_data(*rl_nodes))))


@router.post("/{gameinfo}/player/rb5_player_read_rank_5")
async def player_rb5_player_read_rank_5(request: Request):
    request_info = await core_process_request(request)
    game_version = request_info["game_version"]
    root = request_info["root"][0]

    ranks, minigame_rank = compute_ranks(game_version, _int_list(root, "sc", []), _int(root, "mg_sc"))
    response = E.response(
        E.player(
            E.tbs(
                E.new_rank(ranks, __type="s32"),
                E.old_rank([-1] * 5, __type="s32"),
            ),
            E.mng(
                E.new_rank(minigame_rank, __type="s32"),
                E.old_rank(-1, __type="s32"),
            ),
        )
    )
    return await _respond(request, response)


@router.post("/{gameinfo}/player/rb5_player_write_5")
async def player_rb5_player_write_5(request: Request):
    request_info = await core_process_request(request)
    game_version = request_info["game_version"]
    root = request_info["root"][0]

    refid = _text(root, "pdata/account/rid")
    extid = 0
    if refid:
        card, profile = get_game_profile(refid, game_version)
        if profile is None:
            profile = new_game_profile(game_version, _text(root, "pdata/base/name", "") or "")
        update_profile_from_write(profile, root)
        save_game_profile(refid, game_version, profile)
        card = get_card(refid)
        extid = card["reflec_id"]
        save_stage_logs(extid, game_version, root)

    return await _respond(request, E.response(E.player(E.uid(extid, __type="s32"))))
