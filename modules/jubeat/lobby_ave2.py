import random
import time

from fastapi import APIRouter, Request, Response

from core_common import core_process_request, core_prepare_response, E

# jubeat.dll (sub_10166FB0) registers lobby_ave2 on the "lobby" service, every other module
# on "local": the tag has to be "lobby" or services.get does not give the game a lobby url.
router = APIRouter(prefix="/lobby", tags=["lobby"])
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
#           res: data/refresh_intr(s16) data/start(bool): true = "close the entry now", the
#                master then starts with whoever has connected (alone if nobody has)
#   report  req: same as refresh      res: data/refresh_intr(s16)
#
# Random matching: the entry request carries data/random/level (0..4, only sent in
# that mode) and the game plays whatever music/id the response names, so a random
# player can be put into any waiting room. Such a request looks for an existing room
# first and only opens its own room when nothing is waiting.
#
# All music matching: shopinfo's all_music_matching/is_available switches the feature
# on; the game then sends data/all_music_matching/is_available = true unless its song is
# a shared song the player does not own (such a song must not spread to others). The
# game has no logic of its own for it: it always plays the music/id of the entry
# response, and every player's song travels in its own player card between the cabinets
# (each opponent gets its own chart object, and the end-of-play barrier waits up to 210 s
# for players whose song is longer). So a normal entry that finds no room with its song
# joins a room with another song, provided both sides allow it, and is told to play its
# own song (LOBBY_ALL_MUSIC_OWN_SONG) or the room's song.

# How long a master waits. The game fixes its own deadline when it enters (check's
# entry_timeout, which it clamps to 15..90 s) and cannot extend it afterwards, but it
# starts early when a refresh answers start=true. So the game gets the longest wait and
# the server ends it: after LOBBY_ENTRY_TIMEOUT when nobody joined, and every join
# pushes the start to at least LOBBY_JOIN_EXTEND seconds after that join.
LOBBY_ENTRY_TIMEOUT = 30  # seconds a master waits while nobody has joined
LOBBY_JOIN_EXTEND = 20  # seconds the room stays open after somebody joined (0 = joins do not extend)
LOBBY_MAX_WAIT = 90  # longest wait in total; the game accepts 15..90
LOBBY_CHECK_INTERVAL = 3  # seconds between check polls
LOBBY_REFRESH_INTR = 2  # seconds between master refresh polls
LOBBY_POOL_MAX_AGE = 10  # seconds without refresh before a room is considered dead
LOBBY_MAX_MEMBERS = 4  # master + 3 guests
LOBBY_ALL_MUSIC_MAX_LEVEL_GAP = None  # all music matching: biggest chart level difference, None = any
LOBBY_ALL_MUSIC_OWN_SONG = True  # all music matching: True = everybody plays the song they picked, False = the room's song

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
        if room["owner"] != pcbid and room["members"] < LOBBY_MAX_MEMBERS and not room["started"]
    ]


def _can_join(room, pcbid, game_version, password, local_cnt, exception, lkey_id):
    return (
        room["owner"] != pcbid
        and not room["started"]
        and room["game_version"] == game_version
        and room["password"] == password
        and room["members"] + local_cnt <= LOBBY_MAX_MEMBERS
        and room["roomid"] not in exception
        and not (lkey_id is not None and room["lkey"] == lkey_id)
    )


def _join(room, local_cnt, lkey_id, music=None):
    room["members"] += local_cnt
    now = time.time()
    room["start_at"] = min(max(room["start_at"], now + LOBBY_JOIN_EXTEND), room["created"] + LOBBY_MAX_WAIT)
    if lkey_id is not None:
        room["homed_lkeys"].add(lkey_id)
    return _guest_response(room, music)


def _random_room_order(room, random_level, level):
    # Best room for a random player: another random master of the same level band,
    # then the closest chart level, then the room that has waited longest.
    return (
        0 if room["random_level"] == random_level else 1,
        abs(room["level"] - level),
        room["created"],
    )


def _all_music_allowed(room, level):
    gap = LOBBY_ALL_MUSIC_MAX_LEVEL_GAP
    return gap is None or abs(room["level"] - level) <= gap


def _all_music_room_order(room, level):
    # Best room with another song: the closest chart level, then the longest wait.
    return (abs(room["level"] - level), room["created"])


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


