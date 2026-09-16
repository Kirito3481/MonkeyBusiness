import random

from fastapi import APIRouter, Request, Response

from core_common import core_process_request, core_prepare_response, E

router = APIRouter(prefix="/local2", tags=["local2"])
router.model_whitelist = ["PIX"]

# ESS eventlog (ess.dll sys_eventlog_write_receiver), same layout as IIDX/DDR/SDVX:
#   eventlog { gamesession s64, logsendflg s32, logerrlevel s32, evtidnosendflg s32 }
# gamesession echoes the request's data/gamesession; a negative value means the
# game has no session yet, so hand it a new one. logsendflg 0 stops further
# event uploads for the session.


@router.post("/{gameinfo}/eventlog/write")
async def museca_eventlog_write(request: Request):
    request_info = await core_process_request(request)

    gamesession_node = request_info["root"][0].find("data/gamesession")
    try:
        gamesession = int(gamesession_node.text)
    except (AttributeError, TypeError, ValueError):
        gamesession = -1
    if gamesession < 0:
        gamesession = random.randint(1, 1000000)

    response = E.response(
        E.eventlog(
            E.gamesession(gamesession, __type="s64"),
            E.logsendflg(0, __type="s32"),
            E.logerrlevel(0, __type="s32"),
            E.evtidnosendflg(0, __type="s32"),
        )
    )

    response_body, response_headers = await core_prepare_response(request, response)
    return Response(content=response_body, headers=response_headers)
