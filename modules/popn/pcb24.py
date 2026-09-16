from fastapi import APIRouter, Request, Response

from core_common import core_process_request, core_prepare_response, E

router = APIRouter(prefix="/local", tags=["local"])
router.model_whitelist = ["M39"]

# pop'n music (M39, pcb24/info24/player24 protocol = pop'n 24 Usaneko era, bemaniutils
# popn/common.py). url_slash 0 forwarder looks up "<module>_<method>", so handler
# names must stay pcb24_<method>.


async def _respond(request, response):
    response_body, response_headers = await core_prepare_response(request, response)
    return Response(content=response_body, headers=response_headers)


@router.post("/{gameinfo}/pcb24/boot")
async def pcb24_boot(request: Request):
    request_info = await core_process_request(request)
    return await _respond(request, E.response(E.pcb24()))


@router.post("/{gameinfo}/pcb24/write")
async def pcb24_write(request: Request):
    # Request carries pcb_setting/name (cabinet name); nothing needs to be stored.
    request_info = await core_process_request(request)
    return await _respond(request, E.response(E.pcb24()))
