import time
import zlib

from tinydb import where

from fastapi import APIRouter, Request, Response

from core_common import core_process_request, core_prepare_response, E
from modules.popn.common import (
    ACCOUNT_INTS, ACCOUNT_LISTS, CONFIG_INTS, CUSTOMIZE_INTS, EVENT2021_INTS, EVENT2021_LISTS,
    EVENT_P27_INTS, EVENT_P28_INTS, OPTION_BOOLS, OPTION_INTS, SP_RIDDLES_INTS,
    MEDAL_NO_PLAY, _int, _int_list, _pad, _text, common_info_nodes, ensure_card, get_card,
    get_game_profile, new_game_profile, profiles_table, save_game_profile, score_to_rank, scores_table,
    update_play_statistics,
)

router = APIRouter(prefix="/local2", tags=["local2"])
router.model_whitelist = ["M39"]

# pop'n music player24 for Jam&Fizz (popn22.dll, M39-2025092400); node names and
# types follow the DLL psmap tables, base logic from bemaniutils popn/common.py.
# Handler names must stay player24_<method> for the url_slash 0 forwarder.
# Medals (clear_type) and charts (sheet_num) are stored as the game sends them.

FORCE_UNLOCK_SONGS = False  # needs a song list to be safe; popnhax handles unlocks locally


async def _respond(request, response):
    response_body, response_headers = await core_prepare_response(request, response)
    return Response(content=response_body, headers=response_headers)


def format_extid(popn_id):
    data = str(popn_id)
    crc = abs(zlib.crc32(data.encode("ascii"))) % 10000
    return f"{data}{crc:04d}"


# ---------------------------------------------------------------- scores
def user_scores(popn_id, game_version):
    return scores_table().search((where("popn_id") == int(popn_id)) & (where("game_version") == game_version))


def score_nodes(rows):
    return [
        E.music(
            E.music_num(r["music_num"], __type="s16"),
            E.sheet_num(r["sheet_num"], __type="u8"),
            E.score(r["score"], __type="s32"),
            E.clear_type(r["clear_type"], __type="u8"),
            E.clear_rank(score_to_rank(r["score"]), __type="u8"),
            E.cnt(r["cnt"], __type="s16"),
        )
        for r in rows
        if r["clear_type"] != MEDAL_NO_PLAY
    ]


def update_score(popn_id, game_version, music_num, sheet_num, score, medal, combo, stats):
    cond = (
        (where("popn_id") == int(popn_id))
        & (where("game_version") == game_version)
        & (where("music_num") == music_num)
        & (where("sheet_num") == sheet_num)
    )
    old = scores_table().get(cond) or {}
    raised = score > old.get("score", -1)
    row = {
        "popn_id": int(popn_id),
        "game_version": game_version,
        "music_num": music_num,
        "sheet_num": sheet_num,
        "score": max(score, old.get("score", 0)),
        "clear_type": max(medal, old.get("clear_type", 0)),
        "combo": max(combo, old.get("combo", 0)),
        "cnt": old.get("cnt", 0) + 1,
        "timestamp": int(time.time()),
    }
    row.update(stats if raised or not old else {k: old.get(k, 0) for k in stats})
    scores_table().upsert(row, cond)


