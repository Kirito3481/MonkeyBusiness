from tinydb import Query, where

import random
import time

from fastapi import APIRouter, Request, Response

from core_common import core_process_request, core_prepare_response, E
from core_database import get_db

router = APIRouter(prefix="/local", tags=["local"])
router.model_whitelist = ["PAN"]


# The <last> node: (name, type, value for a new player), in the order the game writes it.
# nostalgia.dll reads every one of these from regist_playdata and get_playdata alike (receiver
# sub_1801D4A50 -> PlayerSettingData reader sub_180189F10: each field is required, none is
# range-checked) and applies them as they come, so a new player has to be given the game's own
# defaults - PlayerSettingData's reset (0x18018AF40): judge_bar_pos 250, note_height 10,
# beat_guide 1, slow_fast 1, the rest 0. (Its sheet_type is -1; the 0 sent so far is kept.)
LAST_FIELDS = (
    ("music_group", "s32", 0),
    ("music_index", "s32", 0),
    ("sheet_type", "s8", 0),
    ("perform_type", "s32", 0),
    ("filter_flag", "u64", 0),
    ("brooch_index", "s32", 0),
    ("hi_speed_level", "s32", 0),
    ("beat_guide", "s8", 1),
    ("headphone_volume", "s8", 0),
    ("judge_bar_pos", "s32", 250),
    ("hands_mode", "s8", 0),
    ("near_setting", "s8", 0),
    ("judge_delay_offset", "s8", 0),
    ("key_beam_level", "s8", 0),
    ("orbit_type", "s8", 0),
    ("note_height", "s8", 10),
    ("note_width", "s8", 0),
    ("judge_width_type", "s8", 0),
    ("beat_guide_volume", "s8", 0),
    ("beat_guide_type", "s8", 0),
    ("key_volume_offset", "s8", 0),
    ("bgm_volume_offset", "s8", 0),
    ("note_disp_type", "s8", 0),
    ("slow_fast", "s8", 1),
    ("option_setting", "s32", 0),
    ("judge_effect_adjust", "s8", 0),
    ("simple_bg", "s8", 0),
    ("bingo_index", "s32", 0),
)
# sent after them by get_playdata only
LAST_CLASS_FIELDS = (
    ("class_basic", "s32", 0),
    ("class_recital", "s32", 0),
    ("grade_basic", "s32", 0),
    ("grade_recital", "s32", 0),
)


def last_node(profile, with_class):
    fields = LAST_FIELDS + (LAST_CLASS_FIELDS if with_class else ())
    return E.last(*[E(name, profile.get(name, default), __type=kind) for name, kind, default in fields])


# Play counts. The game keeps them itself: it takes play_count / today_play_count from
# get_playdata, adds one for the credit (sub_180185540 / sub_180185570) and sends the new values
# with set_total_result (request writer sub_1801856A0), so the server stores what it is told and
# only decides when "today" is over. old_play_count is the player's count in earlier versions.
def today():
    return time.strftime("%Y-%m-%d")


def play_count_nodes(card_profile, game_version):
    game_profile = card_profile["version"].get(str(game_version), {})
    played_today = game_profile.get("today_play_count", 0) if game_profile.get("last_play_date") == today() else 0
    older = sum(
        v.get("play_count", 0)
        for k, v in card_profile["version"].items()
        if k.isdigit() and int(k) < int(game_version)
    )
    return [
        E.play_count(game_profile.get("play_count", 0), __type="s32"),
        E.today_play_count(played_today, __type="s32"),
        E.old_play_count(older, __type="s32"),
        E.old_recital_count(0, __type="s32"),  # recitals of earlier versions are not recorded
    ]


def save_play_counts(game_profile, root):
    def sent(name):
        text = root.findtext(name)
        return int(text) if text is not None else None

    stored_total = game_profile.get("play_count", 0)
    stored_today = game_profile.get("today_play_count", 0)
    total, played_today = sent("play_count"), sent("today_play_count")
    # max(): a repeated or late request never counts a credit twice and never takes one away
    game_profile["play_count"] = max(stored_total, total) if total is not None else stored_total + 1
    if game_profile.get("last_play_date") != today():
        # first credit of the day, also when the login was before midnight and the game still
        # counts on from yesterday's number
        game_profile["today_play_count"] = 1
    else:
        game_profile["today_play_count"] = max(stored_today, played_today) if played_today is not None else stored_today + 1
    game_profile["last_play_date"] = today()


