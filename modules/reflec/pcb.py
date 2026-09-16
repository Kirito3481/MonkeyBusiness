import config

from fastapi import APIRouter, Request, Response

from core_common import core_process_request, core_prepare_response, E

router = APIRouter(prefix="/local", tags=["local"])
router.model_whitelist = ["MBR"]

# REFLEC BEAT VOLZZA 2 "pcb" and "shop" modules (bemaniutils volzzabase.py).
# Handler names must stay <module>_<method> for the url_slash 0 forwarder.


async def _respond(request, response):
    response_body, response_headers = await core_prepare_response(request, response)
    return Response(content=response_body, headers=response_headers)


@router.post("/{gameinfo}/pcb/rb5_pcb_boot")
async def pcb_rb5_pcb_boot(request: Request):
    request_info = await core_process_request(request)

    # sinfo: shop name, close-time enable/hour/minute, shop flag
    response = E.response(
        E.pcb(
            E.sinfo(
                E.nm(config.arcade, __type="str"),
                E.cl_enbl(False, __type="bool"),
                E.cl_h(0, __type="u8"),
                E.cl_m(0, __type="u8"),
                E.shop_flag(True, __type="bool"),
            ),
        )
    )
    return await _respond(request, response)


@router.post("/{gameinfo}/pcb/rb5_pcb_error")
async def pcb_rb5_pcb_error(request: Request):
    request_info = await core_process_request(request)
    return await _respond(request, E.response(E.pcb()))


@router.post("/{gameinfo}/pcb/rb5_pcb_update")
async def pcb_rb5_pcb_update(request: Request):
    request_info = await core_process_request(request)
    return await _respond(request, E.response(E.pcb()))


@router.post("/{gameinfo}/shop/rb5_shop_write_setting")
async def shop_rb5_shop_write_setting(request: Request):
    request_info = await core_process_request(request)
    return await _respond(request, E.response(E.shop()))


@router.post("/{gameinfo}/shop/rb5_shop_write_info")
async def shop_rb5_shop_write_info(request: Request):
    request_info = await core_process_request(request)
    return await _respond(request, E.response(E.shop()))
