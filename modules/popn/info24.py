from fastapi import APIRouter, Request, Response

from core_common import core_process_request, core_prepare_response, E
from modules.popn.common import common_info_nodes

router = APIRouter(prefix="/local2", tags=["local2"])
router.model_whitelist = ["M39"]

# pop'n music info24 / lobby24 (bemaniutils popn/common.py). Handler names must
# stay <module>_<method> for the url_slash 0 forwarder.


async def _respond(request, response):
    response_body, response_headers = await core_prepare_response(request, response)
    return Response(content=response_body, headers=response_headers)


@router.post("/{gameinfo}/info24/common")
async def info24_common(request: Request):
    request_info = await core_process_request(request)
    response = E.response(E.info24(*common_info_nodes(request_info["game_version"])))
    return await _respond(request, response)


# lobby24 is stubbed out entirely (no net taisen); every method gets an empty root.
@router.post("/{gameinfo}/lobby24/{method}")
async def lobby24_any(method: str, request: Request):
    request_info = await core_process_request(request)
    return await _respond(request, E.response(E.lobby24()))


async def lobby24_entry(request: Request):
    return await lobby24_any("entry", request)


async def lobby24_update(request: Request):
    return await lobby24_any("update", request)


async def lobby24_delete(request: Request):
    return await lobby24_any("delete", request)


async def lobby24_read(request: Request):
    return await lobby24_any("read", request)
