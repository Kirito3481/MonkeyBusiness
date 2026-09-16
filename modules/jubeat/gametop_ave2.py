import random

from tinydb import where

from fastapi import APIRouter, Request, Response

from core_common import core_process_request, core_prepare_response, E
from core_database import get_db
from modules.jubeat.shopinfo_ave2 import jubeat_ave2_global_info

router = APIRouter(prefix="/local", tags=["local"])
router.model_whitelist = ["L44"]


def get_profile(cid):
    return get_db().table("jubeat_profile").get(where("card") == cid)


def get_game_profile(cid, game_version):
    profile = get_profile(cid)
    if profile is None:
        return None

    return profile["version"].get(str(game_version), None)


def new_game_profile(game_version, name):
    return {
        "game_version": game_version,
        "name": name,
        "event_flag": 0,
        "tune_cnt": 3,
        "save_cnt": 0,
        "saved_cnt": 0,
        "fc_cnt": 0,
        "ex_cnt": 0,
        "clear_cnt": 0,
        "match_cnt": 0,
        "beat_cnt": 0,
        "mynews_cnt": 0,
        "mtg_entry_cnt": 0,
        "mtg_hold_cnt": 0,
        "mtg_result": 0,
        "bonus_tune_points": 0,
        "is_bonus_tune_played": 0,
        "last": {
            "play_time": 0,
            "shopname": "",
            "areaname": "",
            "music_id": 0,
            "seq_id": 0,
            "sort": 0,
            "category": 0,
            "expert_option": 0,
            "marker": 0,
            "theme": 0,
            "title": 0,
            "parts": 0,
            "rank_sort": 0,
            "combo_disp": 0,
            "target_type": 0,
            "judge_disp": 0,
            "random_option": 0,
            "emblem": [0, 0, 0, 0, 0],
            "matching": 1,
            "hard": 0,
            "hazard": 0,
        },
    }


def format_profile(jid, profile):
    last = profile["last"]

    return E.response(
        E.gametop_ave2(
            E.data(
                jubeat_ave2_global_info(),
                E.player(
                    E.jid(jid, __type="s32"),
                    E.session_id(1, __type="s32"),
                    E.name(profile["name"], __type="str"),
                    E.event_flag(profile["event_flag"], __type="u64"),
                    E.info(
                        E.inherit(0, __type="bool"),
                        E.tune_cnt(profile["tune_cnt"], __type="s32"),
                        E.save_cnt(profile["save_cnt"], __type="s32"),
                        E.saved_cnt(profile["saved_cnt"], __type="s32"),
                        E.fc_cnt(profile["fc_cnt"], __type="s32"),
                        E.ex_cnt(profile["ex_cnt"], __type="s32"),
                        E.clear_cnt(profile["clear_cnt"], __type="s32"),
                        E.match_cnt(profile["match_cnt"], __type="s32"),
                        E.beat_cnt(profile["beat_cnt"], __type="s32"),
                        E.mynews_cnt(profile["mynews_cnt"], __type="s32"),
                        E.mtg_entry_cnt(profile["mtg_entry_cnt"], __type="s32"),
                        E.mtg_hold_cnt(profile["mtg_hold_cnt"], __type="s32"),
                        E.mtg_result(profile["mtg_result"], __type="u8"),
                        E.bonus_tune_points(profile["bonus_tune_points"], __type="s32"),
                        E.is_bonus_tune_played(profile["is_bonus_tune_played"], __type="bool"),
                    ),
                    E.last(
                        E.play_time(last["play_time"], __type="s64"),
                        E.shopname(last["shopname"], __type="str"),
                        E.areaname(last["areaname"], __type="str"),
                        E.music_id(last["music_id"], __type="s32"),
                        E.seq_id(last["seq_id"], __type="s8"),
                        E.sort(last["sort"], __type="s8"),
                        E.category(last["category"], __type="s8"),
                        E.expert_option(last["expert_option"], __type="s8"),
                        E.settings(
                            E.marker(last["marker"], __type="s8"),
                            E.theme(last["theme"], __type="s8"),
                            E.title(last["title"], __type="s16"),
                            E.parts(last["parts"], __type="s16"),
                            E.rank_sort(last["rank_sort"], __type="s8"),
                            E.combo_disp(last["combo_disp"], __type="s8"),
                            E.target_type(last["target_type"], __type="s32"),
                            E.judge_disp(last["judge_disp"], __type="s8"),
                            E.random_option(last["random_option"], __type="s8"),
                            E.emblem(last["emblem"], __type="s16"),
                            E.matching(last["matching"], __type="s8"),
                            E.hard(last["hard"], __type="s8"),
                            E.hazard(last["hazard"], __type="s8"),
                        ),
                    ),
                    E.item(
                        E.music_list([0] * 64, __type="s32"),
                        E.secret_list([0] * 64, __type="s32"),
                        E.theme_list([-1] * 16, __type="s32"),
                        E.marker_list([-1] * 16, __type="s32"),
                        E.title_list([-1] * 160, __type="s32"),
                        E.parts_list([-1] * 160, __type="s32"),
                        E.emblem_list([-1] * 96, __type="s32"),
                        E.commu_list([-1] * 16, __type="s32"),
                        E.new(
                            E.secret_list([0] * 64, __type="s32"),
                            E.theme_list([0] * 16, __type="s32"),
                            E.marker_list([0] * 16, __type="s32"),
                        ),
                    ),
                    E.fc_challenge(
                        E.today(
                            E.music_id(0, __type="s32"),
                            E.state(0, __type="u8"),
                        ),
                        E.whim(
                            E.music_id(0, __type="s32"),
                            E.state(0, __type="u8"),
                        ),
                    ),
                    E.free_first_play(
                        E.is_available(0, __type="bool"),
                    ),
                    E.event_info(),
                    E.jbox(
                        E.point(0, __type="s32"),
                        E.emblem(
                            E.normal(E.index(0, __type="s16")),
                            E.premium(E.index(0, __type="s16")),
                        ),
                    ),
                    E.new_music(),
                    E.navi(
                        E.flag(0, __type="u64"),
                    ),
                    E.gift_list(),
                    E.born(
                        E.status(0, __type="s8"),
                        E.year(0, __type="s16"),
                    ),
                    E.question_list(),
                    E.server(),
                    E.fill_in_category(
                        E.normal(
                            E.no_gray_flag_list([0] * 16, __type="s32"),
                            E.all_yellow_flag_list([0] * 16, __type="s32"),
                            E.full_combo_flag_list([0] * 16, __type="s32"),
                            E.excellent_flag_list([0] * 16, __type="s32"),
                        ),
                        E.hard(
                            E.no_gray_flag_list([0] * 16, __type="s32"),
                            E.all_yellow_flag_list([0] * 16, __type="s32"),
                            E.full_combo_flag_list([0] * 16, __type="s32"),
                            E.excellent_flag_list([0] * 16, __type="s32"),
                        ),
                    ),
                    E.lightchat(
                        E.current_map_id(0, __type="s32"),
                        E.current_event_id(0, __type="s32"),
                        E.map_list(
                            E.map(
                                E.tune_count(0, __type="s32"),
                                E.last_daily_bonus_time(0, __type="u64"),
                                E.event_list(
                                    E.event(
                                        E.display_state(1, __type="s32"),
                                        E.condition_list(),
                                        E.section_list(
                                            E.section(
                                                E.acquired_jwatt(0, __type="s32"),
                                                E.is_cleared(False, __type="bool"),
                                                E.mission_list(),
                                                id="1",
                                            ),
                                        ),
                                        id="1",
                                    ),
                                ),
                                id="21",
                            ),
                        ),
                    ),
                ),
            ),
        )
    )


