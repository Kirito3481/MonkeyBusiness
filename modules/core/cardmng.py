import random
import re

from fastapi import APIRouter, Request, Response
from tinydb import Query, where

from core_common import core_process_request, core_prepare_response, E
from core_database import get_db

router = APIRouter(prefix="/core", tags=["cardmng"])

# Card registration (PIN) is global, like real e-amusement: one "card" table
# shared by every game. Per-game profile tables only say whether the card has
# played that game. inquire status 112 (not registered) is returned only for a
# card no game has ever registered; a card known from another game gets status
# 0 with binded=0 so the game runs its own first-play flow instead of asking
# the player to register the card again.

PROFILE_TABLES = {
    "LDJ": "iidx_profile",
    "MDX": "ddr_profile",
    "KFC": "sdvx_profile",
    "M32": "gitadora_profile",
    "PAN": "nostalgia_profile",
    "REC": "dancerush_profile",
    "JDZ": "iidx_profile",
    "KDZ": "iidx_profile",
    "L44": "jubeat_profile",
    "PIX": "museca_profile",
    "MBR": "reflec_profile",
    "M39": "popn_profile",
}

# Player id key each game module stores in its profile record. Handlers such as
# IIDX music.getrank index it on every record, so a record must never exist
# without it.
ID_KEYS = {
    "iidx_profile": "iidx_id",
    "ddr_profile": "ddr_id",
    "sdvx_profile": "sdvx_id",
    "gitadora_profile": "gitadora_id",
    "nostalgia_profile": "nostalgia_id",
    "dancerush_profile": "drs_id",
    "jubeat_profile": "jubeat_id",
    "museca_profile": "museca_id",
    "reflec_profile": "reflec_id",
    "popn_profile": "popn_id",
}

STATUS_OK = 0
STATUS_NOT_ALLOWED = 110
STATUS_NOT_REGISTERED = 112
STATUS_INVALID_PIN = 116

# A card number is the chip's 8 byte id as 16 hex digits: "E004..." for the old e-AMUSEMENT PASS
# (ISO 15693, the E0 04 of an NXP ICODE chip) and "0..." for FeliCa (Amusement IC and phones) -
# the same two kinds the card number cipher on the back of the card knows. Anything else did not
# come from a card reader: inquire and getrefid answer it with "not allowed" and store nothing.
REJECT_INVALID_CARDS = True
# What a rejected card is answered with. avs2-ea3 turns every non-zero status into the same xrpc
# error (-17) and hands the number on; Nostalgia (PAN 2025020501) shows its general "connection is
# not good, try again" message for 110 and returns to the card screen. 112 must not be used: it
# sends the game on to register the card.
INVALID_CARD_STATUS = STATUS_NOT_ALLOWED
VALID_CARD = re.compile(r"(?:E004[0-9A-F]{12}|0[0-9A-F]{15})", re.IGNORECASE)


def is_valid_card(cid):
    if not REJECT_INVALID_CARDS:
        return True
    return bool(cid) and VALID_CARD.fullmatch(cid) is not None and cid.strip("0") != ""


def get_target_table(game_id):
    return PROFILE_TABLES[game_id]


def cards_table():
    return get_db().table("card")


def get_pin(cid):
    # Global registration first; fall back to a PIN stored by older server code in
    # any per-game table and migrate it into the global table.
    card = cards_table().get(where("card") == cid)
    if card is not None and "pin" in card:
        return card["pin"]

    for table in set(PROFILE_TABLES.values()):
        profile = get_db().table(table).get(where("card") == cid)
        if profile is not None and profile.get("pin") is not None:
            register_card(cid, profile["pin"])
            return profile["pin"]

    return None


def register_card(cid, pin):
    cards_table().upsert({"card": cid, "pin": pin}, where("card") == cid)


def get_profile(game_id, cid):
    target_table = get_target_table(game_id)
    profile = get_db().table(target_table).get(where("card") == cid)

    if profile is None:
        profile = {
            "card": cid,
            "version": {},
        }

    return profile


def get_game_profile(game_id, game_version, cid):
    profile = get_profile(game_id, cid)

    if str(game_version) not in profile["version"]:
        profile["version"][str(game_version)] = {}

    return profile["version"][str(game_version)]


