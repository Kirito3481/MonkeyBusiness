import random
import time

from fastapi import APIRouter, Request, Response

from core_common import core_process_request, core_prepare_response, E

router = APIRouter(prefix="/local", tags=["local"])
router.model_whitelist = ["L44"]

# Protocol (from jubeat.dll lobby_ave2 sender/receiver):
#   check   req: data/enter(bool) data/time(s32)
#           res: data/entrant_nr(u32 @time) data/interval(s16) data/entry_timeout(s16)
#                data/waitlist(@count)/music(u8 = waiting rooms, @id @seq)
#   entry   req: data/locationid cabid version data_version local_matching/key(u8[12])
#                local_matching/count music/id music/seq music/level random/level
#                connect/key(u8[12]) connect/global("ip:port") connect/private("ip:port")
#                exception(s64[] roomids to avoid) [matching_password]
#           res: data/roomid(s64 @master) then master: refresh_intr(s16)
#                guest: connect/key(u8[12]) connect/global connect/private
#                always: music/id(u32) music/seq(u8) [random_seed(u32)]
#   refresh req: data/roomid data/joined(@count)/pcbinfo{cabid,addr}
#           res: data/refresh_intr(s16) data/start(bool, never read by the game)
#   report  req: same as refresh      res: data/refresh_intr(s16)

LOBBY_ENTRY_TIMEOUT = 30  # seconds the game waits for other players
LOBBY_CHECK_INTERVAL = 3  # seconds between check polls
LOBBY_REFRESH_INTR = 2  # seconds between master refresh polls
LOBBY_POOL_MAX_AGE = 10  # seconds without refresh before a room is considered dead
LOBBY_MAX_MEMBERS = 4  # master + 3 guests

# In-memory room pool: roomid -> room dict. Cleared on server restart.
rooms = {}


def _text(node, path, default=""):
    found = node.find(path) if node is not None else None
    if found is None or found.text is None:
        return default
    return found.text


def _int(node, path, default=0):
    try:
        return int(_text(node, path))
    except ValueError:
        return default


def _int_list(node, path):
    return [int(v) for v in _text(node, path).split() if v.strip()]


def _prune_rooms(now):
    for roomid in [r for r, room in rooms.items() if now - room["time"] > LOBBY_POOL_MAX_AGE]:
        del rooms[roomid]


def _my_room(pcbid):
    for room in rooms.values():
        if room["owner"] == pcbid:
            return room
    return None


def _open_rooms(pcbid):
    return [
        room
        for room in rooms.values()
        if room["owner"] != pcbid and room["members"] < LOBBY_MAX_MEMBERS
    ]


def _music_node(mid, seq):
    return E.music(
        E("id", mid, __type="u32"),
        E.seq(seq, __type="u8"),
    )


def _master_response(room):
    return E.response(
        E.lobby_ave2(
            E.data(
                E.roomid(room["roomid"], __type="s64", master="1"),
                E.refresh_intr(LOBBY_REFRESH_INTR, __type="s16"),
                _music_node(room["mid"], room["seq"]),
                E.random_seed(room["random_seed"], __type="u32"),
            ),
        )
    )


def _guest_response(room):
    return E.response(
        E.lobby_ave2(
            E.data(
                E.roomid(room["roomid"], __type="s64", master="0"),
                E.connect(
                    E.key(room["key"], __type="u8"),
                    E("global", room["global"], __type="str"),
                    E.private(room["private"], __type="str"),
                ),
                _music_node(room["mid"], room["seq"]),
                E.random_seed(room["random_seed"], __type="u32"),
            ),
        )
    )


def lobby_check(pcbid, game_version):
    now = int(time.time())
    _prune_rooms(now)

    open_rooms = [r for r in _open_rooms(pcbid) if r["game_version"] == game_version]

    # waitlist: number of waiting rooms per (music id, seq); the game reads the u8
    # value into a per-seq slot keyed by music id.
    waiting = {}
    for room in open_rooms:
        k = (room["mid"], room["seq"])
        waiting[k] = waiting.get(k, 0) + 1

    return E.response(
        E.lobby_ave2(
            E.data(
                E.entrant_nr(len(open_rooms), __type="u32", time=str(now)),
                E.interval(LOBBY_CHECK_INTERVAL, __type="s16"),
                E.entry_timeout(LOBBY_ENTRY_TIMEOUT, __type="s16"),
                E.waitlist(
                    *[
                        E.music(min(count, 255), __type="u8", id=str(mid), seq=str(seq))
                        for (mid, seq), count in waiting.items()
                    ],
                    count=str(len(waiting)),
                ),
            ),
        )
    )