# ---------------------------------------------------------------- profile
def format_profile(popn_id, profile, game_version):
    cfg = profile["config"]
    cus = profile["customize"]
    rows = user_scores(popn_id, game_version)

    last_played = [r["music_num"] for r in sorted(rows, key=lambda r: -r.get("timestamp", 0))[:10]]
    most_played = [r["music_num"] for r in sorted(rows, key=lambda r: -r.get("cnt", 0))[:20]]

    tutorial = profile["tutorial"]
    if tutorial < 0:
        tutorial = 100 if profile["play_count"] > 1 else 0

    items = []
    for key, item in profile["items"].items():
        itype, iid = (int(v) for v in key.split(":"))
        if FORCE_UNLOCK_SONGS and itype == 0:
            continue
        items.append(
            E.item(
                E("type", itype, __type="u8"),
                E("id", iid, __type="u16"),
                E.param(item.get("param", 0), __type="u16"),
                E.is_new(bool(item.get("is_new", 0)), __type="bool"),
                E.get_time(item.get("get_time", 0), __type="u64"),
            )
        )

    navi_children = []
    if profile["navi_points"] is not None:
        navi_children.append(E.raisePoint(_pad(profile["navi_points"], 5, 0), __type="s32"))
    navi_children += [
        E.navi_param(E.navi_id(int(nid), __type="u16"), E.friendship(fs, __type="s32"))
        for nid, fs in profile["navi"].items()
    ]

    today = time.strftime("%Y-%m-%d")
    missions = {mid: m for mid, m in profile["missions"].items() if m.get("day") == today}
    import random as _random

    while len(missions) < 3:
        nid = str(_random.randint(1, 228))
        if nid not in missions:
            missions[nid] = {"points": 0, "complete": 0}

    return E.response(
        E.player24(
            E.result(0, __type="s8"),
            E.account(
                E.g_pm_id(format_extid(popn_id), __type="str"),
                E.name(profile["name"] or "なし", __type="str"),
                E.area_id(profile["area_id"], __type="s16"),
                E.use_navi(profile["use_navi"], __type="s16"),
                E.read_news(profile["read_news"], __type="s16"),
                E.nice(_pad(profile["nice"], 100), __type="s16"),
                E.favorite_chara(_pad(profile["favorite_chara"], 20), __type="s16"),
                E.special_area(_pad(profile["special_area"], 8), __type="s16"),
                E.chocolate_charalist(_pad(profile["chocolate_charalist"], 5), __type="s16"),
                E.chocolate_sp_chara(profile["chocolate_sp_chara"], __type="s32"),
                E.chocolate_pass_cnt(profile["chocolate_pass_cnt"], __type="s32"),
                E.chocolate_hon_cnt(profile["chocolate_hon_cnt"], __type="s32"),
                E.teacher_setting(_pad(profile["teacher_setting"], 10), __type="s16"),
                E.welcom_pack(False, __type="bool"),
                E.ranking_node(profile["ranking_node"], __type="s32"),
                E.chara_ranking_kind_id(profile["chara_ranking_kind_id"], __type="s32"),
                E.navi_evolution_flg(profile["navi_evolution_flg"], __type="s8"),
                E.ranking_news_last_no(profile["ranking_news_last_no"], __type="s32"),
                E.power_point(profile["power_point"], __type="s32"),
                E.player_point(profile["player_point"], __type="s32"),
                E.power_point_list(_pad(profile["power_point_list"], 20), __type="s32"),
                E.tutorial(tutorial, __type="s16"),
                E.staff(0, __type="s8"),
                E.item_type(0, __type="s16"),
                E.item_id(0, __type="s16"),
                E.is_conv(0, __type="s8"),
                E.license_data([-1] * 20, __type="s16"),
                E.my_best(_pad(most_played, 20), __type="s16"),
                E.latest_music(_pad(last_played, 10), __type="s16"),
                E.total_play_cnt(profile["play_count"], __type="s16"),
                E.today_play_cnt(profile["today_play_cnt"], __type="s16"),
                E.consecutive_days(profile["consecutive_days"], __type="s16"),
                E.total_days(profile["total_days"], __type="s16"),
                E.interval_day(0, __type="s16"),
                E.active_fr_num(0, __type="u8"),
                E.sp_riddles_id(profile["sp_riddles_id"], __type="s16"),
                E.option_tuto(bool(profile["option_tuto"]), __type="bool"),
                E.sc_news_no(profile["sc_news_no"], __type="s32"),
            ),
            E.eaappli(E.relation(-1, __type="s8")),
            E.info(E.ep(profile["ep"], __type="u16")),
            E.config(
                E.mode(cfg["mode"], __type="u8"),
                E.chara(cfg["chara"], __type="s16"),
                E.music(cfg["music"], __type="s16"),
                E.sheet(cfg["sheet"], __type="u8"),
                E.category(cfg["category"], __type="s8"),
                E.sub_category(cfg["sub_category"], __type="s8"),
                E.chara_category(cfg["chara_category"], __type="s8"),
                E.course_id(cfg["course_id"], __type="s16"),
                E.course_folder(cfg["course_folder"], __type="s8"),
                E.ms_banner_disp(cfg["ms_banner_disp"], __type="s8"),
                E.ms_down_info(cfg["ms_down_info"], __type="s8"),
                E.ms_side_info(cfg["ms_side_info"], __type="s8"),
                E.ms_raise_type(cfg["ms_raise_type"], __type="s8"),
                E.ms_rnd_type(cfg["ms_rnd_type"], __type="s8"),
                E.banner_sort(cfg["banner_sort"], __type="s8"),
            ),
            option_node(profile),
            E.custom_cate(
                E.valid(0, __type="s8"),
                E.lv_min(-1, __type="s8"),
                E.lv_max(-1, __type="s8"),
                E.medal_min(-1, __type="s8"),
                E.medal_max(-1, __type="s8"),
                E.friend_no(-1, __type="s8"),
                E.score_flg(-1, __type="s8"),
            ),
            E.navi_data(*navi_children),
            *items,
            *[
                E.chara_param(E.chara_id(int(cid), __type="u16"), E.friendship(fs, __type="u16"))
                for cid, fs in profile["chara"].items()
            ],
            *[
                E.area(
                    E.area_id(int(aid), __type="u32"),
                    E.chapter_index(a.get("index", 0), __type="u8"),
                    E.gauge_point(a.get("points", 0), __type="u16"),
                    E.is_cleared(bool(a.get("cleared", 0)), __type="bool"),
                    E.diary(a.get("diary", 0), __type="u32"),
                )
                for aid, a in profile["area"].items()
            ],
            *[
                E.course_data(
                    E.course_id(int(key.split(":")[0]), __type="s16"),
                    E.clear_type(c.get("clear_type", 0), __type="u8"),
                    E.clear_rank(c.get("clear_rank", 0), __type="u8"),
                    E.total_score(c.get("score", 0), __type="s32"),
                    E.update_count(c.get("count", 0), __type="s32"),
                    E.sheet_num(int(key.split(":")[1]), __type="u8"),
                )
                for key, c in profile["courses"].items()
            ],
            *[
                E.mission(
                    E.mission_id(int(mid), __type="u32"),
                    E.gauge_point(m.get("points", 0), __type="u32"),
                    E.mission_comp(m.get("complete", 0), __type="u32"),
                )
                for mid, m in list(missions.items())[:3]
            ],
            E.netvs(
                E.record([0] * 6, __type="s16"),
                *[E.dialog("", __type="str") for _ in range(6)],
                E.ojama_condition([0] * 74, __type="s8"),
                E.set_ojama([0] * 3, __type="s8"),
                E.set_recommend([0] * 3, __type="s8"),
                E.netvs_play_cnt(0, __type="u32"),
            ),
            E.customize(*[E(k, cus[k], __type="u16") for k in CUSTOMIZE_INTS]),
            E.stamp(
                E.stamp_id(profile["stamp_id"], __type="s16"),
                E.cnt(profile["stamp_cnt"], __type="s16"),
            ),
            *[
                E.fes(
                    E.fes_id(int(fid), __type="u32"),
                    E.chapter_index(f.get("index", 0), __type="u8"),
                    E.gauge_point(f.get("points", 0), __type="u16"),
                    E.is_cleared(bool(f.get("cleared", 0)), __type="bool"),
                )
                for fid, f in profile["fes"].items()
            ],
            *jamfizz_event_nodes(profile),
        )
    )


