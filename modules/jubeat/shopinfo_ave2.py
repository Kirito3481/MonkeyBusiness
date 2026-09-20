import datetime
import random

from fastapi import APIRouter, Request, Response

from core_common import core_process_request, core_prepare_response, E
from modules.jubeat.musicdb_ave2 import ave2_newest_first

router = APIRouter(prefix="/local", tags=["local"])
router.model_whitelist = ["L44"]

# 64 x s32 bitmasks indexed by music_info pos_index.
WHITE_MUSIC_LIST = [
    -2013397024, -132120623, 83885717, 545260541, -124928, -262141, -33554401, 16383,
    0, -29164993, -1006632961, -2097153, -2044768257, -27000833, -1005814401, -12289,
    980541439, -301793280, 4187133, 133988320, 1075838048, -32708, -234909665, 91291647,
    16360958, -2097152, -939524095, -2080501762, -402668669, -1053818881, -7364613, 505855231,
    -45613177, -3145729, -536873105, 1938624909, 12, -1048576, 35651583, -246271,
    260046831, -2097280, -33554433, -256, -268437745, 536870911, -1073798144, -805601281,
    -536870913, -1, -2097153, -1, -2177, 252511999, -940052256, 7,
    0, 0, 0, 0, 0, 0, 0, 0,
]

OPEN_MUSIC_LIST = WHITE_MUSIC_LIST

HOT_MUSIC_LIST = [
    0, 0, 0, 0, 0, 0, 0, 0,
    0, 0, 0, 0, 0, 0, 0, 0,
    0, 0, 0, 0, 0, 0, 0, 0,
    0, 0, 0, 0, 0, 0, 0, 0,
    0, 0, 0, 0, 0, 0, 0, 0,
    0, 0, 0, 0, 0, 0, 0, 0,
    0, -268009600, -3407937, -1, -2177, 252511999, -940052256, 7,
    0, 0, 0, 0, 0, 0, 0, 0,
]



# genre_def_music: the 3 representative songs shown for each genre on the first-tune
# genre select screen. Songs must be playable (in white_music_list and is_default /
# add_default); otherwise the game silently uses its built-in id for that slot, and
# several built-in ones were deleted, which shows up as an empty slot.
GENRE_POPS, GENRE_ANIME, GENRE_SOCIALMUSIC, GENRE_TOHO, GENRE_GAME, GENRE_CLASSIC, GENRE_ORIGINAL = 1, 2, 3, 4, 5, 6, 7

GENRE_DEF_MUSIC = {
    # genre id: [music ids in priority order]
    GENRE_POPS: [11000225, 11000186, 11000079],
    GENRE_ANIME: [11000226, 11000115, 11000241],
    GENRE_SOCIALMUSIC: [11000228, 11000238, 11000223],
    GENRE_GAME: [11000221, 11000214, 11000168],
    GENRE_CLASSIC: [10000037, 30000036, 40000047],
    GENRE_ORIGINAL: [30000121, 10000068, 10000065],
    GENRE_TOHO: [11000239, 11000220, 11000184],
}


def genre_def_music_nodes():
    return [
        E.genre(
            *[E.music(id=music_id, priority=priority) for priority, music_id in enumerate(music_ids, start=1)],
            # all three attributes are mandatory; a data_version above the client's
            # installed data package version (0 here) would stop the parser.
            id=genre_id,
            release_code=2023092000,
            data_version=0,
        )
        for genre_id, music_ids in GENRE_DEF_MUSIC.items()
    ]


# Online matching: how long a cabinet waits for the other players at a sync point before it
# goes on alone. With all music matching the songs differ in length, so whoever finishes first
# sits at the result screen until the longest song is over or this time is up.
# The game counts in half seconds (value * 30 frames) and defaults to 105 s / 50 s.
# None = do not send the setting, the game keeps its defaults.
SYNC_VOICE_WAIT_SECONDS = None  # start-of-play and end-of-play barrier (voice_wait_time, game default 105)
SYNC_RESULT_WAIT_SECONDS = None  # waiting for everybody's final result (result_wait_time, game default 50)


# Weekly recommended songs (music_db.dll WeeklyMusic::ReadXmlNode):
#   weekly_music/value(s32)  +  weekly_music/music_list/<any tag id="music id">...
# The songs get their own folder in the music select, the information screen after login shows
# that there are weekly songs, and playing one multiplies the result bonus by 1.2 instead of 1.0
# (a challenge / bonus song gets 1.4). The game stores `value` but never reads it; it carries the
# week here. A new set every Monday (server time), the same for everybody, half licensed songs
# and half KONAMI originals. Needs music_db_ave2.json, without it the list stays empty.
WEEKLY_MUSIC_COUNT = 10


