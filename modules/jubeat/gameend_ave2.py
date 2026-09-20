import time

from tinydb import where

from fastapi import APIRouter, Request, Response

from core_common import core_process_request, core_prepare_response, E
from core_database import get_db
from modules.jubeat.fcchallenge_ave2 import fc_challenge_result
from modules.jubeat.gametop_ave2 import get_profile, get_game_profile

router = APIRouter(prefix="/local", tags=["local"])
router.model_whitelist = ["L44"]

# Protocol (from jubeat.dll gameend_ave2 sender/receiver):
#   regist req: retry, pcbinfo@client_data_version,
#               data/info{time_gametop time_gameend play_time cabid payment mode shopname areaname ...}
#               data/result(@count)/tune(@id){music roomid timestamp player{score@seq@clear@combo
#                   music_rate marker theme category is_hard_mode nr_* best_score best_music_rate
#                   best_clear play_cnt clear_cnt fc_cnt ex_cnt mbar play_mbar} friend...}
#               data/player{session_id refid jid name event_flag continue_count info last item
#                   meeting fc_challenge free_first_play event_info official_news mynews jbox
#                   history eapass navi gift_list born jubility server fill_in_category lightchat ...}
#          res: data/player/end_final_session_id(s32)  (only field the game reads)
#   final  req: retry, pcbinfo, data/info{born question_list payment_list}
#               data/player{end_final_session_id refid jid name item/emblem_list jbox born question_list}
#          res: empty gameend_ave2 (game only checks that a response arrived)

INFO_COUNTERS = (
    "tune_cnt",
    "save_cnt",
    "saved_cnt",
    "fc_cnt",
    "ex_cnt",
    "clear_cnt",
    "match_cnt",
    "beat_cnt",
    "mynews_cnt",
    "mtg_entry_cnt",
    "mtg_hold_cnt",
    "mtg_result",
    "bonus_tune_points",
    "is_bonus_tune_played",
)
LAST_FIELDS = ("expert_option", "sort", "category")
SETTINGS_FIELDS = (
    "marker",
    "theme",
    "title",
    "parts",
    "rank_sort",
    "combo_disp",
    "target_type",
    "judge_disp",
    "random_option",
    "matching",
    "hard",
    "hazard",
)
ITEM_LISTS = (
    "music_list",
    "secret_list",
    "theme_list",
    "marker_list",
    "title_list",
    "parts_list",
    "emblem_list",
    "commu_list",
)
NEW_ITEM_LISTS = ("secret_list", "theme_list", "marker_list")
FILL_IN_LISTS = (
    "no_gray_flag_list",
    "all_yellow_flag_list",
    "full_combo_flag_list",
    "excellent_flag_list",
)


def _text(node, path):
    found = node.find(path) if node is not None else None
    if found is None or found.text is None:
        return None
    return found.text


def _int(node, path, default=0):
    try:
        return int(_text(node, path))
    except (TypeError, ValueError):
        return default


def _int_list(node, path):
    text = _text(node, path)
    if text is None:
        return None
    return [int(v) for v in text.split() if v.strip()]


def _copy_ints(dst, node, keys):
    for key in keys:
        if node is not None and node.find(key) is not None:
            dst[key] = _int(node, key, dst.get(key, 0))


def _copy_lists(dst, node, keys):
    for key in keys:
        values = _int_list(node, key)
        if values is not None:
            dst[key] = values


def _save_profile(refid, game_version, profile):
    db = get_db().table("jubeat_profile")
    all_profiles_for_card = db.get(where("card") == refid)
    if all_profiles_for_card is None:
        return
    all_profiles_for_card["version"][str(game_version)] = profile
    db.upsert(all_profiles_for_card, where("card") == refid)


