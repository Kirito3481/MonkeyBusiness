from fastapi import APIRouter, Request, Response

from core_common import core_process_request, core_prepare_response, E

router = APIRouter(prefix="/local", tags=["local"])
router.model_whitelist = ["PAN"]

# get_music_info response as read by nostalgia.dll (PAN 2025020501, receiver sub_1801D3B40):
#   permitted_list/flag@sheet_type(0..3) = s32[32] bitmask by music index   -- required
#   gamedata_flag_list/event {index s32, status s8, start_time end_time param1 param2 s64, comment str<32}
#   trend_music_list/trend_music@music_index@rank
#   brooch_list/<any>@index (1..255) marks the brooch available            -- optional
#   overwrite_music_list, quest_data_list, information_list, course_data_list -- optional, not sent
# The game loads songs and brooches from its own data_op3 files and only keeps
# entries whose bit/index is permitted here, so permitting everything needs no
# server-side copy of music_list.xml. overwrite_music_list is the only way to
# change unlock_type / release dates of the local list; if it is sent it needs
# revision@/release_code@ and every field per music_spec.

PERMIT_ALL = [-1] * 32  # 1024 bits, all set

# Trend ranking shown in the attract/select "trend" category, best rank first.
# Music indexes from data_op3/sound/music_list.xml (PAN-2025020501).
TREND_MUSIC = [
    531,  # MEGALOVANIA
    529,  # Once Upon a Time
    530,  # Bonetrousle
    566,  # Battle Against a True Hero
    567,  # Hopes and Dreams
    570,  # BIG SHOT (DELTARUNE)
    569,  # THE WORLD REVOLVING (DELTARUNE)
    568,  # Field of Hopes and Dreams (DELTARUNE)
]
BROOCH_MAX_INDEX = 255


@router.post("/{gameinfo}/op3_common/get_common_info")
async def op3_common_get_common_info(request: Request):
    request_info = await core_process_request(request)

    response = E.response(
        E.get_common_info(E.olupdate(E.delete_flag(0, __type="bool")))
    )

    response_body, response_headers = await core_prepare_response(request, response)
    return Response(content=response_body, headers=response_headers)


@router.post("/{gameinfo}/op3_common/get_music_info")
async def op3_common_get_music_info(request: Request):
    request_info = await core_process_request(request)

    response = E.response(
        E.get_music_info(
            E.permitted_list(
                *[E.flag(PERMIT_ALL, __type="s32", sheet_type=str(sheet)) for sheet in range(4)],
            ),
            E.gamedata_flag_list(),
            E.trend_music_list(
                *[
                    E.trend_music(music_index=str(mid), rank=str(rank))
                    for rank, mid in enumerate(TREND_MUSIC, start=1)
                ]
            ),
            E.brooch_list(
                *[E.brooch(index=str(b)) for b in range(1, BROOCH_MAX_INDEX + 1)],
            ),
        )
    )

    response_body, response_headers = await core_prepare_response(request, response)
    return Response(content=response_body, headers=response_headers)
