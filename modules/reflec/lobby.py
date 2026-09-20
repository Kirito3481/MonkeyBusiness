import random
import time

from fastapi import APIRouter, Request, Response

from core_common import core_process_request, core_prepare_response, E
from modules.reflec.player import _int, _int_list, _pad, _text, get_card_by_extid, play_sessions

# reflecbeat.dll: the "lobby" module is a CXrpcModule on the "lobby2" service.
router = APIRouter(prefix="/lobby2", tags=["lobby2"])
router.model_whitelist = ["MBR"]

# REFLEC BEAT VOLZZA 2 "lobby" module (bemaniutils volzzabase.py). Entries live
# in memory: eid -> entry dict. A cabinet posts its entry, polls read for other
# cabinets with the same ver, and deletes its entry when matching ends.

LOBBY_INTERVAL = 120
LOBBY_MAX_AGE = 600  # seconds without refresh before an entry is dropped

from modules.reflec.state import lobbies  # noqa: E402  (shared: this file is loaded twice, see state.py)

ENTRY_INTS = ("mid", "ng", "mopt", "pref", "stg", "pside", "eatime", "gp", "ver")


def _prune():
    now = int(time.time())
    for eid in [e for e, v in lobbies.items() if now - v["time"] > LOBBY_MAX_AGE]:
        del lobbies[eid]


def remove_lobbies_of(extid):
    # Players without an id (guests, uid 0 or -1) all look alike: never drop their entries as
    # "the same player's old entry", they age out through _prune instead.
    if extid is None or extid <= 0:
        return
    for eid in [e for e, v in lobbies.items() if v["uid"] == extid]:
        del lobbies[eid]


def _entry_node(entry):
    return E.e(
        E.eid(entry["eid"], __type="s32"),
        E.mid(entry["mid"], __type="u16"),
        E.ng(entry["ng"], __type="u8"),
        E.uid(entry["uid"], __type="s32"),
        E.uattr(entry["uattr"], __type="s32"),
        E.pn(entry["pn"], __type="str"),
        E.plyid(entry["plyid"], __type="s32"),
        E.mg(entry["mg"], __type="s16"),
        E.mopt(entry["mopt"], __type="s32"),
        E.lid(entry["lid"], __type="str"),
        E.sn(entry["sn"], __type="str"),
        E.pref(entry["pref"], __type="u8"),
        E.stg(entry["stg"], __type="s8"),
        E.pside(entry["pside"], __type="s8"),
        E.eatime(entry["eatime"], __type="s16"),
        E.ga(_pad(entry["ga"], 4), __type="u8"),
        E.gp(entry["gp"], __type="u16"),
        E.la(_pad(entry["la"], 4), __type="u8"),
        E.ver(entry["ver"], __type="u8"),
    )


async def _respond(request, response):
    response_body, response_headers = await core_prepare_response(request, response)
    return Response(content=response_body, headers=response_headers)


@router.post("/{gameinfo}/lobby/rb5_lobby_entry")
async def lobby_rb5_lobby_entry(request: Request):
    request_info = await core_process_request(request)
    game_version = request_info["game_version"]
    e = request_info["root"][0].find("e")
    _prune()

    children = [E.interval(LOBBY_INTERVAL, __type="s32"), E.interval_p(LOBBY_INTERVAL, __type="s32")]

    # The game's receiver (reflecbeat.dll sub_1009A8C0) fails the request unless the response has
    # a non-zero eid (or eid 0 plus a complete e node), so an entry is always made. The session is
    # looked up by the card too: a card registered during this credit had no id at player_start,
    # and a server restart forgets the sessions altogether.
    extid = _int(e, "uid", -1)
    card = get_card_by_extid(extid)
    prof = (card.get("version", {}).get(str(game_version)) if card else None) or {}
    session = next((s for s in play_sessions.values() if s.get("extid") == extid), None)
    if session is None and card is not None:
        session = play_sessions.get(card.get("card"))
    session = session or {}
    if e is not None:
        remove_lobbies_of(extid)
        eid = random.randint(1, 0x7FFFFFFF)
        while eid in lobbies:
            eid = random.randint(1, 0x7FFFFFFF)
        entry = {
            "eid": eid,
            "uid": extid,
            "uattr": prof.get("uattr", 0),
            "pn": prof.get("name", ""),
            "plyid": session.get("plyid", 0),
            "mg": prof.get("mg", 0),
            "lid": _text(e, "lid", "") or "",
            "sn": _text(e, "sn", "") or "",
            "ga": _int_list(e, "ga", []),
            "la": _int_list(e, "la", []),
            "time": int(time.time()),
        }
        for k in ENTRY_INTS:
            entry[k] = _int(e, k)
        lobbies[eid] = entry
        children += [E.eid(eid, __type="s32"), _entry_node(entry)]

    return await _respond(request, E.response(E.lobby(*children)))


@router.post("/{gameinfo}/lobby/rb5_lobby_read")
async def lobby_rb5_lobby_read(request: Request):
    request_info = await core_process_request(request)
    root = request_info["root"][0]
    _prune()

    ver = _int(root, "var")
    extid = _int(root, "uid", -1)
    limit = _int(root, "max", 0)

    children = [E.interval(LOBBY_INTERVAL, __type="s32"), E.interval_p(LOBBY_INTERVAL, __type="s32")]
    for entry in sorted(lobbies.values(), key=lambda v: v["time"]):
        if limit <= 0:
            break
        # Same uid = the asking player's own entry. Players without an id share uid 0/-1, so they
        # never see each other; showing a guest its own entry as an opponent would be worse.
        if entry["uid"] == extid or entry["ver"] != ver:
            continue
        children.append(_entry_node(entry))
        limit -= 1

    return await _respond(request, E.response(E.lobby(*children)))


@router.post("/{gameinfo}/lobby/rb5_lobby_delete_entry")
async def lobby_rb5_lobby_delete_entry(request: Request):
    request_info = await core_process_request(request)
    eid = _int(request_info["root"][0], "eid", -1)
    lobbies.pop(eid, None)
    return await _respond(request, E.response(E.lobby()))