def update_profile_from_regist(profile, info, player):
    last = profile["last"]

    if info is not None:
        last["play_time"] = _int(info, "time_gameend", last["play_time"])
        last["shopname"] = _text(info, "shopname") or last["shopname"]
        last["areaname"] = _text(info, "areaname") or last["areaname"]

    if player is None:
        return profile

    profile["event_flag"] = _int(player, "event_flag", profile["event_flag"])
    _copy_ints(profile, player.find("info"), INFO_COUNTERS)

    lastnode = player.find("last")
    _copy_ints(last, lastnode, LAST_FIELDS)
    if lastnode is not None:
        settings = lastnode.find("settings")
        _copy_ints(last, settings, SETTINGS_FIELDS)
        emblem = _int_list(settings, "emblem")
        if emblem is not None and len(emblem) == 5:
            last["emblem"] = emblem

    item = player.find("item")
    _copy_lists(profile["item"], item, ITEM_LISTS)
    if item is not None:
        _copy_lists(profile["item"]["new"], item.find("new"), NEW_ITEM_LISTS)

    fill = player.find("fill_in_category")
    if fill is not None:
        _copy_lists(profile["fill_in_category"]["normal"], fill.find("normal"), FILL_IN_LISTS)
        _copy_lists(profile["fill_in_category"]["hard"], fill.find("hard"), FILL_IN_LISTS)

    update_profile_from_final(profile, player)

    if player.find("navi/flag") is not None:
        profile["navi_flag"] = _int(player, "navi/flag", profile["navi_flag"])

    update_lightchat(profile, player.find("lightchat"))

    return profile


def update_lightchat(profile, node):
    # Save format (jubeat.dll sender sub_1007BAD0 / event sub_10079A80 / section sub_100825F0):
    #   lightchat { map_list/map@id { tune_count, last_daily_bonus_time, event_list/event@id {
    #   section_list/section@id { acquired_jwatt, is_cleared, mission_list/mission@id { is_cleared, progress } },
    #   condition_list/condition_data@id/condition@id { is_cleared, progress }, display_state } },
    #   next_map_id, next_event_id, next_section_id }
    if node is None:
        return profile

    lc = profile["lightchat"]
    for m in node.findall("map_list/map"):
        map_id = m.get("id")
        if map_id is None:
            continue
        stored_map = lc["maps"].setdefault(map_id, {"tune_count": 0, "last_daily_bonus_time": 0, "events": {}})
        stored_map["tune_count"] = _int(m, "tune_count", stored_map["tune_count"])
        stored_map["last_daily_bonus_time"] = _int(m, "last_daily_bonus_time", stored_map["last_daily_bonus_time"])

        for ev in m.findall("event_list/event"):
            event_id = ev.get("id")
            if event_id is None:
                continue
            stored_ev = stored_map["events"].setdefault(
                event_id, {"display_state": 1, "conditions": {}, "sections": {}}
            )
            if ev.find("display_state") is not None:
                stored_ev["display_state"] = _int(ev, "display_state", stored_ev["display_state"])

            for cond in ev.findall("condition_list/condition_data/condition") + ev.findall("condition_list/condition"):
                if cond.get("id") is not None:
                    stored_ev["conditions"][cond.get("id")] = {
                        "is_cleared": _int(cond, "is_cleared"),
                        "progress": _int(cond, "progress"),
                    }

            for sec in ev.findall("section_list/section"):
                section_id = sec.get("id")
                if section_id is None:
                    continue
                stored_sec = stored_ev["sections"].setdefault(
                    section_id, {"acquired_jwatt": 0, "is_cleared": 0, "missions": {}}
                )
                stored_sec["acquired_jwatt"] = _int(sec, "acquired_jwatt", stored_sec["acquired_jwatt"])
                stored_sec["is_cleared"] = _int(sec, "is_cleared", stored_sec["is_cleared"])
                for mis in sec.findall("mission_list/mission"):
                    if mis.get("id") is not None:
                        stored_sec["missions"][mis.get("id")] = {
                            "is_cleared": _int(mis, "is_cleared"),
                            "progress": _int(mis, "progress"),
                        }

    if node.find("next_map_id") is not None:
        lc["current_map_id"] = _int(node, "next_map_id", lc["current_map_id"])
    if node.find("next_event_id") is not None:
        lc["current_event_id"] = _int(node, "next_event_id", lc["current_event_id"])

    return profile


def update_profile_from_final(profile, player):
    # Shared by regist and final: emblem ownership, jbox state, birth info.
    if player is None:
        return profile

    emblem_list = _int_list(player, "item/emblem_list")
    if emblem_list is not None:
        profile["item"]["emblem_list"] = emblem_list

    jbox = player.find("jbox")
    if jbox is not None:
        profile["jbox"]["point"] = _int(jbox, "point", profile["jbox"]["point"])
        emblem_type = _int(jbox, "emblem/type", -1)
        index = _int(jbox, "emblem/index", 0)
        if emblem_type == 1:
            profile["jbox"]["normal_index"] = index
        elif emblem_type == 2:
            profile["jbox"]["premium_index"] = index

    born = player.find("born")
    if born is not None:
        profile["born"]["status"] = _int(born, "status", profile["born"]["status"])
        profile["born"]["year"] = _int(born, "year", profile["born"]["year"])

    return profile