def get_profile(cid):
    return get_db().table("nostalgia_profile").get(where("card") == cid)


def get_game_profile(cid, game_version):
    profile = get_profile(cid)

    return profile["version"].get(str(game_version), None)


@router.post("/{gameinfo}/op3_player/regist_playdata")
async def op3_player_regist_playdata(request: Request):
    request_info = await core_process_request(request)
    game_version = request_info["game_version"]

    root = request_info["root"][0]

    dataid = root.find("dataid").text
    refid = root.find("refid").text
    name = root.find("name").text

    db = get_db().table("nostalgia_profile")
    all_profiles_for_card = db.get(Query().card == dataid)

    if all_profiles_for_card is None:
        all_profiles_for_card = {"card": dataid, "version": {}}

    if "nostalgia_id" not in all_profiles_for_card:
        nostalgia_id = random.randint(10000000, 99999999)
        all_profiles_for_card["nostalgia_id"] = nostalgia_id

    all_profiles_for_card["version"][str(game_version)] = {
        "game_version": game_version,
        "name": name,
        **{name: default for name, _, default in LAST_FIELDS + LAST_CLASS_FIELDS},
        "play_count": 0,
        "today_play_count": 0,
        "last_play_date": "",
        "money": 0,
        "pianist_power": 0,
        "fame_index": 0,
        "kingdom_id": 0,
        "quest_index": 0,
        "param1": [0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0],
        "param2": [0, 0, 0, 0, 0, 0, 0, 0],
    }

    db.upsert(all_profiles_for_card, where("card") == dataid)

    response = E.response(
        E.regist_playdata(
            E.permitted_list(
                E.flag([-1] * 32, __type="s32", sheet_type="0"),
                E.flag([-1] * 32, __type="s32", sheet_type="1"),
                E.flag([-1] * 32, __type="s32", sheet_type="2"),
                E.flag([-1] * 32, __type="s32", sheet_type="3"),
            ),
            E.valid_quest_list(E.quest(index="1")),
            E.valid_course_list(E.course(index="1")),
            E.name(name, __type="str"),
            *play_count_nodes(all_profiles_for_card, game_version),
            E.music_list(
                E.flag([-1] * 32, __type="s32", sheet_type="0"),
                E.flag([-1] * 32, __type="s32", sheet_type="1"),
                E.flag([-1] * 32, __type="s32", sheet_type="2"),
                E.flag([-1] * 32, __type="s32", sheet_type="3"),
            ),
            E.free_for_play_music_list(
                E.flag([-1] * 32, __type="s32", sheet_type="0"),
                E.flag([-1] * 32, __type="s32", sheet_type="1"),
                E.flag([-1] * 32, __type="s32", sheet_type="2"),
                E.flag([-1] * 32, __type="s32", sheet_type="3"),
            ),
            last_node(all_profiles_for_card["version"][str(game_version)], with_class=False),
            E.travel(
                E.money(0, __type="s32"),
                E.pianist_power(0, __type="s32"),
                E.fame_index(0, __type="s32"),
                E.kingdom_id(0, __type="s32"),
                E.quest_index(0, __type="s32"),
            ),
            # E.brooch_list(),
            # E.enquete_list(),
            # E.event_list(),
        )
    )

    response_body, response_headers = await core_prepare_response(request, response)
    return Response(content=response_body, headers=response_headers)