def option_node(profile):
    opt = profile["option"]
    return E.option(
        E.hispeed(opt["hispeed"], __type="s16"),
        E.popkun(opt["popkun"], __type="u8"),
        E.hidden(bool(opt["hidden"]), __type="bool"),
        E.hidden_rate(opt["hidden_rate"], __type="s16"),
        E.sudden(bool(opt["sudden"]), __type="bool"),
        E.sudden_rate(opt["sudden_rate"], __type="s16"),
        E.randmir(opt["randmir"], __type="s8"),
        E.gauge_type(opt["gauge_type"], __type="s8"),
        E.ojama_0(opt["ojama_0"], __type="u8"),
        E.ojama_1(opt["ojama_1"], __type="u8"),
        E.forever_0(bool(opt["forever_0"]), __type="bool"),
        E.forever_1(bool(opt["forever_1"]), __type="bool"),
        E.full_setting(bool(opt["full_setting"]), __type="bool"),
        E.judge(opt["judge"], __type="u8"),
        E.guide_se(opt["guide_se"], __type="s8"),
        E.guide_se_vol(opt["guide_se_vol"], __type="u8"),
        E.lift(bool(opt["lift"]), __type="bool"),
        E.lift_rate(opt["lift_rate"], __type="s16"),
    )


def jamfizz_event_nodes(profile):
    """event2021 / riddles_data / event_p27 / event_p28 (Jam&Fizz player24.read)."""
    ev = profile["event2021"]
    rd = profile["riddles"]
    p27 = profile["event_p27"]
    p28 = profile["event_p28"]
    lamp = p28.get("neon_lamp") or {"id": 49, "point": 0, "is_cleared": 1}
    nodes = [
        E.event2021(
            E.point(ev["point"], __type="u32"),
            E.step(ev["step"], __type="u8"),
            E.quest_point(_pad(ev["quest_point"], 8, 0), __type="u32"),
            E.step_nos(ev["step_nos"], __type="u8"),
            E.quest_point_nos(_pad(ev["quest_point_nos"], 13, 0), __type="u32"),
        ),
        E.event_p27(
            E.team_id(p27["team_id"], __type="s16"),
            E.first_play(bool(p27["first_play"]), __type="bool"),
            E.select_battery_id(p27["select_battery_id"], __type="s16"),
            E.elem_first_play(bool(p27["elem_first_play"]), __type="bool"),
            E.today_first_play(bool(p27["today_first_play"]), __type="bool"),
            *[
                E.team(
                    E.team_id(int(tid), __type="s16"),
                    E.ex_no(t.get("ex_no", 0), __type="s16"),
                    E.point(t.get("point", 0), __type="u32"),
                    E.is_cleared(bool(t.get("is_cleared", 0)), __type="bool"),
                )
                for tid, t in p27["teams"].items()
            ],
            *[
                E.battery(
                    E.battery_id(int(bid), __type="s16"),
                    E.energy(b.get("energy", 0), __type="u32"),
                    E.is_cleared(bool(b.get("is_cleared", 0)), __type="bool"),
                )
                for bid, b in p27["batteries"].items()
            ],
        ),
        E.event_p28(
            E.burger_first_play(bool(p28["burger_first_play"]), __type="bool"),
            E.burger_daily_bonus(bool(p28["burger_daily_bonus"]), __type="bool"),
            E.current_order(_pad(p28["current_order"], 3), __type="s16"),
            E.neko_daily_bonus(bool(p28["neko_daily_bonus"]), __type="bool"),
            *[
                E.order(
                    E("id", int(oid), __type="s16"),
                    E.point(o.get("point", 0), __type="u32"),
                    E.patties(_pad(o.get("patties"), 40), __type="s16"),
                    E.is_cleared(bool(o.get("is_cleared", 0)), __type="bool"),
                )
                for oid, o in p28["orders"].items()
            ],
            E.neon_lamp(
                E("id", lamp["id"], __type="s16"),
                E.point(lamp["point"], __type="u32"),
                E.is_cleared(bool(lamp["is_cleared"]), __type="bool"),
            ),
            *[
                E.neko_stamp(
                    E("id", int(sid), __type="s16"),
                    E.point(s.get("point", 0), __type="u32"),
                    E.is_cleared(bool(s.get("is_cleared", 0)), __type="bool"),
                )
                for sid, s in p28["neko_stamps"].items()
            ],
        ),
    ]
    if rd["sp"] or rd["sh"]:
        nodes.append(
            E.riddles_data(
                *[
                    E.sp_riddles(
                        E.kaimei_gauge(r.get("kaimei_gauge", 0), __type="u16"),
                        E.is_cleared(bool(r.get("is_cleared", 0)), __type="bool"),
                        E.riddles_cleared(bool(r.get("riddles_cleared", 0)), __type="bool"),
                        E.select_count(r.get("select_count", 0), __type="u8"),
                        E.other_count(r.get("other_count", 0), __type="u32"),
                    )
                    for r in rd["sp"]
                ],
                *[E.sh_riddles(E.sh_riddles_id(int(i), __type="u32")) for i in rd["sh"]],
            )
        )
    return nodes


