import re
import xml.etree.ElementTree as ET
from os import path, stat

from fastapi import APIRouter, Request, Response

from core_common import core_process_request, core_prepare_response, E

router = APIRouter(prefix="/local", tags=["local"])
router.model_whitelist = ["PAN"]

# get_music_info response as read by nostalgia.dll (PAN 2025020501, receiver sub_1801D3B40):
#   overwrite_music_list@revision@release_code / music_spec@index {jk_jpn jk_asia jk_kor jk_idn (bool)
#       unlock_type real_unlock_type (s8) start_date end_date (str) real_once_price real_forever_price (s32)
#       real_start_date real_end_date (str)}   -- optional; if sent, attrs + every field are required
#   permitted_list/flag@sheet_type(0..3) = s32[32] bitmask by music index   -- required
#   gamedata_flag_list/event {index s32, status s8, start_time end_time param1 param2 s64, comment str<32}
#   trend_music_list/trend_music@music_index@rank
#   brooch_list/<any>@index (1..255) marks the brooch available            -- optional
#   quest_data_list, information_list, course_data_list                     -- optional, not sent
# The game loads the song list from its own data_op3/sound/music_list.xml; the
# response "music_list" node is never read. The overwrite list below forces every
# song open: jackets shown in all regions, unlock_type 1, dates wide open.

MUSIC_LIST_PATHS = (
    path.join("modules", "nostalgia", "music_list.xml"),
    "music_list.xml",
)

OPEN_START_DATE = "2017-03-01 10:00"
OPEN_END_DATE = "9999-12-31 23:59"

PERMIT_ALL = [-1] * 32  # 1024 bits, all set
BROOCH_MAX_INDEX = 255

# Trend ranking shown in the "trend" category, best rank first.
TREND_MUSIC = [
    531,  # MEGALOVANIA
    529,  # Once Upon a Time
    530,  # Bonetrousle
    566,  # Battle Against a True Hero
    567,  # Hopes and Dreams
    570,  # BIG SHOT (DELTARUNE)
    569,  # THE WORLD REVOLVING (DELTARUNE)
    568,  # Field of Hopes and Dreams (DELTARUNE)
]

_music_cache = {"key": None, "value": None}


def load_xml(file_path):
    # Konami data XML declares Shift_JIS, which ElementTree refuses; decode first.
    raw = open(file_path, "rb").read().decode("shift_jisx0213", errors="replace")
    raw = re.sub(r"^\s*<\?xml[^>]*\?>", "", raw)
    return ET.fromstring(raw)


def load_music_list():
    # Returns (revision, release_code, {index: {real_once_price, real_forever_price}}),
    # cached until the file changes.
    f = next((p for p in MUSIC_LIST_PATHS if path.exists(p)), None)
    if f is None:
        return None, None, {}

    key = (f, stat(f).st_mtime)
    if _music_cache["key"] == key:
        return _music_cache["value"]

    root = load_xml(f)
    songs = {}
    for m in root.findall("music_spec"):
        if m.get("index") is None:
            continue
        songs[int(m.get("index"))] = {
            "real_once_price": int(m.findtext("real_once_price") or 0),
            "real_forever_price": int(m.findtext("real_forever_price") or 0),
        }

    value = (root.get("revision", ""), root.get("release_code", ""), songs)
    _music_cache["key"] = key
    _music_cache["value"] = value
    return value


def overwrite_music_spec(mid, song):
    return E.music_spec(
        E.jk_jpn(True, __type="bool"),
        E.jk_asia(True, __type="bool"),
        E.jk_kor(True, __type="bool"),
        E.jk_idn(True, __type="bool"),
        E.unlock_type(1, __type="s8"),
        E.real_unlock_type(1, __type="s8"),
        E.start_date(OPEN_START_DATE, __type="str"),
        E.end_date(OPEN_END_DATE, __type="str"),
        E.real_once_price(song["real_once_price"], __type="s32"),
        E.real_forever_price(song["real_forever_price"], __type="s32"),
        E.real_start_date(OPEN_START_DATE, __type="str"),
        E.real_end_date(OPEN_END_DATE, __type="str"),
        index=str(mid),
    )


def build_music_info():
    revision, release_code, songs = load_music_list()

    children = []
    if songs:
        children.append(
            E.overwrite_music_list(
                *[overwrite_music_spec(mid, songs[mid]) for mid in sorted(songs)],
                revision=revision,
                release_code=release_code,
            )
        )

    children += [
        E.permitted_list(
            *[E.flag(PERMIT_ALL, __type="s32", sheet_type=str(sheet)) for sheet in range(4)],
        ),
        E.gamedata_flag_list(),
        E.trend_music_list(
            *[
                E.trend_music(music_index=str(mid), rank=str(rank))
                for rank, mid in enumerate(TREND_MUSIC, start=1)
            ]
        ),
        E.brooch_list(
            *[E.brooch(index=str(b)) for b in range(1, BROOCH_MAX_INDEX + 1)],
        ),
    ]

    return E.response(E.get_music_info(*children))


@router.post("/{gameinfo}/op3_common/get_common_info")
async def op3_common_get_common_info(request: Request):
    request_info = await core_process_request(request)

    response = E.response(
        E.get_common_info(E.olupdate(E.delete_flag(0, __type="bool")))
    )

    response_body, response_headers = await core_prepare_response(request, response)
    return Response(content=response_body, headers=response_headers)


@router.post("/{gameinfo}/op3_common/get_music_info")
async def op3_common_get_music_info(request: Request):
    request_info = await core_process_request(request)

    response = build_music_info()

    response_body, response_headers = await core_prepare_response(request, response)
    return Response(content=response_body, headers=response_headers)