def save_scores(jid, game_version, result, profile):
    if result is None:
        return

    db = get_db().table("jubeat_scores_best")
    now_ms = int(time.time() * 1000)

    for tune in result.findall("tune"):
        p = tune.find("player")
        score_node = p.find("score") if p is not None else None
        if score_node is None:
            continue

        music_id = _int(tune, "music", -1)
        seq = int(score_node.get("seq", "0"))
        if music_id < 0 or seq not in (0, 1, 2):
            continue  # edit charts (seq 3) are not kept in the best table

        hard = 1 if _int(p, "is_hard_mode") else 0
        timestamp = _int(tune, "timestamp", now_ms)

        # The game already merged this play into its own record and sends the
        # updated per-chart best/counters, so store those as-is.
        row = {
            "jubeat_id": int(jid),
            "game_version": game_version,
            "music_id": music_id,
            "seq": seq,
            "hard": hard,
            "score": _int(p, "best_score", _int(p, "score")),
            "music_rate": _int(p, "best_music_rate", _int(p, "music_rate")),
            "clear": _int(p, "best_clear", int(score_node.get("clear", "0"))),
            "play_cnt": _int(p, "play_cnt", 1),
            "clear_cnt": _int(p, "clear_cnt"),
            "fc_cnt": _int(p, "fc_cnt"),
            "ex_cnt": _int(p, "ex_cnt"),
            "bar": _int_list(p, "mbar") or _int_list(p, "play_mbar") or [],
            "last_score": _int(p, "score"),
            "last_combo": int(score_node.get("combo", "0")),
            "timestamp": timestamp,
        }

        db.upsert(
            row,
            (where("jubeat_id") == int(jid))
            & (where("game_version") == game_version)
            & (where("music_id") == music_id)
            & (where("seq") == seq)
            & (where("hard") == hard),
        )

        profile["last"]["music_id"] = music_id
        profile["last"]["seq_id"] = seq


def update_fc_challenge(profile, result, player):
    """Full combo challenge: today's state from the tune results, the whim state as the game sends it."""
    tunes = []
    for tune in result.findall("tune") if result is not None else []:
        score_node = tune.find("player/score")
        if score_node is not None:
            tunes.append((_int(tune, "music", -1), int(score_node.get("seq", "0")), int(score_node.get("clear", "0"))))
    whim = player.find("fc_challenge/whim") if player is not None else None
    if whim is not None:
        fc_challenge_result(profile, tunes, _int(whim, "music_id"), _int(whim, "state"))
    else:
        fc_challenge_result(profile, tunes)


@router.post("/{gameinfo}/gameend_ave2/regist")
async def gameend_ave2_regist(request: Request):
    request_info = await core_process_request(request)
    game_version = request_info["game_version"]

    data = request_info["root"][0].find("data")
    info = data.find("info") if data is not None else None
    result = data.find("result") if data is not None else None
    player = data.find("player") if data is not None else None

    refid = _text(player, "refid")
    profile = get_game_profile(refid, game_version) if refid else None

    if profile is not None:
        card = get_profile(refid)
        jid = card.get("jubeat_id", _int(player, "jid"))
        update_profile_from_regist(profile, info, player)
        save_scores(jid, game_version, result, profile)
        update_fc_challenge(profile, result, player)
        _save_profile(refid, game_version, profile)

    response = E.response(
        E.gameend_ave2(
            E.data(
                E.player(
                    E.session_id(1, __type="s32"),
                    E.end_final_session_id(1, __type="s32"),
                ),
            ),
        )
    )

    response_body, response_headers = await core_prepare_response(request, response)
    return Response(content=response_body, headers=response_headers)


@router.post("/{gameinfo}/gameend_ave2/final")
async def gameend_ave2_final(request: Request):
    request_info = await core_process_request(request)
    game_version = request_info["game_version"]

    data = request_info["root"][0].find("data")
    player = data.find("player") if data is not None else None

    refid = _text(player, "refid")
    profile = get_game_profile(refid, game_version) if refid else None

    if profile is not None:
        update_profile_from_final(profile, player)
        _save_profile(refid, game_version, profile)

    response = E.response(
        E.gameend_ave2(),
    )

    response_body, response_headers = await core_prepare_response(request, response)
    return Response(content=response_body, headers=response_headers)
