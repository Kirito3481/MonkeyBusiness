import itertools

from fastapi import APIRouter, Request, Response

from core_common import core_process_request, core_prepare_response, E

# popn22.dll: lobby24 is the only module on the "lobby2" service (the rest is on "local2").
router = APIRouter(prefix="/lobby2", tags=["lobby2"])
router.model_whitelist = ["M39"]

# pop'n music lobby24 (net taisen) is a stub: nobody is ever waiting, so the game fills the match
# with CPU players. What popn22.dll reads from the responses (receivers in the CXrpcLobbyModule
# method vtables around 0x1020E79C):
#   getList  res: list*n (up to 30, each matching_num/staff/item_type/... ), none is fine
#   entry    res: no(u32) is REQUIRED - sub_10021A00 throws PropertyNotFoundException without it
#            and nothing catches that, the game crashes on an empty <lobby24/>
#   update   req: room_no matched_cnt location_id gpm_id staff   res: nothing is read
#   delete   res: nothing is read
# Handler names must stay <module>_<method>, lower case, for the url_slash 0 forwarder.

_entry_no = itertools.count(1)


async def _respond(request, response):
    response_body, response_headers = await core_prepare_response(request, response)
    return Response(content=response_body, headers=response_headers)


@router.post("/{gameinfo}/lobby24/{method}")
async def lobby24_any(method: str, request: Request):
    request_info = await core_process_request(request)
    if method == "entry":
        return await _respond(request, E.response(E.lobby24(E.no(next(_entry_no) & 0xFFFFFFFF or 1, __type="u32"))))
    return await _respond(request, E.response(E.lobby24()))


async def lobby24_getlist(request: Request):
    return await lobby24_any("getList", request)


async def lobby24_entry(request: Request):
    return await lobby24_any("entry", request)


async def lobby24_update(request: Request):
    return await lobby24_any("update", request)


async def lobby24_delete(request: Request):
    return await lobby24_any("delete", request)


async def lobby24_read(request: Request):
    return await lobby24_any("read", request)
