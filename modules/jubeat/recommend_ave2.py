from fastapi import APIRouter, Request, Response

from core_common import core_process_request, core_prepare_response, E

router = APIRouter(prefix="/local", tags=["local"])
router.model_whitelist = ["L44"]


@router.post("/{gameinfo}/recommend_ave2/get_recommend")
async def recommend_ave2_get_recommend(request: Request):
    request_info = await core_process_request(request)

    # TODO: return up to 12 <music order="n"><music_id s32/><seq s8/></music> entries
    response = E.response(
        E.recommend_ave2(
            E.data(
                E.player(
                    E.music_list(),
                ),
            ),
        )
    )

    response_body, response_headers = await core_prepare_response(request, response)
    return Response(content=response_body, headers=response_headers)