def _sub_node(profile, node, key, ints, lists=None):
    if node is None:
        return None
    target = profile[key]
    for k in ints:
        if node.find(k) is not None:
            target[k] = _int(node, k, target.get(k, 0))
    for k, size in (lists or {}).items():
        values = _int_list(node, k)
        if values is not None:
            target[k] = _pad(values, size, 0)
    return node


def update_jamfizz_events(profile, root):
    _sub_node(profile, root.find("event2021"), "event2021", EVENT2021_INTS, EVENT2021_LISTS)

    riddles = root.find("riddles_data")
    if riddles is not None:
        sp = [{k: _int(n, k) for k in SP_RIDDLES_INTS} for n in riddles.findall("sp_riddles")]
        if sp:
            profile["riddles"]["sp"] = sp
        sh = [_int(n, "sh_riddles_id") for n in riddles.findall("sh_riddles")]
        if sh:
            profile["riddles"]["sh"] = sh

    p27 = _sub_node(profile, root.find("event_p27"), "event_p27", EVENT_P27_INTS)
    if p27 is not None:
        for n in p27.findall("team"):
            profile["event_p27"]["teams"][str(_int(n, "team_id"))] = {
                "ex_no": _int(n, "ex_no"), "point": _int(n, "point"), "is_cleared": _int(n, "is_cleared")}
        for n in p27.findall("battery"):
            profile["event_p27"]["batteries"][str(_int(n, "battery_id"))] = {
                "energy": _int(n, "energy"), "is_cleared": _int(n, "is_cleared")}

    p28 = _sub_node(profile, root.find("event_p28"), "event_p28", EVENT_P28_INTS)
    if p28 is not None:
        order = _int_list(p28, "current_order")
        if order is not None:
            profile["event_p28"]["current_order"] = _pad(order, 3)
        for n in p28.findall("order"):
            profile["event_p28"]["orders"][str(_int(n, "id"))] = {
                "point": _int(n, "point"), "patties": _pad(_int_list(n, "patties"), 40), "is_cleared": _int(n, "is_cleared")}
        lamp = p28.find("neon_lamp")
        if lamp is not None:
            profile["event_p28"]["neon_lamp"] = {
                "id": _int(lamp, "id"), "point": _int(lamp, "point"), "is_cleared": _int(lamp, "is_cleared")}
        for n in p28.findall("neko_stamp"):
            profile["event_p28"]["neko_stamps"][str(_int(n, "id"))] = {
                "point": _int(n, "point"), "is_cleared": _int(n, "is_cleared")}

    for n in root.findall("fes"):
        profile["fes"][str(_int(n, "fes_id"))] = {
            "index": _int(n, "chapter_index"), "points": _int(n, "gauge_point"), "cleared": _int(n, "is_cleared")}