@router.post("/{gameinfo}/gametop_ave2/get_info")
async def gametop_ave2_get_info(request: Request):
    request_info = await core_process_request(request)

    response = E.response(
        E.gametop_ave2(
            E.data(
                jubeat_ave2_global_info(),
            ),
        )
    )

    response_body, response_headers = await core_prepare_response(request, response)
    return Response(content=response_body, headers=response_headers)


@router.post("/{gameinfo}/gametop_ave2/regist")
async def gametop_ave2_regist(request: Request):
    request_info = await core_process_request(request)
    game_version = request_info["game_version"]

    player = request_info["root"][0].find("data/player")
    refid = player.find("refid").text
    name_node = player.find("name")
    name = name_node.text if name_node is not None and name_node.text else "なし"

    db = get_db().table("jubeat_profile")
    all_profiles_for_card = db.get(where("card") == refid)

    if all_profiles_for_card is None:
        all_profiles_for_card = {"card": refid, "version": {}}

    if "jubeat_id" not in all_profiles_for_card:
        all_profiles_for_card["jubeat_id"] = random.randint(10000000, 99999999)

    all_profiles_for_card["version"][str(game_version)] = new_game_profile(game_version, name)

    db.upsert(all_profiles_for_card, where("card") == refid)

    response = format_profile(
        all_profiles_for_card["jubeat_id"],
        all_profiles_for_card["version"][str(game_version)],
    )

    response_body, response_headers = await core_prepare_response(request, response)
    return Response(content=response_body, headers=response_headers)


@router.post("/{gameinfo}/gametop_ave2/get_pdata")
async def gametop_ave2_get_pdata(request: Request):
    request_info = await core_process_request(request)
    game_version = request_info["game_version"]

    refid = request_info["root"][0].find("data/player/refid").text

    profile = get_game_profile(refid, game_version)

    if profile is None:
        # 109 = NO_PROFILE, game falls back to gametop_ave2.regist
        response = E.response(E.gametop_ave2(status=109))
    else:
        response = format_profile(get_profile(refid)["jubeat_id"], profile)

    response_body, response_headers = await core_prepare_response(request, response)
    return Response(content=response_body, headers=response_headers)


@router.post("/{gameinfo}/gametop_ave2/get_mdata")
async def gametop_ave2_get_mdata(request: Request):
    request_info = await core_process_request(request)

    jid = int(request_info["root"][0].find("data/player/jid").text)

    # TODO: fill mdata_list from saved scores once gameend_ave2 is implemented
    response = E.response(
        E.gametop_ave2(
            E.data(
                E.player(
                    E.jid(jid, __type="s32"),
                    E.mdata_list(),
                ),
            ),
        )
    )

    response_body, response_headers = await core_prepare_response(request, response)
    return Response(content=response_body, headers=response_headers)


@router.post("/{gameinfo}/gametop_ave2/get_meeting")
async def gametop_ave2_get_meeting(request: Request):
    request_info = await core_process_request(request)

    response = E.response(
        E.gametop_ave2(
            E.data(
                E.meeting(
                    E.single(count=0),
                ),
                E.reward(
                    E.total(0, __type="s32"),
                    E.point(0, __type="s32"),
                ),
            ),
        )
    )

    response_body, response_headers = await core_prepare_response(request, response)
    return Response(content=response_body, headers=response_headers)
