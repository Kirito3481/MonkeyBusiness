from fastapi import APIRouter, Request, Response

from core_common import core_process_request, core_prepare_response, E

# popn22.dll: lobby24 is the only module on the "lobby2" service (the rest is on "local2").
router = APIRouter(prefix="/lobby2", tags=["lobby2"])
router.model_whitelist = ["M39"]

# pop'n music lobby24 (net taisen) is stubbed out entirely; every method gets an empty root.
# Handler names must stay <module>_<method> for the url_slash 0 forwarder.


async def _respond(request, response):
    response_body, response_headers = await core_prepare_response(request, response)
    return Response(content=response_body, headers=response_headers)


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