def update_profile_from_write(profile, root):
    account = root.find("account")
    if account is not None:
        for k in ACCOUNT_INTS:
            if account.find(k) is not None:
                profile[k] = _int(account, k, profile[k])
        for k, size in ACCOUNT_LISTS.items():
            values = _int_list(account, k)
            if values is not None:
                profile[k] = _pad(values, size)

    if root.find("info/ep") is not None:
        profile["ep"] = _int(root, "info/ep", profile["ep"])
    stamp = root.find("stamp")
    if stamp is not None:
        profile["stamp_id"] = _int(stamp, "stamp_id", profile["stamp_id"])
        profile["stamp_cnt"] = _int(stamp, "cnt", profile["stamp_cnt"])

    config = root.find("config")
    if config is not None:
        for k in CONFIG_INTS:
            if config.find(k) is not None:
                profile["config"][k] = _int(config, k, profile["config"][k])

    option = root.find("option")
    if option is not None:
        for k in OPTION_INTS:
            if option.find(k) is not None:
                profile["option"][k] = _int(option, k, profile["option"][k])

    customize = root.find("customize")
    if customize is not None:
        for k in CUSTOMIZE_INTS:
            if customize.find(k) is not None:
                profile["customize"][k] = _int(customize, k, profile["customize"][k])

    navi_data = root.find("navi_data")
    if navi_data is not None:
        points = _int_list(navi_data, "raisePoint")
        if points is not None:
            profile["navi_points"] = _pad(points, 5, 0)
        for node in navi_data.findall("navi_param"):
            profile["navi"][str(_int(node, "navi_id"))] = _int(node, "friendship")

    today = time.strftime("%Y-%m-%d")
    for node in root:
        if node.tag == "item":
            itype = _int(node, "type")
            if FORCE_UNLOCK_SONGS and itype == 0:
                continue
            profile["items"][f"{itype}:{_int(node, 'id')}"] = {
                "param": _int(node, "param"),
                "is_new": _int(node, "is_new"),
                "get_time": _int(node, "get_time"),
            }
        elif node.tag == "chara_param":
            profile["chara"][str(_int(node, "chara_id"))] = _int(node, "friendship")
        elif node.tag == "area":
            profile["area"][str(_int(node, "area_id"))] = {
                "index": _int(node, "chapter_index"),
                "points": _int(node, "gauge_point"),
                "cleared": _int(node, "is_cleared"),
                "diary": _int(node, "diary"),
            }
        elif node.tag == "mission":
            mission_id = _int(node, "mission_id")
            if mission_id > 0:
                profile["missions"][str(mission_id)] = {
                    "points": _int(node, "gauge_point"),
                    "complete": _int(node, "mission_comp"),
                    "day": today,
                }

    update_jamfizz_events(profile, root)
    # option_tuto is read-only for the game (never in the write psmap): treat it like
    # tutorial and mark the option tutorial as seen once a play has been saved.
    profile["option_tuto"] = 1

    # Unlock NAVI-kun and Kenshi Yonezu songs after one play (bemaniutils does the same)
    for songid in (1592, 1608):
        profile["items"].setdefault(f"0:{songid}", {"param": 0xF, "is_new": 0, "get_time": int(time.time())})

    update_play_statistics(profile)
    return profile


