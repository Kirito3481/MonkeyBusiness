from fastapi import APIRouter, Request, Response

from core_common import core_process_request, core_prepare_response, E

router = APIRouter(prefix="/local", tags=["local"])
router.model_whitelist = ["L44"]


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
