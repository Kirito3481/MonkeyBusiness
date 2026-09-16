import random
import time

from tinydb import where

from core_common import E
from core_database import get_db

# pop'n music pcb24/info24/player24 protocol. The module names date from pop'n 24
# Usaneko, but M39-2025092400 is pop'n music Jam&Fizz (popn22.dll psmap tables):
# account nice[100], sp_riddles_id/option_tuto/sc_news_no, option lift/guide_se_vol,
# event2021, riddles_data, event_p27 (team/battery), event_p28 (burger orders,
# neon_lamp, neko_stamp). Based on bemaniutils popn/common.py + usaneko.py.

GAME_MAX_DECO_ID = 97

# Event phases sent in info24.common / player24.start (usaneko.py get_common_config).
MUSIC_PHASE = 11  # 0..11, default song unlock phase (11 = everything)
NAVIKUN_PHASE = 15  # 0..15, NAVI-kun song phase (15 = everything)
HOLIDAY_GREETING = 0  # 0 Okay!, 1 Merry Christmas!, 2 Happy New Year!
ACTIVE_EVENT = 1  # 0 none, 1 NAVI-kun event (sends areas), 2 Daily Mission
ENABLE_NET_TAISEN = 0

PHASES = {
    0: MUSIC_PHASE,
    1: 2,
    2: HOLIDAY_GREETING,
    3: 4,
    4: 1,
    5: ENABLE_NET_TAISEN,
    6: 1,  # NAVI-kun shunkyoku toujou (song 1608)
    7: 1,
    8: 2,
    9: 2 if ACTIVE_EVENT == 2 else 0,  # Daily Mission
    10: NAVIKUN_PHASE,
    11: 1,
    12: 2,
    13: 1,  # Pop'n Peace preview song (1703)
}
SEND_AREAS = ACTIVE_EVENT == 1

# Game-side chart / medal / rank values
CHART_EASY, CHART_NORMAL, CHART_HYPER, CHART_EX = 0, 1, 2, 3
MEDAL_NO_PLAY = 0
RANK_E, RANK_D, RANK_C, RANK_B, RANK_A, RANK_AA, RANK_AAA, RANK_S = 1, 2, 3, 4, 5, 6, 7, 8


def score_to_rank(score):
    if score < 50000:
        return RANK_E
    if score < 62000:
        return RANK_D
    if score < 72000:
        return RANK_C
    if score < 82000:
        return RANK_B
    if score < 90000:
        return RANK_A
    if score < 95000:
        return RANK_AA
    if score < 98000:
        return RANK_AAA
    return RANK_S


# ---------------------------------------------------------------- xml helpers
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


def _pad(values, size, fill=-1):
    values = list(values or [])[:size]
    return values + [fill] * (size - len(values))


# ---------------------------------------------------------------- storage
def profiles_table():
    return get_db().table("popn_profile")


def scores_table():
    return get_db().table("popn_scores_best")


def get_card(refid):
    if refid is None:
        return None
    return profiles_table().get(where("card") == refid)


def ensure_card(refid):
    db = profiles_table()
    card = db.get(where("card") == refid) or {"card": refid, "version": {}}
    if "popn_id" not in card:
        card["popn_id"] = random.randint(10000000, 99999999)
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


ACCOUNT_INTS = (
    "tutorial", "read_news", "area_id", "use_navi", "ranking_node", "chara_ranking_kind_id",
    "navi_evolution_flg", "ranking_news_last_no", "power_point", "player_point",
    "chocolate_sp_chara", "chocolate_pass_cnt", "chocolate_hon_cnt", "chocolate_giri_cnt", "chocolate_kokyu_cnt",
    "card_again_count", "sp_riddles_id", "sc_news_no",
)
ACCOUNT_LISTS = {"nice": 100, "favorite_chara": 20, "special_area": 8, "chocolate_charalist": 5, "teacher_setting": 10, "power_point_list": 20}
CONFIG_INTS = (
    "mode", "chara", "music", "sheet", "category", "sub_category", "chara_category", "course_id",
    "course_folder", "ms_banner_disp", "ms_down_info", "ms_side_info", "ms_raise_type", "ms_rnd_type", "banner_sort",
)
OPTION_INTS = (
    "hispeed", "popkun", "hidden", "hidden_rate", "sudden", "sudden_rate", "randmir", "gauge_type",
    "ojama_0", "ojama_1", "forever_0", "forever_1", "full_setting", "judge", "guide_se", "guide_se_vol", "lift", "lift_rate",
)
OPTION_BOOLS = {"hidden", "sudden", "forever_0", "forever_1", "full_setting", "lift"}
OPTION_DEFAULTS = {"guide_se_vol": 3}
EVENT2021_INTS = ("point", "step", "step_nos")
EVENT2021_LISTS = {"quest_point": 8, "quest_point_nos": 13}
EVENT_P27_INTS = ("team_id", "first_play", "select_battery_id", "elem_first_play", "today_first_play")
EVENT_P28_INTS = ("burger_first_play", "burger_daily_bonus", "neko_daily_bonus")
SP_RIDDLES_INTS = ("kaimei_gauge", "is_cleared", "riddles_cleared", "select_count", "other_count")
CUSTOMIZE_INTS = ("effect_left", "effect_center", "effect_right", "hukidashi", "comment_1", "comment_2")


