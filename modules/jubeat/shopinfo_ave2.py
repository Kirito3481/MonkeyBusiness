from fastapi import APIRouter, Request, Response

from core_common import core_process_request, core_prepare_response, E

router = APIRouter(prefix="/local", tags=["local"])
router.model_whitelist = ["L44"]

# 64 x s32 bitmasks indexed by music_info pos_index.
WHITE_MUSIC_LIST = [
    -2013397024, -132120623, 83885717, 545260541, -124928, -262141, -33554401, 16383,
    0, -29164993, -1006632961, -2097153, -2044768257, -27000833, -1005814401, -12289,
    980541439, -301793280, 4187133, 133988320, 1075838048, -32708, -234909665, 91291647,
    16360958, -2097152, -939524095, -2080501762, -402668669, -1053818881, -7364613, 505855231,
    -45613177, -3145729, -536873105, 1938624909, 12, -1048576, 35651583, -246271,
    260046831, -2097280, -33554433, -256, -268437745, 536870911, -1073798144, -805601281,
    -536870913, -1, -2097153, -1, -2177, 252511999, -940052256, 7,
    0, 0, 0, 0, 0, 0, 0, 0,
]

OPEN_MUSIC_LIST = WHITE_MUSIC_LIST

HOT_MUSIC_LIST = [
    0, 0, 0, 0, 0, 0, 0, 0,
    0, 0, 0, 0, 0, 0, 0, 0,
    0, 0, 0, 0, 0, 0, 0, 0,
    0, 0, 0, 0, 0, 0, 0, 0,
    0, 0, 0, 0, 0, 0, 0, 0,
    0, 0, 0, 0, 0, 0, 0, 0,
    0, -268009600, -3407937, -1, -2177, 252511999, -940052256, 7,
    0, 0, 0, 0, 0, 0, 0, 0,
]



def jubeat_ave2_global_info():
    # Shared <info> node used by shopinfo_ave2.regist and gametop_ave2.get_info.
    no_bits = [0] * 64

    return E.info(
        E.event_info(),
        E.share_music(),
        E.genre_def_music(),
        E.black_jacket_list(no_bits, __type="s32"),
        E.weekly_music(),
        E.white_music_list(WHITE_MUSIC_LIST, __type="s32"),
        E.white_marker_list([-1, 127231] + [0] * 14, __type="s32"),
        E.white_theme_list([7295] + [0] * 15, __type="s32"),
        E.add_default_music_list(no_bits, __type="s32"),
        E.open_music_list(OPEN_MUSIC_LIST, __type="s32"),
        E.hot_music_list(HOT_MUSIC_LIST, __type="s32"),
        E.expert_option(E.is_available(True, __type="bool")),
        E.konami_logo_50th(E.is_available(True, __type="bool")),
        E.all_music_matching(E.is_available(True, __type="bool")),
        E.random_option(E.is_available(True, __type="bool")),
        E.judge_disp(E.is_available(True, __type="bool")),
        E.password_match(E.is_available(True, __type="bool")),
        E.april_fools_2024(E.is_available(False, __type="bool")),
        E.update_2024091800(E.is_available(True, __type="bool")),
        E.stealth_extend(E.is_available(True, __type="bool")),
        E.lightchat(
            E.map_list(
                E.map(
                    E.event_list(
                        E.event(
                            E.event_type(1, __type="s32"),
                            E.stime(0, __type="u64"),
                            E.etime(0, __type="u64"),
                            E.is_open(True, __type="bool"),
                            E.hint("HINT", __type="str"),
                            E.unlock_text("UNLOCK TEXT", __type="str"),
                            E.condition_list(),
                            E.section_list(
                                E.section(
                                    E.tube_text("01BC00", __type="str"),
                                    E.required_jwatt(10, __type="s32"),
                                    E.reward_type(1, __type="s32"),
                                    E.reward_param(11000105, __type="s32"),
                                    E.dialogue("jubeat beyond the Ave.へようこそ！", __type="str"),
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
    )


@router.post("/{gameinfo}/shopinfo_ave2/regist")
async def shopinfo_ave2_regist(request: Request):
    request_info = await core_process_request(request)

    response = E.response(
        E.shopinfo_ave2(
            E.data(
                E.cabid(1, __type="u32"),
                E.locationid("EA000001", __type="str"),
                E.facility(
                    E.exist(1, __type="u32"),
                ),
                jubeat_ave2_global_info(),
            ),
        )
    )

    response_body, response_headers = await core_prepare_response(request, response)
    return Response(content=response_body, headers=response_headers)