# ---------------------------------------------------------------- handlers
@router.post("/{gameinfo}/player24/new")
async def player24_new(request: Request):
    request_info = await core_process_request(request)
    game_version = request_info["game_version"]
    root = request_info["root"][0]

    refid = _text(root, "ref_id")
    if not refid:
        return await _respond(request, E.response(E.player24(E.result(2, __type="s8"))))

    card = ensure_card(refid)
    profile = new_game_profile(game_version, _text(root, "name") or "なし")
    save_game_profile(refid, game_version, profile)
    return await _respond(request, format_profile(card["popn_id"], profile, game_version))


@router.post("/{gameinfo}/player24/conversion")
async def player24_conversion(request: Request):
    request_info = await core_process_request(request)
    game_version = request_info["game_version"]
    root = request_info["root"][0]

    refid = _text(root, "ref_id")
    if not refid:
        return await _respond(request, E.response(E.player24(E.result(2, __type="s8"))))

    card = ensure_card(refid)
    profile = new_game_profile(game_version, _text(root, "name") or "なし")
    if root.find("chara") is not None:
        profile["config"]["chara"] = _int(root, "chara", -1)
    for node in root.findall("item"):
        profile["items"][f"{_int(node, 'type')}:{_int(node, 'id')}"] = {
            "param": _int(node, "param"),
            "is_new": _int(node, "is_new"),
            "get_time": _int(node, "get_time"),
        }
    save_game_profile(refid, game_version, profile)
    return await _respond(request, format_profile(card["popn_id"], profile, game_version))