def new_game_profile(game_version, name):
    return {
        "game_version": game_version,
        "name": name,
        "tutorial": -1,  # -1 = derive from play count on load
        "read_news": 0,
        "area_id": 0,
        "use_navi": 0,
        "ranking_node": 0,
        "chara_ranking_kind_id": 0,
        "navi_evolution_flg": 0,
        "ranking_news_last_no": 0,
        "power_point": 0,
        "player_point": 300,
        "chocolate_sp_chara": -1,
        "chocolate_pass_cnt": 0,
        "chocolate_hon_cnt": 0,
        "chocolate_giri_cnt": 0,
        "chocolate_kokyu_cnt": 0,
        "card_again_count": 0,
        "sp_riddles_id": -1,
        "sc_news_no": -1,
        "option_tuto": 0,
        "nice": [-1] * 100,
        "favorite_chara": [-1] * 20,
        "special_area": [-1] * 8,
        "chocolate_charalist": [-1] * 5,
        "teacher_setting": [-1] * 10,
        "power_point_list": [-1] * 20,
        "ep": 0,
        "stamp_id": 0,
        "stamp_cnt": 0,
        "config": {"mode": 0, "chara": -1, "music": -1, "sheet": 0, "category": -1, "sub_category": -1,
                   "chara_category": -1, "course_id": -1, "course_folder": -1, "ms_banner_disp": -1,
                   "ms_down_info": -1, "ms_side_info": -1, "ms_raise_type": -1, "ms_rnd_type": -1, "banner_sort": -1},
        "option": {k: OPTION_DEFAULTS.get(k, 0) for k in OPTION_INTS},
        "customize": {k: 0 for k in CUSTOMIZE_INTS},
        "navi_points": None,
        "items": {},  # "type:id" -> {"param", "is_new", "get_time"}
        "chara": {},  # chara_id -> friendship
        "navi": {},  # navi_id -> friendship
        "area": {},  # area_id -> {index, points, cleared, diary}
        "courses": {},  # "course_id:sheet" -> {score, clear_type, clear_rank, count, pref, lid}
        "missions": {},  # mission_id -> {points, complete, day}
        "fes": {},  # fes_id -> {index, points, cleared}
        "event2021": {"point": 0, "step": 0, "quest_point": [0] * 8, "step_nos": 0, "quest_point_nos": [0] * 13},
        "riddles": {"sp": [], "sh": []},  # sp: list of {kaimei_gauge,...} (index = riddle no), sh: list of ids
        "event_p27": {"team_id": 0, "first_play": 0, "select_battery_id": 0, "elem_first_play": 0,
                      "today_first_play": 0, "teams": {}, "batteries": {}},
        "event_p28": {"burger_first_play": 0, "burger_daily_bonus": 0, "neko_daily_bonus": 0,
                      "current_order": [-1, -1, -1], "orders": {}, "neon_lamp": None, "neko_stamps": {}},
        "custom_courses": {},  # course_id -> last write_course payload
        "play_count": 0,
        "today_play_cnt": 0,
        "consecutive_days": 0,
        "total_days": 0,
        "last_play_day": "",
    }


def update_play_statistics(profile):
    today = time.strftime("%Y-%m-%d")
    yesterday = time.strftime("%Y-%m-%d", time.localtime(time.time() - 86400))
    profile["play_count"] += 1
    if profile["last_play_day"] == today:
        profile["today_play_cnt"] += 1
    else:
        profile["today_play_cnt"] = 1
        profile["total_days"] += 1
        profile["consecutive_days"] = profile["consecutive_days"] + 1 if profile["last_play_day"] == yesterday else 1
        profile["last_play_day"] = today


# ---------------------------------------------------------------- common info (info24.common / player24.start)
def common_info_nodes(game_version):
    nodes = [
        E.phase(E.event_id(pid, __type="s16"), E.phase(val, __type="s16"))
        for pid, val in PHASES.items()
    ]
    # ranking_info (weekly courses) omitted: no course scheduling on this server.
    if SEND_AREAS:
        nodes += [
            E.area(
                E.area_id(i, __type="s16"),
                E.end_date(0, __type="u64"),
                E.medal_id(i, __type="s16"),
                E.is_limit(False, __type="bool"),
            )
            for i in range(1, 16)
        ]
    nodes += [E.choco(E.choco_id(i, __type="s16"), E.param(-1, __type="s32")) for i in range(5)]
    for goods_id in range(GAME_MAX_DECO_ID):
        if goods_id < 15:
            price = 30
        elif goods_id < 30:
            price = 40
        elif goods_id < 45:
            price = 60
        elif goods_id < 60:
            price = 80
        elif goods_id < 98:
            price = 200
        else:
            price = 250
        nodes.append(
            E.goods(
                E.item_id(goods_id + 1, __type="s32"),
                E.item_type(3, __type="s16"),
                E.price(price, __type="s32"),
                E.goods_type(0, __type="s16"),
            )
        )

    charas = {}
    for card in profiles_table().all():
        prof = card.get("version", {}).get(str(game_version))
        if prof is None:
            continue
        chara = prof.get("config", {}).get("chara", -1)
        if chara > 0:
            charas[chara] = charas.get(chara, 0) + 1
    for rank, (chara, _) in enumerate(sorted(charas.items(), key=lambda kv: -kv[1])[:20], start=1):
        nodes.append(E.popular(E.rank(rank, __type="s16"), E.chara_num(chara, __type="s16")))

    plays = {}
    for row in scores_table().search(where("game_version") == game_version):
        plays[row["music_num"]] = plays.get(row["music_num"], 0) + row.get("cnt", 1)
    for mid, _ in sorted(plays.items(), key=lambda kv: -kv[1])[:500]:
        nodes.append(E.popular_music(E.music_num(mid, __type="s16")))

    return nodes