def _guest_response(room, music=None):
    # music: (id, seq) the guest plays instead of the room's song
    mid, seq = music or (room["mid"], room["seq"])
    return E.response(
        E.lobby_ave2(
            E.data(
                E.roomid(room["roomid"], __type="s64", master="0"),
                E.connect(
                    E.key(room["key"], __type="u8"),
                    E("global", room["global"], __type="str"),
                    E.private(room["private"], __type="str"),
                ),
                _music_node(mid, seq),
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
                E.entry_timeout(max(LOBBY_MAX_WAIT, LOBBY_ENTRY_TIMEOUT), __type="s16"),
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
    level = _int(data, "music/level")
    # random/level is only present in random matching
    random_level = _int(data, "random/level", -1) if data.find("random/level") is not None else None
    is_random = random_level is not None
    all_music = _text(data, "all_music_matching/is_available", "1") not in ("0", "false")
    key = _int_list(data, "connect/key")
    glob = _text(data, "connect/global", "0.0.0.0:0")
    priv = _text(data, "connect/private", "0.0.0.0:0")
    exception = set(_int_list(data, "exception"))
    lkey = _int_list(data, "local_matching/key")
    local_cnt = max(_int(data, "local_matching/count", 1), 1)
    # The game prints its 4 digit password with "%d", so 0401 arrives as "401".
    password = _text(data, "matching_password").strip()
    if password.isdigit():
        password = str(int(password))

    # Local group id: cabinets on the same LAN that already grouped via
    # find-circle share this key. Solo cabinets (count 1) are never grouped.
    lkey_id = bytes(lkey).hex() if (local_cnt > 1 and lkey) else None

    join_args = (pcbid, game_version, password, local_cnt, exception, lkey_id)

    # 1) Re-entry by an existing master: hand back their own room. A random master
    #    that is still alone first looks for somebody else's room (step 3).
    own = _my_room(pcbid)
    if own is not None and not (is_random and own["members"] <= local_cnt):
        own["time"] = now
        return _master_response(own)

    # 2) My local group is already homed in a room: join that master as a sibling.
    #    Slots for the whole group were reserved when the group was homed.
    if lkey_id is not None:
        for room in rooms.values():
            if room["owner"] != pcbid and lkey_id in room["homed_lkeys"] and not room["started"]:
                return _guest_response(room)

    # 3) Cross-group join. Random matching takes any waiting room (the response names
    #    the song to play) as long as that room's song is one everybody can play.
    #    Normal matching prefers a room with the same song; with all music matching
    #    on both sides it falls back to a room with another song.
    candidates = [room for room in rooms.values() if _can_join(room, *join_args)]
    if is_random:
        candidates = sorted(
            (room for room in candidates if room["all_music"]),
            key=lambda room: _random_room_order(room, random_level, level),
        )
    else:
        same_song = [room for room in candidates if room["mid"] == mid]
        if same_song or not all_music:
            candidates = same_song
        else:
            candidates = sorted(
                (room for room in candidates if room["all_music"] and _all_music_allowed(room, level)),
                key=lambda room: _all_music_room_order(room, level),
            )
    if candidates:
        if own is not None:
            del rooms[own["roomid"]]  # lone random master moves into the other room
        room = candidates[0]
        keep_song = not is_random and room["mid"] != mid and LOBBY_ALL_MUSIC_OWN_SONG
        return _join(room, local_cnt, lkey_id, (mid, seq) if keep_song else None)

    if own is not None:
        own["time"] = now
        return _master_response(own)

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
        "level": level,
        "random_level": random_level,
        "all_music": all_music,
        "members": local_cnt,
        "key": (key + [0] * 12)[:12],
        "global": glob,
        "private": priv,
        "password": password,
        "lkey": lkey_id or "",
        "homed_lkeys": {lkey_id} if lkey_id else set(),
        "random_seed": random.randint(0, 0x7FFFFFFF),
        "start_at": now + LOBBY_ENTRY_TIMEOUT,  # when refresh starts answering start=true
        "started": False,  # start was sent: the master is closing its entry, nobody may join any more
        "created": now,
        "time": now,
    }
    rooms[roomid] = room
    return _master_response(room)


def lobby_refresh(pcbid, data):
    now = int(time.time())
    _prune_rooms(now)

    start = False
    room = rooms.get(_int(data, "roomid"))
    if room is not None and room["owner"] == pcbid:
        room["time"] = now
        if time.time() >= room["start_at"]:
            room["started"] = start = True

    return E.response(
        E.lobby_ave2(
            E.data(
                E.refresh_intr(LOBBY_REFRESH_INTR, __type="s16"),
                E.start(start, __type="bool"),
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