def lobby_entry(pcbid, game_version, data):
    now = int(time.time())
    _prune_rooms(now)

    mid = _int(data, "music/id")
    seq = _int(data, "music/seq")
    key = _int_list(data, "connect/key")
    glob = _text(data, "connect/global", "0.0.0.0:0")
    priv = _text(data, "connect/private", "0.0.0.0:0")
    exception = set(_int_list(data, "exception"))
    lkey = _int_list(data, "local_matching/key")
    local_cnt = max(_int(data, "local_matching/count", 1), 1)
    password = _text(data, "matching_password")

    # Local group id: cabinets on the same LAN that already grouped via
    # find-circle share this key. Solo cabinets (count 1) are never grouped.
    lkey_id = bytes(lkey).hex() if (local_cnt > 1 and lkey) else None

    # 1) Re-entry by an existing master: hand back their own room.
    room = _my_room(pcbid)
    if room is not None:
        room["time"] = now
        return _master_response(room)

    # 2) My local group is already homed in a room: join that master as a sibling.
    #    Slots for the whole group were reserved when the group was homed.
    if lkey_id is not None:
        for room in rooms.values():
            if room["owner"] != pcbid and lkey_id in room["homed_lkeys"]:
                return _guest_response(room)

    # 3) Cross-group join: same song, same version, same password, room for all of us.
    for room in _open_rooms(pcbid):
        if lkey_id is not None and room["lkey"] == lkey_id:
            continue
        if (
            room["game_version"] == game_version
            and room["mid"] == mid
            and room["password"] == password
            and room["members"] + local_cnt <= LOBBY_MAX_MEMBERS
            and room["roomid"] not in exception
        ):
            room["members"] += local_cnt
            if lkey_id is not None:
                room["homed_lkeys"].add(lkey_id)
            return _guest_response(room)

    # 4) Nothing to join: open a new room and become its master.
    roomid = random.randint(1, 0x7FFFFFFF)
    while roomid in rooms:
        roomid = random.randint(1, 0x7FFFFFFF)

    room = {
        "roomid": roomid,
        "owner": pcbid,
        "game_version": game_version,
        "mid": mid,
        "seq": seq,
        "members": local_cnt,
        "key": (key + [0] * 12)[:12],
        "global": glob,
        "private": priv,
        "password": password,
        "lkey": lkey_id or "",
        "homed_lkeys": {lkey_id} if lkey_id else set(),
        "random_seed": random.randint(0, 0x7FFFFFFF),
        "time": now,
    }
    rooms[roomid] = room
    return _master_response(room)


def lobby_refresh(pcbid, data):
    now = int(time.time())
    _prune_rooms(now)

    room = rooms.get(_int(data, "roomid"))
    if room is not None and room["owner"] == pcbid:
        room["time"] = now

    return E.response(
        E.lobby_ave2(
            E.data(
                E.refresh_intr(LOBBY_REFRESH_INTR, __type="s16"),
                E.start(False, __type="bool"),
            ),
        )
    )


def lobby_report(pcbid, data):
    roomid = _int(data, "roomid")
    room = rooms.get(roomid)
    if room is not None and room["owner"] == pcbid:
        del rooms[roomid]

    return E.response(
        E.lobby_ave2(
            E.data(
                E.refresh_intr(LOBBY_REFRESH_INTR, __type="s16"),
            ),
        )
    )


def _pcbid(request_info):
    return request_info["root"].attrib.get("srcid", "")


@router.post("/{gameinfo}/lobby_ave2/check")
async def lobby_ave2_check(request: Request):
    request_info = await core_process_request(request)

    response = lobby_check(_pcbid(request_info), request_info["game_version"])

    response_body, response_headers = await core_prepare_response(request, response)
    return Response(content=response_body, headers=response_headers)


@router.post("/{gameinfo}/lobby_ave2/entry")
async def lobby_ave2_entry(request: Request):
    request_info = await core_process_request(request)

    data = request_info["root"][0].find("data")
    response = lobby_entry(_pcbid(request_info), request_info["game_version"], data)

    response_body, response_headers = await core_prepare_response(request, response)
    return Response(content=response_body, headers=response_headers)


@router.post("/{gameinfo}/lobby_ave2/refresh")
async def lobby_ave2_refresh(request: Request):
    request_info = await core_process_request(request)

    data = request_info["root"][0].find("data")
    response = lobby_refresh(_pcbid(request_info), data)

    response_body, response_headers = await core_prepare_response(request, response)
    return Response(content=response_body, headers=response_headers)


@router.post("/{gameinfo}/lobby_ave2/report")
async def lobby_ave2_report(request: Request):
    request_info = await core_process_request(request)

    data = request_info["root"][0].find("data")
    response = lobby_report(_pcbid(request_info), data)

    response_body, response_headers = await core_prepare_response(request, response)
    return Response(content=response_body, headers=response_headers)