@router.post("/{gameinfo}/player24/read")
async def player24_read(request: Request):
    request_info = await core_process_request(request)
    game_version = request_info["game_version"]

    refid = _text(request_info["root"][0], "ref_id")
    card, profile = get_game_profile(refid, game_version)
    if profile is None:
        # result 2 = no profile (no previous-version conversion on this server)
        response = E.response(E.player24(E.result(2, __type="s8")))
    else:
        response = format_profile(card["popn_id"], profile, game_version)
    return await _respond(request, response)


@router.post("/{gameinfo}/player24/write")
async def player24_write(request: Request):
    request_info = await core_process_request(request)
    game_version = request_info["game_version"]
    root = request_info["root"][0]

    refid = _text(root, "ref_id")
    if refid:
        card, profile = get_game_profile(refid, game_version)
        if profile is None:
            profile = new_game_profile(game_version, _text(root, "account/name") or "なし")
        update_profile_from_write(profile, root)
        save_game_profile(refid, game_version, profile)
    return await _respond(request, E.response(E.player24()))


@router.post("/{gameinfo}/player24/read_score")
async def player24_read_score(request: Request):
    request_info = await core_process_request(request)
    game_version = request_info["game_version"]

    card = get_card(_text(request_info["root"][0], "ref_id"))
    rows = user_scores(card["popn_id"], game_version) if card and "popn_id" in card else []
    return await _respond(request, E.response(E.player24(*score_nodes(rows))))


@router.post("/{gameinfo}/player24/write_music")
async def player24_write_music(request: Request):
    request_info = await core_process_request(request)
    game_version = request_info["game_version"]
    root = request_info["root"][0]

    card = get_card(_text(root, "ref_id"))
    if card and "popn_id" in card:
        update_score(
            card["popn_id"],
            game_version,
            _int(root, "music_num"),
            _int(root, "sheet_num"),
            _int(root, "score"),
            _int(root, "clear_type"),
            _int(root, "combo"),
            {k: _int(root, k) for k in ("cool", "great", "good", "bad")},
        )
    return await _respond(request, E.response(E.player24()))


@router.post("/{gameinfo}/player24/start")
async def player24_start(request: Request):
    request_info = await core_process_request(request)
    response = E.response(
        E.player24(
            E.play_id(0, __type="s32"),
            *common_info_nodes(request_info["game_version"]),
        )
    )
    return await _respond(request, response)


@router.post("/{gameinfo}/player24/logout")
async def player24_logout(request: Request):
    request_info = await core_process_request(request)
    return await _respond(request, E.response(E.player24()))


@router.post("/{gameinfo}/player24/buy")
async def player24_buy(request: Request):
    request_info = await core_process_request(request)
    game_version = request_info["game_version"]
    root = request_info["root"][0]

    refid = _text(root, "ref_id")
    card, profile = get_game_profile(refid, game_version) if refid else (None, None)
    if profile is not None:
        price = _int(root, "price")
        lumina = _int(root, "lumina")
        if lumina >= price:
            profile["player_point"] = lumina - price
            profile["items"][f"{_int(root, 'type')}:{_int(root, 'id')}"] = {
                "param": _int(root, "param"),
                "is_new": 1,
                "get_time": int(time.time()),
            }
            save_game_profile(refid, game_version, profile)
    return await _respond(request, E.response(E.player24()))