def weekly_music(day=None):
    """(week number yyyyww, [music ids]) of the ISO week `day` (a datetime.date, default today) is in."""
    year, week, _ = (day or datetime.date.today()).isocalendar()
    rng = random.Random(f"weekly-{year}-{week}")
    licensed, original = ave2_newest_first(False), ave2_newest_first(True)
    half = WEEKLY_MUSIC_COUNT // 2
    picks = rng.sample(licensed, min(half, len(licensed)))
    picks += rng.sample(original, min(WEEKLY_MUSIC_COUNT - len(picks), len(original)))
    return year * 100 + week, sorted(picks)


def _weekly_music_node():
    week, music_ids = weekly_music()
    return E.weekly_music(
        E.value(week, __type="s32"),
        E.music_list(*[E.music(id=str(music_id)) for music_id in music_ids]),
    )


def _sync_wait_nodes():
    if SYNC_VOICE_WAIT_SECONDS is None and SYNC_RESULT_WAIT_SECONDS is None:
        return []
    return [
        E.sync_wait_setting(
            E.result_wait_time(int((SYNC_RESULT_WAIT_SECONDS if SYNC_RESULT_WAIT_SECONDS is not None else 50) * 2), __type="s32"),
            E.voice_wait_time(int((SYNC_VOICE_WAIT_SECONDS if SYNC_VOICE_WAIT_SECONDS is not None else 105) * 2), __type="s32"),
        )
    ]


def jubeat_ave2_global_info():
    # Shared <info> node used by shopinfo_ave2.regist and gametop_ave2.get_info.
    no_bits = [0] * 64

    return E.info(
        E.event_info(),
        E.share_music(),
        E.genre_def_music(*genre_def_music_nodes()),  # Category Select Screen Default Music List
        E.black_jacket_list(no_bits, __type="s32"),  # Music Jacket Censorship
        _weekly_music_node(),
        E.white_music_list(WHITE_MUSIC_LIST, __type="s32"),  # Playable Music List
        E.white_marker_list([-1, 127231] + [0] * 14, __type="s32"),  # Allowed Marker List
        E.white_theme_list([7295] + [0] * 15, __type="s32"),  # Allowed Background List
        E.add_default_music_list(no_bits, __type="s32"),
        E.open_music_list(OPEN_MUSIC_LIST, __type="s32"),
        E.hot_music_list(HOT_MUSIC_LIST, __type="s32"),  # jubility Pickup Music List
        E.expert_option(E.is_available(True, __type="bool")),
        E.konami_logo_50th(E.is_available(True, __type="bool")),
        E.all_music_matching(E.is_available(True, __type="bool")),
        E.random_option(E.is_available(True, __type="bool")),
        E.judge_disp(E.is_available(True, __type="bool")),
        E.password_match(E.is_available(True, __type="bool")),
        E.april_fools_2024(E.is_available(True, __type="bool")),
        E.update_2024091800(E.is_available(True, __type="bool")),
        E.stealth_extend(E.is_available(True, __type="bool")),
        *_sync_wait_nodes(),
        E.lightchat(
            E.map_list(
                E.map(
                    E.event_list(
                        E.event(
                            E.event_type(1, __type="s32"),
                            E.stime(0, __type="u64"),
                            E.etime(0, __type="u64"),
                            E.is_open(True, __type="bool"),
                            E.hint("HINT", __type="str"),
                            E.unlock_text("UNLOCK TEXT", __type="str"),
                            E.condition_list(),
                            E.section_list(
                                E.section(
                                    E.tube_text("01BC00", __type="str"),
                                    E.required_jwatt(10, __type="s32"),
                                    E.reward_type(1, __type="s32"),
                                    E.reward_param(11000105, __type="s32"),
                                    E.dialogue("jubeat beyond the Ave.へようこそ！", __type="str"),
                                    E.mission_list(),
                                    id="1",
                                ),
                            ),
                            id="1",
                        ),
                    ),
                    id="21",
                ),
            ),
        ),
    )


@router.post("/{gameinfo}/shopinfo_ave2/regist")
async def shopinfo_ave2_regist(request: Request):
    request_info = await core_process_request(request)

    response = E.response(
        E.shopinfo_ave2(
            E.data(
                E.cabid(1, __type="u32"),
                E.locationid("EA000001", __type="str"),
                E.facility(
                    E.exist(1, __type="u32"),
                ),
                jubeat_ave2_global_info(),
            ),
        )
    )

    response_body, response_headers = await core_prepare_response(request, response)
    return Response(content=response_body, headers=response_headers)
