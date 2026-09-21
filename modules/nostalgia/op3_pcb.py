from fastapi import APIRouter, Request, Response

from core_common import core_process_request, core_prepare_response, E

router = APIRouter(prefix="/local", tags=["local"])
router.model_whitelist = ["PAN"]

# op3_pcb as sent by nostalgia.dll (PAN 2025020501, methods registered in sub_1801D7520):
#   report_boot              req: softwareid hardwareid locationid customercode (str)
#   report_testmode_settings req: the same four, then one node per test menu page (sub_18013C8D0):
#                                 clock_settings coin_settings ecomode_settings game_settings
#                                 network_settings sound_settings virtual_coin_settings
#                                 caribration_settings
# Both receivers (sub_1801D4350 / sub_1801D4570) return success without reading anything, so an
# empty response is all the game wants - but without one it gets a 404 and sends the report
# again every three seconds.


async def _respond(request):
    response_body, response_headers = await core_prepare_response(request, E.response(E.op3_pcb()))
    return Response(content=response_body, headers=response_headers)


@router.post("/{gameinfo}/op3_pcb/report_boot")
async def op3_pcb_report_boot(request: Request):
    await core_process_request(request)
    return await _respond(request)


@router.post("/{gameinfo}/op3_pcb/report_testmode_settings")
async def op3_pcb_report_testmode_settings(request: Request):
    await core_process_request(request)
    return await _respond(request)
