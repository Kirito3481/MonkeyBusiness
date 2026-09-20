import time

from fastapi import APIRouter, Request, Response

from core_common import core_process_request, core_prepare_response, E

router = APIRouter(prefix="/local", tags=["local"])
router.model_whitelist = ["L44"]

# logger_ave2.report (from jubeat.dll, sender sub_10171410 / receiver sub_101714A0):
#   req: retry(s32) data/code(str) data/information(str)   the game reporting an error or an odd state
#   res: nothing is read, any well formed response ends the request; without a handler the game
#        gets a 404 and retries the report.
# The reports are printed so they show up next to the request log.


@router.post("/{gameinfo}/logger_ave2/report")
async def logger_ave2_report(request: Request):
    request_info = await core_process_request(request)

    root = request_info["root"]
    data = root[0].find("data")
    code = (data.findtext("code") if data is not None else "") or ""
    information = (data.findtext("information") if data is not None else "") or ""
    print(f"{time.strftime('%H:%M:%S')} jubeat logger_ave2.report from {root.attrib.get('srcid', '?')}: code={code!r} information={information!r}")

    response = E.response(E.logger_ave2())

    response_body, response_headers = await core_prepare_response(request, response)
    return Response(content=response_body, headers=response_headers)
