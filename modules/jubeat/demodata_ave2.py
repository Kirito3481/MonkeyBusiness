import time

from fastapi import APIRouter, Request, Response

from core_common import core_process_request, core_prepare_response, E
from modules.jubeat.musicdb_ave2 import ave2_newest_first, ave2_play_counts

router = APIRouter(prefix="/local", tags=["local"])
router.model_whitelist = ["L44"]

# get_hitchart (from jubeat.dll sub_1019BD30): the request is empty, the response is
#   data/update(str, max 19 chars)
#   data/hitchart_lic(@count) and data/hitchart_org(@count), each with up to 10
#   rankdata{music_id s32, rank s16 1..10, prev s16}
# The game keeps the two top 10 lists and fills the HIT CHART folder of the music select
# with them (licensed first, then originals, only songs the player can play). More than 10
# rankdata per list would overflow the game's buffer.
HITCHART_SIZE = 10


def hitchart(game_version):
    """Top songs by play count over every player; ranks nobody played yet go to the newest songs.

    Which songs are licensed / KONAMI originals comes from musicdb_ave2 (music_db_ave2.json);
    without that file the hit chart stays empty.
    """
    plays = ave2_play_counts(game_version)  # cached for a few minutes
    charts = {}
    for kind, original in (("licensed", False), ("original", True)):
        newest_first = ave2_newest_first(original)
        age = {music_id: index for index, music_id in enumerate(newest_first)}
        played = sorted((m for m in plays if m in age and plays[m] > 0), key=lambda m: (-plays[m], age[m]))
        charts[kind] = (played + [m for m in newest_first if m not in plays])[:HITCHART_SIZE]
    return charts


def _hitchart_node(name, music_ids):
    return E(
        name,
        *[
            E.rankdata(
                E.music_id(music_id, __type="s32"),
                E.rank(rank, __type="s16"),
                E.prev(rank, __type="s16"),  # parsed but not used by the game
            )
            for rank, music_id in enumerate(music_ids, 1)
        ],
        count=str(len(music_ids)),
    )


@router.post("/{gameinfo}/demodata_ave2/get_info")
async def demodata_ave2_get_info(request: Request):
    request_info = await core_process_request(request)

    response = E.response(
        E.demodata_ave2(
            E.data(
                E.info(
                    E.black_jacket_list([0] * 64, __type="s32"),
                ),
            ),
        )
    )

    response_body, response_headers = await core_prepare_response(request, response)
    return Response(content=response_body, headers=response_headers)


@router.post("/{gameinfo}/demodata_ave2/get_jbox_list")
async def demodata_ave2_get_jbox_list(request: Request):
    request_info = await core_process_request(request)

    response = E.response(
        E.demodata_ave2(
            E.selection_list(),
        )
    )

    response_body, response_headers = await core_prepare_response(request, response)
    return Response(content=response_body, headers=response_headers)


@router.post("/{gameinfo}/demodata_ave2/get_news")
async def demodata_ave2_get_news(request: Request):
    request_info = await core_process_request(request)

    response = E.response(
        E.demodata_ave2(
            E.data(
                E.officialnews(count=0),
            )
        ),
    )

    response_body, response_headers = await core_prepare_response(request, response)
    return Response(content=response_body, headers=response_headers)


@router.post("/{gameinfo}/demodata_ave2/get_hitchart")
async def demodata_ave2_get_hitchart(request: Request):
    request_info = await core_process_request(request)

    charts = hitchart(request_info["game_version"])
    response = E.response(
        E.demodata_ave2(
            E.data(
                E.update(time.strftime("%Y/%m/%d %H:%M"), __type="str"),
                _hitchart_node("hitchart_lic", charts["licensed"]),
                _hitchart_node("hitchart_org", charts["original"]),
            )
        ),
    )

    response_body, response_headers = await core_prepare_response(request, response)
    return Response(content=response_body, headers=response_headers)
