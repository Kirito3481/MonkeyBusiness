from fastapi import APIRouter, Request, Response

from core_common import core_process_request, core_prepare_response, E
from modules.popn.common import common_info_nodes

router = APIRouter(prefix="/local2", tags=["local2"])
router.model_whitelist = ["M39"]

# pop'n music info24 (bemaniutils popn/common.py). Handler names must stay
# <module>_<method> for the url_slash 0 forwarder. lobby24 lives in lobby24.py: the game
# sends it through the "lobby2" service, not "local2".


async def _respond(request, response):
    response_body, response_headers = await core_prepare_response(request, response)
    return Response(content=response_body, headers=response_headers)


@router.post("/{gameinfo}/info24/common")
async def info24_common(request: Request):
    request_info = await core_process_request(request)
    response = E.response(E.info24(*common_info_nodes(request_info["game_version"])))
    return await _respond(request, response)