@router.post("/{gameinfo}/player24/update_ranking")
async def player24_update_ranking(request: Request):
    request_info = await core_process_request(request)
    game_version = request_info["game_version"]
    root = request_info["root"][0]

    refid = _text(root, "ref_id")
    card, profile = get_game_profile(refid, game_version) if refid else (None, None)
    children = []
    if profile is not None:
        course_id = _int(root, "course_id")
        sheet = _int(root, "sheet_num")
        key = f"{course_id}:{sheet}"
        old = profile["courses"].get(key, {})
        new = {
            "score": max(_int(root, "total_score"), old.get("score", 0)),
            "clear_type": max(_int(root, "clear_type"), old.get("clear_type", 0)),
            "clear_rank": max(_int(root, "clear_rank"), old.get("clear_rank", 0)),
            "pref": _int(root, "pref"),
            "lid": _text(root, "location_id", ""),
            "count": old.get("count", 0) + 1,
        }
        profile["courses"][key] = new
        save_game_profile(refid, game_version, profile)

        # Rank among every profile that played this course/sheet
        scores = []
        for c in profiles_table().all():
            p = c.get("version", {}).get(str(game_version))
            if p and key in p.get("courses", {}):
                scores.append((c.get("popn_id"), p["courses"][key]["score"]))
        scores.sort(key=lambda s: -s[1])
        rank = next((i + 1 for i, (pid, _) in enumerate(scores) if pid == card["popn_id"]), 1)
        for name in ("all_ranking", "pref_ranking", "location_ranking"):
            children.append(
                E(
                    name,
                    E.name(profile["name"] or "なし", __type="str"),
                    E.chara_num(profile["config"]["chara"], __type="s16"),
                    E.total_score(new["score"], __type="s32"),
                    E.clear_type(new["clear_type"], __type="u8"),
                    E.clear_rank(new["clear_rank"], __type="u8"),
                    E.player_count(len(scores), __type="s16"),
                    E.player_rank(rank, __type="s16"),
                )
            )
    return await _respond(request, E.response(E.player24(*children)))


@router.post("/{gameinfo}/player24/read_option")
async def player24_read_option(request: Request):
    # Jam&Fizz: request ref_id/music_num/sheet_num, response parsed by the option
    # game-data reader (same node/types as player24.read <option>).
    request_info = await core_process_request(request)
    game_version = request_info["game_version"]
    refid = _text(request_info["root"][0], "ref_id")
    card, profile = get_game_profile(refid, game_version) if refid else (None, None)
    if profile is None:
        profile = new_game_profile(game_version, "")
    return await _respond(request, E.response(E.player24(option_node(profile))))


@router.post("/{gameinfo}/player24/write_course")
async def player24_write_course(request: Request):
    # Jam&Fizz custom course result (course_name, stage1..4 music/sheet, norma, medal,
    # total_score, max_combo, last_gauge, license). Response carries no payload.
    request_info = await core_process_request(request)
    game_version = request_info["game_version"]
    root = request_info["root"][0]
    refid = _text(root, "ref_id")
    card, profile = get_game_profile(refid, game_version) if refid else (None, None)
    if profile is not None:
        course_id = str(_int(root, "course_id"))
        record = {
            "name": _text(root, "course_name", ""),
            "count": profile["custom_courses"].get(course_id, {}).get("count", 0) + 1,
            "stages": [[_int(root, f"stage{i}_music_num", -1), _int(root, f"stage{i}_sheet_num")] for i in range(1, 5)],
        }
        for k in ("norma_type", "norma_1_num", "norma_2_num", "clear_medal", "clear_norma",
                  "total_score", "max_combo", "last_gauge", "chara_num"):
            record[k] = _int(root, k)
        profile["custom_courses"][course_id] = record
        save_game_profile(refid, game_version, profile)
    return await _respond(request, E.response(E.player24()))


@router.post("/{gameinfo}/player24/tsumtsum")
async def player24_tsumtsum(request: Request):
    # Jam&Fizz: <player24 ref_id="" uid=""/> -> <status> s8 (0 = ok)
    request_info = await core_process_request(request)
    return await _respond(request, E.response(E.player24(E.status(0, __type="s8"))))


@router.post("/{gameinfo}/player24/friend")
async def player24_friend(request: Request):
    request_info = await core_process_request(request)
    # Rivals are not supported: tell the game the slot is empty.
    return await _respond(request, E.response(E.player24(E.result(2, __type="s8"))))