def create_profile(game_id, game_version, cid, pin):
    # Make sure the per-game record exists so game modules find the card. The PIN
    # is also kept here so older module code that reads profile["pin"] keeps working.
    target_table = get_target_table(game_id)
    profile = get_profile(game_id, cid)

    profile["pin"] = pin
    ensure_player_id(target_table, profile)

    get_db().table(target_table).upsert(profile, where("card") == cid)


def ensure_player_id(target_table, profile):
    id_key = ID_KEYS.get(target_table)
    if id_key and id_key not in profile:
        profile[id_key] = random.randint(10000000, 99999999)
        return True
    return False


def has_played(game_id, cid):
    profile = get_profile(game_id, cid)
    return any(bool(v) for v in profile["version"].values())


@router.post("/{gameinfo}/cardmng/authpass")
async def cardmng_authpass(request: Request):
    request_info = await core_process_request(request)

    cid = request_info["root"][0].attrib["refid"]
    passwd = request_info["root"][0].attrib["pass"]

    pin = get_pin(cid)
    status = STATUS_OK if pin is not None and passwd == pin else STATUS_INVALID_PIN

    response = E.response(E.authpass(status=status))

    response_body, response_headers = await core_prepare_response(request, response)
    return Response(content=response_body, headers=response_headers)


@router.post("/{gameinfo}/cardmng/bindmodel")
async def cardmng_bindmodel(request: Request):
    request_info = await core_process_request(request)

    response = E.response(E.bindmodel(dataid=1))

    response_body, response_headers = await core_prepare_response(request, response)
    return Response(content=response_body, headers=response_headers)


@router.post("/{gameinfo}/cardmng/getrefid")
async def cardmng_getrefid(request: Request):
    request_info = await core_process_request(request)

    cid = request_info["root"][0].attrib.get("cardid", "")
    passwd = request_info["root"][0].attrib["passwd"]

    if not is_valid_card(cid):
        print(f"cardmng.getrefid: rejected card number {cid!r}")
        response = E.response(E.getrefid(status=INVALID_CARD_STATUS))
        response_body, response_headers = await core_prepare_response(request, response)
        return Response(content=response_body, headers=response_headers)

    register_card(cid, passwd)
    if request_info["model"] in PROFILE_TABLES:
        create_profile(request_info["model"], request_info["game_version"], cid, passwd)

    response = E.response(
        E.getrefid(
            dataid=cid,
            refid=cid,
        )
    )

    response_body, response_headers = await core_prepare_response(request, response)
    return Response(content=response_body, headers=response_headers)


@router.post("/{gameinfo}/cardmng/inquire")
async def cardmng_inquire(request: Request):
    request_info = await core_process_request(request)

    cid = request_info["root"][0].attrib.get("cardid", "")
    model = request_info["model"]

    if not is_valid_card(cid):
        # "not allowed", what a real network answers for a card it will not accept; unlike 112
        # it does not send the game on to register the card
        print(f"cardmng.inquire: rejected card number {cid!r}")
        response = E.response(E.inquire(status=INVALID_CARD_STATUS))
        response_body, response_headers = await core_prepare_response(request, response)
        return Response(content=response_body, headers=response_headers)

    pin = get_pin(cid)
    if pin is None:
        # Never registered anywhere: game asks the player to register the card.
        binded = 0
        newflag = 1
        status = STATUS_NOT_REGISTERED
    else:
        status = STATUS_OK
        if model in PROFILE_TABLES:
            # Registered by another game: give this game its own record now so its
            # first-play flow finds the card without a second registration.
            record = get_db().table(get_target_table(model)).get(where("card") == cid)
            if record is None:
                create_profile(model, request_info["game_version"], cid, pin)
            elif ensure_player_id(get_target_table(model), record):
                # Record created before ids were assigned here: repair it.
                get_db().table(get_target_table(model)).upsert(record, where("card") == cid)
            if model == "LDJ":
                # IIDX migrates old profiles itself (pc.oldget -> getname -> takeover),
                # so binded must reflect this version only.
                played = bool(get_profile(model, cid)["version"].get(str(request_info["game_version"])))
            else:
                played = has_played(model, cid)
        else:
            played = False
        binded = 1 if played else 0
        newflag = 0 if played else 1

    response = E.response(
        E.inquire(
            dataid=cid,
            ecflag=1,
            expired=0,
            binded=binded,
            newflag=newflag,
            refid=cid,
            status=status,
        )
    )

    response_body, response_headers = await core_prepare_response(request, response)
    return Response(content=response_body, headers=response_headers)