@router.post("/{gameinfo}/op3_player/get_musicdata")
async def op3_player_get_musicdata(request: Request):
    request_info = await core_process_request(request)
    game_version = request_info["game_version"]

    refid = request_info["root"][0].find("refid").text
    profile = get_game_profile(refid, game_version)
    nostalgia_id = get_profile(refid)["nostalgia_id"]

    records = (
        get_db()
        .table("nostalgia_scores_best")
        .search(where("nostalgia_id") == nostalgia_id)
    )

    response = E.response(
        E.get_musicdata(
            *[
                E.music(
                    E.recital(
                        E.score(r["score"], __type="s32"),
                        E.play_count(r["play_count"], __type="s32"),
                        E.clear_count(r["clear_count"], __type="s32"),
                        E.multi_count(r["multi_count"], __type="s32"),
                        E.clear_flag(r["clear_flag"], __type="s32"),
                        E.hands_mode(r["hands_mode"], __type="s8"),
                        E.evaluation(5, __type="u32"),
                        E.grade(r["grade"], __type="u32"),
                    ),
                    E.score(r["score"], __type="s32"),
                    E.play_count(r["play_count"], __type="s32"),
                    E.clear_count(r["clear_count"], __type="s32"),
                    E.multi_count(r["multi_count"], __type="s32"),
                    E.clear_flag(r["clear_flag"], __type="s32"),
                    E.hands_mode(r["hands_mode"], __type="s8"),
                    E.evaluation(5, __type="u32"),
                    E.grade(r["grade"], __type="u32"),
                    sheet_type=r["sheet_type"],
                    music_index=r["music_index"],
                )
                for r in records
            ],
            # E.new_music_list(
            #    *[E.music(
            #        E.unlock_time(0, __type="u64"),
            #        sheet_type="2",
            #        music_index=x
            #    )for x in range(1,300)],
            # ),
        )
    )

    response_body, response_headers = await core_prepare_response(request, response)
    return Response(content=response_body, headers=response_headers)


@router.post("/{gameinfo}/op3_player/get_playdata")
async def op3_player_get_playdata(request: Request):
    request_info = await core_process_request(request)
    game_version = request_info["game_version"]

    refid = request_info["root"][0].find("refid").text
    profile = get_game_profile(refid, game_version)

    response = E.response(
        E.get_playdata(
            E.permitted_list(
                E.flag([-1] * 32, __type="s32", sheet_type="0"),
                E.flag([-1] * 32, __type="s32", sheet_type="1"),
                E.flag([-1] * 32, __type="s32", sheet_type="2"),
                E.flag([-1] * 32, __type="s32", sheet_type="3"),
            ),
            # E.valid_quest_list(
            #    E.quest(index="1")
            # ),
            # E.valid_course_list(
            #    E.course(index="1")
            # ),
            E.name(profile["name"], __type="str"),
            *play_count_nodes(get_profile(refid), game_version),
            E.music_list(
                E.flag([-1] * 32, __type="s32", sheet_type="0"),
                E.flag([-1] * 32, __type="s32", sheet_type="1"),
                E.flag([-1] * 32, __type="s32", sheet_type="2"),
                E.flag([-1] * 32, __type="s32", sheet_type="3"),
            ),
            E.free_for_play_music_list(
                E.flag([-1] * 32, __type="s32", sheet_type="0"),
                E.flag([-1] * 32, __type="s32", sheet_type="1"),
                E.flag([-1] * 32, __type="s32", sheet_type="2"),
                E.flag([-1] * 32, __type="s32", sheet_type="3"),
            ),
            last_node(profile, with_class=True),
            E.travel(
                E.money(profile["money"], __type="s32"),
                E.pianist_power(profile["pianist_power"], __type="s32"),
                E.fame_index(profile["fame_index"], __type="s32"),
                E.kingdom_id(profile["kingdom_id"], __type="s32"),
                E.quest_index(profile["quest_index"], __type="s32"),
            ),
            E.extra_param(
                E.param(
                    E.count(len(profile["param1"]), __type="s32"),
                    E.params_array(profile["param1"], __type="s32"),
                    type="1",
                ),
                E.param(
                    E.count(len(profile["param2"]), __type="s32"),
                    E.params_array(profile["param2"], __type="s32"),
                    type="2",
                ),
                # E.param(
                #    E.count(11, __type="s32"),
                #    E.params_array([0, 1, 0, 0, 0, 0, 0, 0, 0, 0, 0], __type="s32"),
                #    type="1"
                # ),
                # E.param(
                #    E.count(8, __type="s32"),
                #    E.params_array([64, 0, 0, 0, 0, 0, 0, 0], __type="s32"),
                #    type="2"
                # ),
            ),
            # E.brooch_list(),
            # E.enquete_list(),
            # E.event_list(),
        )
    )

    response_body, response_headers = await core_prepare_response(request, response)
    return Response(content=response_body, headers=response_headers)


@router.post("/{gameinfo}/op3_player/set_stage_result")
async def op3_player_set_stage_result(request: Request):
    request_info = await core_process_request(request)
    game_version = request_info["game_version"]

    root = request_info["root"][0]

    # no refid = a guest credit (see set_total_result): nothing to save
    profile = get_profile(root.findtext("refid"))
    if profile is None or root.find("stageinfo/stage") is None:
        response = E.response(E.set_stage_result(E.player()))
        response_body, response_headers = await core_prepare_response(request, response)
        return Response(content=response_body, headers=response_headers)

    nostalgia_id = profile["nostalgia_id"]
    game_profile = profile["version"].get(str(game_version), {})

    stageinfo = root.find("stageinfo")

    stages = stageinfo.findall("stage")
    stage = stages[-1]
    common = stage.find("common")

    music_index = int(stage.get("music_index"))
    sheet_type = int(stage.get("sheet_type"))
    play_time = int(common.find("play_time").text)
    score = int(common.find("score").text)
    combo = int(common.find("combo").text)
    grade = int(common.find("grade").text)
    hands_mode = int(common.find("hands_mode").text)
    play_count = int(common.find("play_count").text)
    clear_count = int(common.find("clear_count").text)
    multi_count = int(common.find("multi_count").text)
    clear_flag = int(common.find("clear_flag").text)
    slow_count = int(common.find("slow_count").text)
    fast_count = int(common.find("fast_count").text)
    judge_count_miss = int(common.find("judge_count/miss").text)
    judge_count_good = int(common.find("judge_count/good").text)
    judge_count_just = int(common.find("judge_count/just").text)
    judge_count_super_just = int(common.find("judge_count/super_just").text)
    judge_count_near = int(common.find("judge_count/near").text)
    judge_percent_max_count_long_miss = int(
        common.find("judge_percent_max_count_long/miss").text
    )
    judge_percent_max_count_long_good = int(
        common.find("judge_percent_max_count_long/good").text
    )
    judge_percent_max_count_long_just = int(
        common.find("judge_percent_max_count_long/just").text
    )
    judge_percent_max_count_long_super_just = int(
        common.find("judge_percent_max_count_long/super_just").text
    )
    judge_percent_max_count_long_near = int(
        common.find("judge_percent_max_count_long/near").text
    )
    judge_percent_max_count_trill_miss = int(
        common.find("judge_percent_max_count_trill/miss").text
    )
    judge_percent_max_count_trill_good = int(
        common.find("judge_percent_max_count_trill/good").text
    )
    judge_percent_max_count_trill_just = int(
        common.find("judge_percent_max_count_trill/just").text
    )
    judge_percent_max_count_trill_super_just = int(
        common.find("judge_percent_max_count_trill/super_just").text
    )
    judge_percent_max_count_trill_near = int(
        common.find("judge_percent_max_count_trill/near").text
    )
    note_num_normal = int(common.find("note_num/normal").text)
    note_num_long = int(common.find("note_num/long").text)
    note_num_glissando = int(common.find("note_num/glissando").text)
    note_num_trill = int(common.find("note_num/trill").text)
    note_success_rate_normal = int(common.find("note_success_rate/normal").text)
    note_success_rate_long = int(common.find("note_success_rate/long").text)
    note_success_rate_glissando = int(common.find("note_success_rate/glissando").text)
    note_success_rate_trill = int(common.find("note_success_rate/trill").text)
    best_score = int(common.find("best_score").text)

    db = get_db()
    db.table("nostalgia_scores").insert(
        {
            "timestamp": play_time,
            "game_version": game_version,
            "nostalgia_id": nostalgia_id,
            "music_index": music_index,
            "sheet_type": sheet_type,
            "score": score,
            "combo": combo,
            "grade": grade,
            "hands_mode": hands_mode,
            "play_count": play_count,
            "clear_count": clear_count,
            "multi_count": multi_count,
            "clear_flag": clear_flag,
            "slow_count": slow_count,
            "fast_count": fast_count,
            "judge_count_miss": judge_count_miss,
            "judge_count_good": judge_count_good,
            "judge_count_just": judge_count_just,
            "judge_count_super_just": judge_count_super_just,
            "judge_count_near": judge_count_near,
            "judge_percent_max_count_long_miss": judge_percent_max_count_long_miss,
            "judge_percent_max_count_long_good": judge_percent_max_count_long_good,
            "judge_percent_max_count_long_just": judge_percent_max_count_long_just,
            "judge_percent_max_count_long_super_just": judge_percent_max_count_long_super_just,
            "judge_percent_max_count_long_near": judge_percent_max_count_long_near,
            "judge_percent_max_count_trill_miss": judge_percent_max_count_trill_miss,
            "judge_percent_max_count_trill_good": judge_percent_max_count_trill_good,
            "judge_percent_max_count_trill_just": judge_percent_max_count_trill_just,
            "judge_percent_max_count_trill_super_just": judge_percent_max_count_trill_super_just,
            "judge_percent_max_count_trill_near": judge_percent_max_count_trill_near,
            "note_num_normal": note_num_normal,
            "note_num_long": note_num_long,
            "note_num_glissando": note_num_glissando,
            "note_num_trill": note_num_trill,
            "note_success_rate_normal": note_success_rate_normal,
            "note_success_rate_long": note_success_rate_long,
            "note_success_rate_glissando": note_success_rate_glissando,
            "note_success_rate_trill": note_success_rate_trill,
            "best_score": best_score,
        },
    )

    best = db.table("nostalgia_scores_best").get(
        (where("nostalgia_id") == nostalgia_id)
        & (where("music_index") == music_index)
        & (where("sheet_type") == sheet_type)
    )
    best = {} if best is None else best

    best_score_data = {
        "game_version": game_version,
        "nostalgia_id": nostalgia_id,
        "music_index": music_index,
        "sheet_type": sheet_type,
        "score": max(score, best.get("score", score)),
        "play_count": play_count,
        "clear_count": clear_count,
        "multi_count": multi_count,
        "clear_flag": max(clear_flag, best.get("clear_flag", clear_flag)),
        "hands_mode": max(hands_mode, best.get("hands_mode", hands_mode)),
        "grade": max(grade, best.get("grade", grade)),
    }

    db.table("nostalgia_scores_best").upsert(
        best_score_data,
        (where("nostalgia_id") == nostalgia_id)
        & (where("music_index") == music_index)
        & (where("sheet_type") == sheet_type),
    )

    response = E.response(E.set_stage_result(E.player()))

    response_body, response_headers = await core_prepare_response(request, response)
    return Response(content=response_body, headers=response_headers)


@router.post("/{gameinfo}/op3_player/set_total_result")
async def op3_player_set_total_result(request: Request):
    request_info = await core_process_request(request)
    game_version = request_info["game_version"]

    root = request_info["root"][0]

    # A guest credit sends set_total_result too: nostalgia.dll (request builder sub_1801D56D0)
    # only adds refid/cardno/ecflag when a card is logged in. There is nothing to save then, and
    # without an answer the game repeats the request five times.
    profile = get_profile(root.findtext("refid"))
    if profile is None:
        response = E.response(E.set_total_result(E.player()))
        response_body, response_headers = await core_prepare_response(request, response)
        return Response(content=response_body, headers=response_headers)

    refid = profile["card"]
    game_profile = profile["version"].get(str(game_version), {})

    def save_int(key, path):
        # a field the game left out keeps its stored value
        text = root.findtext(path)
        if text is not None:
            game_profile[key] = int(text)

    for k in [
        "music_group",
        "music_index",
        "sheet_type",
        "perform_type",
        "filter_flag",
        "brooch_index",
        "hi_speed_level",
        "beat_guide",
        "headphone_volume",
        "judge_bar_pos",
        "hands_mode",
        "near_setting",
        "judge_delay_offset",
        "key_beam_level",
        "orbit_type",
        "note_height",
        "note_width",
        "judge_width_type",
        "beat_guide_volume",
        "beat_guide_type",
        "key_volume_offset",
        "bgm_volume_offset",
        "note_disp_type",
        "slow_fast",
        "option_setting",
        "judge_effect_adjust",
        "simple_bg",
        "bingo_index",
        "class_basic",
        "class_recital",
        "grade_basic",
        "grade_recital",
    ]:
        save_int(k, f"last/{k}")

    for k in [
        "money",
        "pianist_power",
        "fame_index",
        "kingdom_id",
        "quest_index",
    ]:
        save_int(k, f"travel/{k}")

    for param in root.findall("extra_param/param"):
        values = param.findtext("params_array")
        if values is not None:
            game_profile[f"param{param.get('type')}"] = [int(x) for x in values.split()]

    save_play_counts(game_profile, root)

    profile["version"][str(game_version)] = game_profile

    get_db().table("nostalgia_profile").upsert(profile, where("card") == refid)

    response = E.response(E.set_total_result(E.player()))

    response_body, response_headers = await core_prepare_response(request, response)
    return Response(content=response_body, headers=response_headers)
