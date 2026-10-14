"""jubeat: what every version shares. The versions themselves are files of their own (beyondtheave.py and
beyondtheave_lobby.py for beyond the Ave; festo.py, festo_lobby.py and festo_lab.py for festo): each owns a router,
its routes and its settings and hands the requests to a JubeatGame - or to a subclass of it that answers what the
version does differently (JubeatFestoGame). This file has no router and no public coroutine on purpose:
modules/__init__.py runs every file of this folder as a nameless module and would register them a second time."""
import datetime
import json
import os
import random
import statistics
import time
from core_common import core_process_request, core_prepare_response, E
from core_database import get_db
from tinydb import where
from fastapi import Response


# ====================================================================================================
# song data  (was musicdb_ave2.py)
# ====================================================================================================
# Song data of a game version for the modules that need more than a music id.
#
# data/<game version>/music_db.json (10 = festo, 12 = beyond the Ave) is made from the game's music_info.xml by
# jubeat_l44_music_db.py:
#     {"<music id>": [debut version, 1 = KONAMI original / 0 = licensed, level bsc, adv, ext]}
# Without the file every list here is empty and the features built on it stay empty too.

_music_db_cache = {}  # game_version -> {music_id: song}


def music_db(game_version):
    """{music id: [debut, original, level bsc, adv, ext]} of a game version, read once from data/<game version>/."""
    songs = _music_db_cache.get(game_version)
    if songs is None:
        try:
            with open(os.path.join(os.path.dirname(os.path.abspath(__file__)), "data", str(game_version), "music_db.json"), encoding="utf-8") as f:
                songs = {int(music_id): song for music_id, song in json.load(f).items()}
        except (OSError, ValueError):
            songs = {}
        _music_db_cache[game_version] = songs
    return songs

PLAY_COUNT_CACHE_SECONDS = 600
NEW_PLAYER_LEVEL = 3.0  # chart level assumed for somebody without any history
_play_count_cache = {}  # game_version -> (time, {music_id: plays})


def ave2_newest_first(game_version, original=None):
    """Music ids, newest version first; original=True/False keeps only originals / licensed songs."""
    songs = [
        (song[0], music_id)
        for music_id, song in music_db(game_version).items()
        if original is None or bool(song[1]) == original
    ]
    return [music_id for _, music_id in sorted(songs, reverse=True)]


def ave2_level(game_version, music_id, seq):
    song = music_db(game_version).get(music_id)
    return song[2 + seq] if song is not None and 0 <= seq <= 2 else 0.0


def ave2_player_history(jid, game_version):
    """Best-score rows of a player for songs and charts the music database knows."""
    if not jid:
        return []
    rows = get_db().table("jubeat_scores_best").search((where("jubeat_id") == int(jid)) & (where("game_version") == game_version))
    return [row for row in rows if row.get("music_id") in music_db(game_version) and row.get("seq") in (0, 1, 2)]


def ave2_player_level(jid, game_version, played=(), history=None):
    """(chart level, chart) the player plays: the tunes of this credit, else the last ten charts played."""
    if history is None:
        history = ave2_player_history(jid, game_version)
    recent = sorted(history, key=lambda row: row.get("timestamp", 0))[-10:]
    charts = [(m, s) for m, s in played if m in music_db(game_version) and s in (0, 1, 2)] or [(row["music_id"], row["seq"]) for row in recent]
    levels = [ave2_level(game_version, m, s) for m, s in charts if ave2_level(game_version, m, s) > 0]
    return (statistics.median(levels) if levels else NEW_PLAYER_LEVEL), (charts[-1][1] if charts else 0)


def ave2_play_counts(game_version):
    """{music_id: plays of every player, chart and mode}, cached for a few minutes."""
    now = time.time()
    cached = _play_count_cache.get(game_version)
    if cached is not None and now - cached[0] < PLAY_COUNT_CACHE_SECONDS:
        return cached[1]
    plays = {}
    for row in get_db().table("jubeat_scores_best").all():
        if row.get("game_version") == game_version:
            plays[row["music_id"]] = plays.get(row["music_id"], 0) + int(row.get("play_cnt", 0))
    _play_count_cache[game_version] = (now, plays)
    return plays


# ====================================================================================================
# full combo challenge  (was fcchallenge_ave2.py)
# ====================================================================================================
# FULL COMBO CHALLENGE of jubeat beyond the Ave (data/player/fc_challenge in gametop / gameend).
#
# From jubeat.dll (profile parser sub_100ED0F0, result handling sub_100A6C80, gameend sender
# sub_100F47E0):
#   gametop sends  fc_challenge/today{music_id s32, state u8}  (required)
#                  fc_challenge/whim {music_id s32, state u8}  (optional)
#   A music_id the game knows becomes a challenge song: it is shown on the information screen
#   after login, playing it shows the challenge mark until the first miss, and a full combo
#   completes it. "today" is the same song for everybody, "whim" is a personal one that can be
#   tried once (the game gives it one attempt as long as state has no 0x40).
#   state bits:  1 << (2 * seq) played on that chart      2 << (2 * seq) full combo on that chart
#                0x40 played                               0x80 completed
#   gameend sends fc_challenge/today{music_id} and fc_challenge/whim{music_id, state}, each only
#   when that song was played in the credit. The state of "today" does not come back, so it is
#   worked out here from the tune results.
# Both songs change at midnight (server time). Needs data/<game version>/music_db.json, without it the
# challenge stays off (music_id 0).

FCC_WHIM_LEVEL_RANGE = 1.0  # the personal song has a chart within +- this of the level the player plays

CLEAR_FLAG_FULL_COMBO = 0x04
CLEAR_FLAG_EXCELLENT = 0x08
STATE_PLAYED = 0x40
STATE_COMPLETED = 0x80


def fc_challenge_today(game_version, today=None):
    """Today's challenge song, the same for every player."""
    songs = sorted(music_db(game_version))
    if not songs:
        return 0
    return random.Random(f"fcc-today-{today or time.strftime('%Y%m%d')}").choice(songs)


def _whim(jid, game_version, today, avoid):
    level, _ = ave2_player_level(jid, game_version)
    songs = [
        music_id
        for music_id in sorted(music_db(game_version))
        if music_id != avoid and any(abs(ave2_level(game_version, music_id, seq) - level) <= FCC_WHIM_LEVEL_RANGE for seq in (0, 1, 2))
    ] or [music_id for music_id in sorted(music_db(game_version)) if music_id != avoid]
    return random.Random(f"fcc-whim-{today}-{jid}").choice(songs) if songs else 0


def fc_challenge_refresh(profile, jid, game_version, today=None):
    """Make profile["fc_challenge"] the challenge of today. Returns True when it changed (save it)."""
    today = today or time.strftime("%Y%m%d")
    current = profile.get("fc_challenge")
    if isinstance(current, dict) and current.get("date") == today:
        return False
    music_id = fc_challenge_today(game_version, today)
    profile["fc_challenge"] = {
        "date": today,
        "today": {"music_id": music_id, "state": 0},
        "whim": {"music_id": _whim(jid, game_version, today, music_id), "state": 0},
    }
    return True


def fc_challenge_result(profile, tunes, whim_music_id=None, whim_state=None):
    """Take the outcome of a credit into profile["fc_challenge"].

    tunes: [(music_id, seq, clear flags of that play)]; whim_*: what gameend sent for the whim song.
    """
    challenge = profile.get("fc_challenge")
    if not isinstance(challenge, dict):
        return
    today = challenge.get("today", {})
    for music_id, seq, clear in tunes:
        if music_id and music_id == today.get("music_id") and seq in (0, 1, 2):
            today["state"] = today.get("state", 0) | (1 << (2 * seq)) | STATE_PLAYED
            if clear & (CLEAR_FLAG_FULL_COMBO | CLEAR_FLAG_EXCELLENT):
                today["state"] |= (2 << (2 * seq)) | STATE_COMPLETED
    whim = challenge.get("whim", {})
    if whim_music_id and whim_music_id == whim.get("music_id") and whim_state is not None:
        whim["state"] = (whim.get("state", 0) | whim_state) & 0xFF


# ====================================================================================================
# shopinfo: what the shop (every cabinet) is told  (was shopinfo_ave2.py)
# ====================================================================================================
# LIGHT CHAT. The maps are a setting of the version (LIGHTCHAT_MAPS in beyondtheave.py): map id -> event id ->
# (event type, sections (id, tube text, required jwatt, reward type, reward param, dialogue)).
# Event types (jubeat.dll sub_1007ADA0 @0x1007AE2A): 1 an ordinary chat, done for good once its sections are cleared;
# 2 one with an end date shown; 3 the ENDLESS chat: when a section of it is cleared the game takes the reward and puts
# acquired_jwatt back to 0 by itself (0x1010D126), so it never ends - and the game NEEDS one: with no chat of type
# 1 / 2 / 6 left it falls back to the first type 3 event, and without any it runs off the end of a list and dies
# (the chat select crash of 2026-09-22). Map 99 (CONCIERGE) is always available whatever its conditions say.
# Conditions (condition_list) only decide when an event unlocks; they do not repeat anything.
EVENT_CHAT, EVENT_ENDLESS = 1, 3
# Reward types (jubeat.dll: grant sub_100E8000, display sub_1017F3B0): 1 music (music id), 2 title, 3 marker,
# 4 play background, 7 a whole bonus tune (param ignored), 9 jbox points, 11 BONUS TUNE GAUGE points (param = points
# as they are, shown as "100 ten"; a bonus tune costs 1000), 13 emblem, 14 jwatt. The game adds the reward itself
# and sends the result with the save (bonus_tune_points), the server only keeps what it is told.
REWARD_MUSIC, REWARD_BONUS_TUNE_GAUGE = 1, 11
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
# and half KONAMI originals. Needs data/<game version>/music_db.json, without it the list stays empty.
WEEKLY_MUSIC_COUNT = 10


def weekly_music(game_version, day=None):
    """(week number yyyyww, [music ids]) of the ISO week `day` (a datetime.date, default today) is in."""
    year, week, _ = (day or datetime.date.today()).isocalendar()
    rng = random.Random(f"weekly-{year}-{week}")
    licensed, original = ave2_newest_first(game_version, False), ave2_newest_first(game_version, True)
    half = WEEKLY_MUSIC_COUNT // 2
    picks = rng.sample(licensed, min(half, len(licensed)))
    picks += rng.sample(original, min(WEEKLY_MUSIC_COUNT - len(picks), len(original)))
    return year * 100 + week, sorted(picks)


def _weekly_music_node(game_version):
    week, music_ids = weekly_music(game_version)
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


def jubeat_ave2_global_info(lightchat_maps, game_version):
    # Shared <info> node used by shopinfo_ave2.regist and gametop_ave2.get_info.
    no_bits = [0] * 64

    return E.info(
        E.event_info(),
        E.share_music(),
        E.genre_def_music(*genre_def_music_nodes()),  # Category Select Screen Default Music List
        E.black_jacket_list(no_bits, __type="s32"),  # Music Jacket Censorship
        _weekly_music_node(game_version),
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
                *[
                    E.map(
                        E.event_list(
                            *[
                                E.event(
                                    E.event_type(event_type, __type="s32"),
                                    E.stime(0, __type="u64"),
                                    E.etime(0, __type="u64"),
                                    E.is_open(True, __type="bool"),
                                    E.hint("HINT", __type="str"),
                                    E.unlock_text("UNLOCK TEXT", __type="str"),
                                    E.condition_list(),
                                    E.section_list(
                                        *[
                                            E.section(
                                                E.tube_text(tube_text, __type="str"),
                                                E.required_jwatt(required_jwatt, __type="s32"),
                                                E.reward_type(reward_type, __type="s32"),
                                                E.reward_param(reward_param, __type="s32"),
                                                E.dialogue(dialogue, __type="str"),
                                                E.mission_list(),
                                                id=str(section_id),
                                            )
                                            for section_id, tube_text, required_jwatt, reward_type, reward_param, dialogue in sections
                                        ],
                                    ),
                                    id=str(event_id),
                                )
                                for event_id, (event_type, sections) in events.items()
                            ],
                        ),
                        id=str(map_id),
                    )
                    for map_id, events in lightchat_maps.items()
                ],
            ),
        ),
    )


# ====================================================================================================
# gametop: the player's data at the start of a credit  (was gametop_ave2.py)
# ====================================================================================================
MDATA_PARTITIONS = 3  # the game calls get_mdata with mdata_ver 1..3


def get_profile(cid):
    return get_db().table("jubeat_profile").get(where("card") == cid)


def get_profile_by_jid(jid):
    return get_db().table("jubeat_profile").get(where("jubeat_id") == int(jid))


def get_game_profile(cid, game_version):
    profile = get_profile(cid)
    if profile is None:
        return None

    stored = profile["version"].get(str(game_version), None)
    if stored is None:
        return None

    return merge_defaults(new_game_profile(game_version, stored.get("name", "")), stored)


def start_version_profile(card, game_version):
    """A fresh profile of game_version for a card that so far has profiles of other versions only. The name comes
    along from the newest of them, nothing else (scores stay with their version). Saved; returns the profile."""
    newest = card["version"][max(card["version"], key=int)]
    profile = new_game_profile(game_version, newest.get("name", ""))
    fc_challenge_refresh(profile, card["jubeat_id"], game_version)
    card["version"][str(game_version)] = profile
    get_db().table("jubeat_profile").upsert(card, where("card") == card["card"])
    return profile


def merge_defaults(defaults, stored):
    # Deep-merge so profiles saved by older server code still have every key.
    merged = dict(defaults)
    for k, v in stored.items():
        if isinstance(v, dict) and isinstance(merged.get(k), dict):
            merged[k] = merge_defaults(merged[k], v)
        else:
            merged[k] = v
    return merged


def new_game_profile(game_version, name):
    return {
        "game_version": game_version,
        "name": name,
        "event_flag": 0,
        "tune_cnt": 0,
        "save_cnt": 0,
        "saved_cnt": 0,
        "fc_cnt": 0,
        "ex_cnt": 0,
        "clear_cnt": 0,
        "match_cnt": 0,
        "beat_cnt": 0,
        "mynews_cnt": 0,
        "mtg_entry_cnt": 0,
        "mtg_hold_cnt": 0,
        "mtg_result": 0,
        "bonus_tune_points": 0,
        "is_bonus_tune_played": 0,
        "last": {
            "play_time": 0,
            "shopname": "",
            "areaname": "",
            "music_id": 0,
            "seq_id": 0,
            "sort": 0,
            "category": 0,
            "expert_option": 0,
            "marker": 0,
            "theme": 0,
            "title": 0,
            "parts": 0,
            "rank_sort": 0,
            "combo_disp": 0,
            "target_type": 0,
            "judge_disp": 0,
            "random_option": 0,
            "emblem": [0, 0, 0, 0, 0],
            "matching": 1,
            "hard": 0,
            "hazard": 0,
        },
        "item": {
            "music_list": [-1] * 64,
            "secret_list": [-1] * 64,
            "theme_list": [-1] * 16,
            "marker_list": [-1] * 16,
            "title_list": [-1] * 160,
            "parts_list": [-1] * 160,
            "emblem_list": [-1] * 96,
            "commu_list": [-1] * 16,
            "new": {
                "secret_list": [0] * 64,
                "theme_list": [0] * 16,
                "marker_list": [0] * 16,
            },
        },
        "fill_in_category": {
            "normal": {
                "no_gray_flag_list": [0] * 16,
                "all_yellow_flag_list": [0] * 16,
                "full_combo_flag_list": [0] * 16,
                "excellent_flag_list": [0] * 16,
            },
            "hard": {
                "no_gray_flag_list": [0] * 16,
                "all_yellow_flag_list": [0] * 16,
                "full_combo_flag_list": [0] * 16,
                "excellent_flag_list": [0] * 16,
            },
        },
        "jbox": {
            "point": 0,
            "normal_index": 0,
            "premium_index": 0,
        },
        "born": {
            "status": 0,
            "year": 0,
        },
        "navi_flag": 0,
        # lightchat progress, keyed by map/event/section/mission/condition id (strings).
        # Map 21 / event 1 / section 1 are the ones defined in shopinfo's global info.
        "lightchat": {
            "current_map_id": 0,
            "current_event_id": 0,
            "maps": {
                "21": {
                    "tune_count": 0,
                    "last_daily_bonus_time": 0,
                    "events": {
                        "1": {
                            "display_state": 1,
                            "conditions": {},
                            "sections": {
                                "1": {"acquired_jwatt": 0, "is_cleared": 0, "missions": {}},
                            },
                        },
                    },
                },
            },
        },
    }


def _lightchat_node(lc, lightchat_maps):
    # Load format (jubeat.dll receiver sub_1007B840 -> map sub_1007D4B0 -> event sub_100796D0
    # -> section sub_10082300 / condition sub_100785E0):
    #   lightchat { current_map_id s32, current_event_id s32, map_list/map@id { tune_count s32,
    #   last_daily_bonus_time u64, event_list/event@id { display_state s32,
    #   condition_list/condition@id { is_cleared bool, progress s32 },
    #   section_list/section@id { acquired_jwatt s32, is_cleared bool,
    #   mission_list/mission@id { is_cleared bool, progress s32 } } } } }
    def progress_nodes(tag, entries):
        return [
            E(
                tag,
                E.is_cleared(bool(v.get("is_cleared", 0)), __type="bool"),
                E.progress(v.get("progress", 0), __type="s32"),
                id=str(k),
            )
            for k, v in sorted(entries.items(), key=lambda kv: int(kv[0]))
        ]

    # every map / event / section shopinfo defines, also what the player has not reached yet (see LIGHTCHAT_MAPS):
    # a copy with empty entries added, the stored progress itself is left alone
    known = {k: dict(v, events={e: dict(ev, sections=dict(ev.get("sections", {}))) for e, ev in v.get("events", {}).items()})
             for k, v in lc["maps"].items()}
    for defined_map, events in lightchat_maps.items():
        entry = known.setdefault(str(defined_map), {"events": {}})
        for defined_event, (_event_type, defined_sections) in events.items():
            event = entry["events"].setdefault(str(defined_event), {"sections": {}})
            for section_id, *_ in defined_sections:
                event["sections"].setdefault(str(section_id), {})

    maps = []
    for map_id, m in sorted(known.items(), key=lambda kv: int(kv[0])):
        events = []
        for event_id, ev in sorted(m.get("events", {}).items(), key=lambda kv: int(kv[0])):
            stored_sections = dict(ev.get("sections", {}))
            sections = [
                E.section(
                    E.acquired_jwatt(sec.get("acquired_jwatt", 0), __type="s32"),
                    E.is_cleared(bool(sec.get("is_cleared", 0)), __type="bool"),
                    E.mission_list(*progress_nodes("mission", sec.get("missions", {}))),
                    id=str(section_id),
                )
                for section_id, sec in sorted(stored_sections.items(), key=lambda kv: int(kv[0]))
            ]
            events.append(
                E.event(
                    E.display_state(ev.get("display_state", 1), __type="s32"),
                    E.condition_list(*progress_nodes("condition", ev.get("conditions", {}))),
                    E.section_list(*sections),
                    id=str(event_id),
                )
            )
        maps.append(
            E.map(
                E.tune_count(m.get("tune_count", 0), __type="s32"),
                E.last_daily_bonus_time(m.get("last_daily_bonus_time", 0), __type="u64"),
                E.event_list(*events),
                id=str(map_id),
            )
        )

    return E.lightchat(
        E.current_map_id(lc["current_map_id"], __type="s32"),
        E.current_event_id(lc["current_event_id"], __type="s32"),
        E.map_list(*maps),
    )


def _fill_in_node(tag, cat):
    return E(
        tag,
        E.no_gray_flag_list(cat["no_gray_flag_list"], __type="s32"),
        E.all_yellow_flag_list(cat["all_yellow_flag_list"], __type="s32"),
        E.full_combo_flag_list(cat["full_combo_flag_list"], __type="s32"),
        E.excellent_flag_list(cat["excellent_flag_list"], __type="s32"),
    )


def format_profile(game, game_version, jid, profile):
    last = profile["last"]
    item = profile["item"]
    fill = profile["fill_in_category"]
    jbox = profile["jbox"]
    born = profile["born"]
    # full combo challenge of today; see fcchallenge_ave2 (the handlers refresh and save it)
    fcc = profile.get("fc_challenge") or {"today": {"music_id": 0, "state": 0}, "whim": {"music_id": 0, "state": 0}}

    return E.response(
        game.module(
            "gametop",
            E.data(
                game.global_info(game_version),
                E.player(
                    E.jid(jid, __type="s32"),
                    E.session_id(1, __type="s32"),
                    E.name(profile["name"], __type="str"),
                    E.event_flag(profile["event_flag"], __type="u64"),
                    E.info(
                        E.inherit(0, __type="bool"),
                        E.tune_cnt(profile["tune_cnt"], __type="s32"),
                        E.save_cnt(profile["save_cnt"], __type="s32"),
                        E.saved_cnt(profile["saved_cnt"], __type="s32"),
                        E.fc_cnt(profile["fc_cnt"], __type="s32"),
                        E.ex_cnt(profile["ex_cnt"], __type="s32"),
                        E.clear_cnt(profile["clear_cnt"], __type="s32"),
                        E.match_cnt(profile["match_cnt"], __type="s32"),
                        E.beat_cnt(profile["beat_cnt"], __type="s32"),
                        E.mynews_cnt(profile["mynews_cnt"], __type="s32"),
                        E.mtg_entry_cnt(profile["mtg_entry_cnt"], __type="s32"),
                        E.mtg_hold_cnt(profile["mtg_hold_cnt"], __type="s32"),
                        E.mtg_result(profile["mtg_result"], __type="u8"),
                        E.bonus_tune_points(profile["bonus_tune_points"], __type="s32"),
                        E.is_bonus_tune_played(profile["is_bonus_tune_played"], __type="bool"),
                    ),
                    E.last(
                        E.play_time(last["play_time"], __type="s64"),
                        E.shopname(last["shopname"], __type="str"),
                        E.areaname(last["areaname"], __type="str"),
                        E.music_id(last["music_id"], __type="s32"),
                        E.seq_id(last["seq_id"], __type="s8"),
                        E.sort(last["sort"], __type="s8"),
                        E.category(last["category"], __type="s8"),
                        E.expert_option(last["expert_option"], __type="s8"),
                        E.settings(
                            E.marker(last["marker"], __type="s8"),
                            E.theme(last["theme"], __type="s8"),
                            E.title(last["title"], __type="s16"),
                            E.parts(last["parts"], __type="s16"),
                            E.rank_sort(last["rank_sort"], __type="s8"),
                            E.combo_disp(last["combo_disp"], __type="s8"),
                            E.target_type(last["target_type"], __type="s32"),
                            E.judge_disp(last["judge_disp"], __type="s8"),
                            E.random_option(last["random_option"], __type="s8"),
                            E.emblem(last["emblem"], __type="s16"),
                            E.matching(last["matching"], __type="s8"),
                            E.hard(last["hard"], __type="s8"),
                            E.hazard(last["hazard"], __type="s8"),
                        ),
                    ),
                    E.item(
                        E.music_list(item["music_list"], __type="s32"),
                        E.secret_list(item["secret_list"], __type="s32"),
                        E.theme_list(item["theme_list"], __type="s32"),
                        E.marker_list(item["marker_list"], __type="s32"),
                        E.title_list(item["title_list"], __type="s32"),
                        E.parts_list(item["parts_list"], __type="s32"),
                        E.emblem_list(item["emblem_list"], __type="s32"),
                        E.commu_list(item["commu_list"], __type="s32"),
                        E.new(
                            E.secret_list(item["new"]["secret_list"], __type="s32"),
                            E.theme_list(item["new"]["theme_list"], __type="s32"),
                            E.marker_list(item["new"]["marker_list"], __type="s32"),
                        ),
                    ),
                    E.fc_challenge(
                        E.today(
                            E.music_id(fcc["today"]["music_id"], __type="s32"),
                            E.state(fcc["today"]["state"], __type="u8"),
                        ),
                        E.whim(
                            E.music_id(fcc["whim"]["music_id"], __type="s32"),
                            E.state(fcc["whim"]["state"], __type="u8"),
                        ),
                    ),
                    E.free_first_play(
                        E.is_available(0, __type="bool"),
                    ),
                    E.event_info(*game.player_event_nodes(profile)),
                    E.jbox(
                        E.point(jbox["point"], __type="s32"),
                        E.emblem(
                            E.normal(E.index(jbox["normal_index"], __type="s16")),
                            E.premium(E.index(jbox["premium_index"], __type="s16")),
                        ),
                    ),
                    E.new_music(),
                    E.navi(
                        E.flag(profile["navi_flag"], __type="u64"),
                    ),
                    E.gift_list(),
                    E.born(
                        E.status(born["status"], __type="s8"),
                        E.year(born["year"], __type="s16"),
                    ),
                    E.question_list(),
                    E.server(),
                    E.fill_in_category(
                        _fill_in_node("normal", fill["normal"]),
                        _fill_in_node("hard", fill["hard"]),
                    ),
                    *game.player_nodes(profile),
                ),
            ),
        )
    )


def _mode_node(tag, charts):
    # charts: {seq: score_row} for one of normal/hard. Arrays are indexed by seq 0..2.
    def arr(key):
        return [charts[s][key] if s in charts else 0 for s in range(3)]

    bars = [
        E.bar(charts[s]["bar"], __type="u8", seq=str(s))
        for s in range(3)
        if s in charts and charts[s].get("bar")
    ]

    return E(
        tag,
        E.score(arr("score"), __type="s32"),
        E.music_rate(arr("music_rate"), __type="s32"),
        E.clear(arr("clear"), __type="s8"),
        E.play_cnt(arr("play_cnt"), __type="s32"),
        E.clear_cnt(arr("clear_cnt"), __type="s32"),
        E.fc_cnt(arr("fc_cnt"), __type="s32"),
        E.ex_cnt(arr("ex_cnt"), __type="s32"),
        *bars,
    )


def format_scores(game, jid, game_version, partition):
    rows = get_db().table("jubeat_scores_best").search(
        (where("jubeat_id") == int(jid)) & (where("game_version") == game_version)
    )

    by_music = {}
    for row in rows:
        by_music.setdefault(row["music_id"], {"normal": {}, "hard": {}})
        by_music[row["music_id"]]["hard" if row["hard"] else "normal"][row["seq"]] = row

    # Split the song list across the 3 get_mdata calls the game makes.
    music_ids = sorted(by_music)
    part = max(1, min(partition, MDATA_PARTITIONS)) - 1
    chunk = music_ids[part::MDATA_PARTITIONS]

    musicdata = []
    for mid in chunk:
        modes = by_music[mid]
        children = []
        if modes["normal"]:
            children.append(_mode_node("normal", modes["normal"]))
        if modes["hard"]:
            children.append(_mode_node("hard", modes["hard"]))
        musicdata.append(E.musicdata(*children, music_id=str(mid)))

    return E.response(
        game.module(
            "gametop",
            E.data(
                E.player(
                    E.jid(int(jid), __type="s32"),
                    E.mdata_list(*musicdata),
                ),
            ),
        )
    )


# ====================================================================================================
# gameend: saving a credit  (was gameend_ave2.py)
# ====================================================================================================
# Protocol (from jubeat.dll gameend_ave2 sender/receiver):
#   regist req: retry, pcbinfo@client_data_version,
#               data/info{time_gametop time_gameend play_time cabid payment mode shopname areaname ...}
#               data/result(@count)/tune(@id){music roomid timestamp player{score@seq@clear@combo
#                   music_rate marker theme category is_hard_mode nr_* best_score best_music_rate
#                   best_clear play_cnt clear_cnt fc_cnt ex_cnt mbar play_mbar} friend...}
#               data/player{session_id refid jid name event_flag continue_count info last item
#                   meeting fc_challenge free_first_play event_info official_news mynews jbox
#                   history eapass navi gift_list born jubility server fill_in_category lightchat ...}
#          res: data/player/end_final_session_id(s32)  (only field the game reads)
#   final  req: retry, pcbinfo, data/info{born question_list payment_list}
#               data/player{end_final_session_id refid jid name item/emblem_list jbox born question_list}
#          res: empty gameend_ave2 (game only checks that a response arrived)

INFO_COUNTERS = (
    "tune_cnt",
    "save_cnt",
    "saved_cnt",
    "fc_cnt",
    "ex_cnt",
    "clear_cnt",
    "match_cnt",
    "beat_cnt",
    "mynews_cnt",
    "mtg_entry_cnt",
    "mtg_hold_cnt",
    "mtg_result",
    "bonus_tune_points",
    "is_bonus_tune_played",
)
LAST_FIELDS = ("expert_option", "sort", "category")
SETTINGS_FIELDS = (
    "marker",
    "theme",
    "title",
    "parts",
    "rank_sort",
    "combo_disp",
    "target_type",
    "judge_disp",
    "random_option",
    "matching",
    "hard",
    "hazard",
)
ITEM_LISTS = (
    "music_list",
    "secret_list",
    "theme_list",
    "marker_list",
    "title_list",
    "parts_list",
    "emblem_list",
    "commu_list",
)
NEW_ITEM_LISTS = ("secret_list", "theme_list", "marker_list")
FILL_IN_LISTS = (
    "no_gray_flag_list",
    "all_yellow_flag_list",
    "full_combo_flag_list",
    "excellent_flag_list",
)


def _text(node, path):
    found = node.find(path) if node is not None else None
    if found is None or found.text is None:
        return None
    return found.text


def _int(node, path, default=0):
    try:
        return int(_text(node, path))
    except (TypeError, ValueError):
        return default


def _int_list(node, path):
    text = _text(node, path)
    if text is None:
        return None
    return [int(v) for v in text.split() if v.strip()]


def _copy_ints(dst, node, keys):
    for key in keys:
        if node is not None and node.find(key) is not None:
            dst[key] = _int(node, key, dst.get(key, 0))


def _copy_lists(dst, node, keys):
    for key in keys:
        values = _int_list(node, key)
        if values is not None:
            dst[key] = values


def _save_profile(refid, game_version, profile):
    db = get_db().table("jubeat_profile")
    all_profiles_for_card = db.get(where("card") == refid)
    if all_profiles_for_card is None:
        return
    all_profiles_for_card["version"][str(game_version)] = profile
    db.upsert(all_profiles_for_card, where("card") == refid)


def update_profile_from_regist(profile, info, player):
    last = profile["last"]

    if info is not None:
        last["play_time"] = _int(info, "time_gameend", last["play_time"])
        last["shopname"] = _text(info, "shopname") or last["shopname"]
        last["areaname"] = _text(info, "areaname") or last["areaname"]

    if player is None:
        return profile

    profile["event_flag"] = _int(player, "event_flag", profile["event_flag"])
    _copy_ints(profile, player.find("info"), INFO_COUNTERS)

    lastnode = player.find("last")
    _copy_ints(last, lastnode, LAST_FIELDS)
    if lastnode is not None:
        settings = lastnode.find("settings")
        _copy_ints(last, settings, SETTINGS_FIELDS)
        emblem = _int_list(settings, "emblem")
        if emblem is not None and len(emblem) == 5:
            last["emblem"] = emblem

    item = player.find("item")
    _copy_lists(profile["item"], item, ITEM_LISTS)
    if item is not None:
        _copy_lists(profile["item"]["new"], item.find("new"), NEW_ITEM_LISTS)

    fill = player.find("fill_in_category")
    if fill is not None:
        _copy_lists(profile["fill_in_category"]["normal"], fill.find("normal"), FILL_IN_LISTS)
        _copy_lists(profile["fill_in_category"]["hard"], fill.find("hard"), FILL_IN_LISTS)

    update_profile_from_final(profile, player)

    if player.find("navi/flag") is not None:
        profile["navi_flag"] = _int(player, "navi/flag", profile["navi_flag"])

    return profile


def update_lightchat(profile, node):
    # Save format (jubeat.dll sender sub_1007BAD0 / event sub_10079A80 / section sub_100825F0):
    #   lightchat { map_list/map@id { tune_count, last_daily_bonus_time, event_list/event@id {
    #   section_list/section@id { acquired_jwatt, is_cleared, mission_list/mission@id { is_cleared, progress } },
    #   condition_list/condition_data@id/condition@id { is_cleared, progress }, display_state } },
    #   next_map_id, next_event_id, next_section_id }
    if node is None:
        return profile

    lc = profile["lightchat"]
    for m in node.findall("map_list/map"):
        map_id = m.get("id")
        if map_id is None:
            continue
        stored_map = lc["maps"].setdefault(map_id, {"tune_count": 0, "last_daily_bonus_time": 0, "events": {}})
        stored_map["tune_count"] = _int(m, "tune_count", stored_map["tune_count"])
        stored_map["last_daily_bonus_time"] = _int(m, "last_daily_bonus_time", stored_map["last_daily_bonus_time"])

        for ev in m.findall("event_list/event"):
            event_id = ev.get("id")
            if event_id is None:
                continue
            stored_ev = stored_map["events"].setdefault(
                event_id, {"display_state": 1, "conditions": {}, "sections": {}}
            )
            if ev.find("display_state") is not None:
                stored_ev["display_state"] = _int(ev, "display_state", stored_ev["display_state"])

            for cond in ev.findall("condition_list/condition_data/condition") + ev.findall("condition_list/condition"):
                if cond.get("id") is not None:
                    stored_ev["conditions"][cond.get("id")] = {
                        "is_cleared": _int(cond, "is_cleared"),
                        "progress": _int(cond, "progress"),
                    }

            for sec in ev.findall("section_list/section"):
                section_id = sec.get("id")
                if section_id is None:
                    continue
                stored_sec = stored_ev["sections"].setdefault(
                    section_id, {"acquired_jwatt": 0, "is_cleared": 0, "missions": {}}
                )
                stored_sec["acquired_jwatt"] = _int(sec, "acquired_jwatt", stored_sec["acquired_jwatt"])
                stored_sec["is_cleared"] = _int(sec, "is_cleared", stored_sec["is_cleared"])
                for mis in sec.findall("mission_list/mission"):
                    if mis.get("id") is not None:
                        stored_sec["missions"][mis.get("id")] = {
                            "is_cleared": _int(mis, "is_cleared"),
                            "progress": _int(mis, "progress"),
                        }

    if node.find("next_map_id") is not None:
        lc["current_map_id"] = _int(node, "next_map_id", lc["current_map_id"])
    if node.find("next_event_id") is not None:
        lc["current_event_id"] = _int(node, "next_event_id", lc["current_event_id"])

    return profile


def update_profile_from_final(profile, player):
    # Shared by regist and final: emblem ownership, jbox state, birth info.
    if player is None:
        return profile

    emblem_list = _int_list(player, "item/emblem_list")
    if emblem_list is not None:
        profile["item"]["emblem_list"] = emblem_list

    jbox = player.find("jbox")
    if jbox is not None:
        profile["jbox"]["point"] = _int(jbox, "point", profile["jbox"]["point"])
        emblem_type = _int(jbox, "emblem/type", -1)
        index = _int(jbox, "emblem/index", 0)
        if emblem_type == 1:
            profile["jbox"]["normal_index"] = index
        elif emblem_type == 2:
            profile["jbox"]["premium_index"] = index

    born = player.find("born")
    if born is not None:
        profile["born"]["status"] = _int(born, "status", profile["born"]["status"])
        profile["born"]["year"] = _int(born, "year", profile["born"]["year"])

    return profile


def save_scores(jid, game_version, result, profile):
    if result is None:
        return

    db = get_db().table("jubeat_scores_best")
    now_ms = int(time.time() * 1000)

    for tune in result.findall("tune"):
        p = tune.find("player")
        score_node = p.find("score") if p is not None else None
        if score_node is None:
            continue

        music_id = _int(tune, "music", -1)
        seq = int(score_node.get("seq", "0"))
        if music_id < 0 or seq not in (0, 1, 2):
            continue  # edit charts (seq 3) are not kept in the best table

        hard = 1 if _int(p, "is_hard_mode") else 0
        timestamp = _int(tune, "timestamp", now_ms)

        # The game already merged this play into its own record and sends the
        # updated per-chart best/counters, so store those as-is.
        row = {
            "jubeat_id": int(jid),
            "game_version": game_version,
            "music_id": music_id,
            "seq": seq,
            "hard": hard,
            "score": _int(p, "best_score", _int(p, "score")),
            "music_rate": _int(p, "best_music_rate", _int(p, "music_rate")),
            "clear": _int(p, "best_clear", int(score_node.get("clear", "0"))),
            "play_cnt": _int(p, "play_cnt", 1),
            "clear_cnt": _int(p, "clear_cnt"),
            "fc_cnt": _int(p, "fc_cnt"),
            "ex_cnt": _int(p, "ex_cnt"),
            "bar": _int_list(p, "mbar") or _int_list(p, "play_mbar") or [],
            "last_score": _int(p, "score"),
            "last_combo": int(score_node.get("combo", "0")),
            "timestamp": timestamp,
        }

        db.upsert(
            row,
            (where("jubeat_id") == int(jid))
            & (where("game_version") == game_version)
            & (where("music_id") == music_id)
            & (where("seq") == seq)
            & (where("hard") == hard),
        )

        profile["last"]["music_id"] = music_id
        profile["last"]["seq_id"] = seq


def update_fc_challenge(profile, result, player):
    """Full combo challenge: today's state from the tune results, the whim state as the game sends it."""
    tunes = []
    for tune in result.findall("tune") if result is not None else []:
        score_node = tune.find("player/score")
        if score_node is not None:
            tunes.append((_int(tune, "music", -1), int(score_node.get("seq", "0")), int(score_node.get("clear", "0"))))
    whim = player.find("fc_challenge/whim") if player is not None else None
    if whim is not None:
        fc_challenge_result(profile, tunes, _int(whim, "music_id"), _int(whim, "state"))
    else:
        fc_challenge_result(profile, tunes)


# ====================================================================================================
# recommend: songs for this player  (was recommend_ave2.py)
# ====================================================================================================
# get_recommend (from jubeat.dll, sender sub_10171980 / receiver sub_1019DDC0):
#   req: data/retry(s32) data/player/jid(s32)
#        data/player/music_list/music(@order){music_id s32, seq s8}   the tunes of this credit so far (max 3)
#   res: data/player/music_list/music(@order){music_id s32, seq s8}   max 12, seq 0..2
# The recommend screen puts the songs on the first 12 panels (4 x 3) and the player picks one to
# play. The game drops songs the player cannot play; when nothing is left (or the list is empty)
# it falls back to 12 songs that are hard coded in the game.
RECOMMEND_SIZE = 12
RECOMMEND_RETRY = 3  # songs the player has played but not cleared / scored lowest on
RECOMMEND_POPULAR = 3  # most played songs of this server the player has not tried
# "around my level": chart level within +- this of what the player plays. Levels below 9 are
# whole numbers, 9 and 10 have decimals (9.0 .. 10.9), so the high levels get a narrower band.
RECOMMEND_LEVEL_RANGE = 1.0
RECOMMEND_LEVEL_RANGE_HIGH = 0.5

CLEAR_FLAG_CLEARED = 2


def _closest_chart(game_version, music_id, level, preferred_seq):
    """The chart of a song whose level is closest to `level` (the preferred chart wins a tie)."""
    charts = [(abs(ave2_level(game_version, music_id, seq) - level), seq != preferred_seq, seq) for seq in (0, 1, 2) if ave2_level(game_version, music_id, seq) > 0]
    return min(charts)[2] if charts else preferred_seq


def recommend(game_version, jid, played, today=None):
    """Up to 12 (music_id, seq). Changes every day and stays the same within it.

    played: [(music_id, seq)] of the current credit, oldest first.
    """
    today = today or time.strftime("%Y%m%d")
    rng = random.Random(f"{today}-{jid}")
    history = ave2_player_history(jid, game_version)
    level, seq = ave2_player_level(jid, game_version, played, history)

    this_credit = {m for m, _ in played}
    known = {row["music_id"] for row in history} | this_credit
    picks = []

    def add(music_id, chart):
        if len(picks) < RECOMMEND_SIZE and music_id in music_db(game_version) and music_id not in this_credit and all(music_id != m for m, _ in picks):
            picks.append((music_id, chart))

    # 1) try again: not cleared yet, then the lowest scores; a different handful every day
    retry = sorted(
        (row for row in history if abs(ave2_level(game_version, row["music_id"], row["seq"]) - level) <= 1.5),
        key=lambda row: (bool(row.get("clear", 0) & CLEAR_FLAG_CLEARED), row.get("score", 0)),
    )[:10]
    for row in rng.sample(retry, min(RECOMMEND_RETRY, len(retry))):
        add(row["music_id"], row["seq"])

    # 2) what everybody plays here and this player has not tried
    plays = ave2_play_counts(game_version)
    popular = [m for m in sorted(plays, key=lambda m: -plays[m]) if m not in known and plays[m] > 0]
    for music_id in popular[:RECOMMEND_POPULAR]:
        add(music_id, _closest_chart(game_version, music_id, level, seq))

    # 3) new to the player, around the level they play
    around = []
    for music_id in music_db(game_version):
        if music_id in known:
            continue
        chart = _closest_chart(game_version, music_id, level, seq)
        if abs(ave2_level(game_version, music_id, chart) - level) <= (RECOMMEND_LEVEL_RANGE_HIGH if level >= 9 else RECOMMEND_LEVEL_RANGE):
            around.append((music_id, chart))
    rng.shuffle(around)
    for music_id, chart in around:
        add(music_id, chart)

    # 4) still room (tiny song list, player who has played everything): the newest songs
    for music_id in ave2_newest_first(game_version):
        add(music_id, _closest_chart(game_version, music_id, level, seq))
    return picks


# ====================================================================================================
# demodata: attract screen data  (was demodata_ave2.py)
# ====================================================================================================
# get_hitchart (from jubeat.dll sub_1019BD30): the request is empty, the response is
#   data/update(str, max 19 chars)
#   data/hitchart_lic(@count) and data/hitchart_org(@count), each with up to 10
#   rankdata{music_id s32, rank s16 1..10, prev s16}
# The game keeps the two top 10 lists and fills the HIT CHART folder of the music select
# with them (licensed first, then originals, only songs the player can play). More than 10
# rankdata per list would overflow the game's buffer.
HITCHART_SIZE = 10


def hitchart(game_version):
    """Top songs by play count over every player; ranks nobody played yet go to the newest songs.

    Which songs are licensed / KONAMI originals comes from data/<game version>/music_db.json;
    without that file the hit chart stays empty.
    """
    plays = ave2_play_counts(game_version)  # cached for a few minutes
    charts = {}
    for kind, original in (("licensed", False), ("original", True)):
        newest_first = ave2_newest_first(game_version, original)
        age = {music_id: index for index, music_id in enumerate(newest_first)}
        played = sorted((m for m in plays if m in age and plays[m] > 0), key=lambda m: (-plays[m], age[m]))
        charts[kind] = (played + [m for m in newest_first if m not in plays])[:HITCHART_SIZE]
    return charts


def _hitchart_node(name, music_ids):
    return E(
        name,
        *[
            E.rankdata(
                E.music_id(music_id, __type="s32"),
                E.rank(rank, __type="s16"),
                E.prev(rank, __type="s16"),  # parsed but not used by the game
            )
            for rank, music_id in enumerate(music_ids, 1)
        ],
        count=str(len(music_ids)),
    )


# ====================================================================================================
# lobby: online matching  (was lobby_ave2.py)
# ====================================================================================================
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


def _lobby_text(node, path, default=""):
    found = node.find(path) if node is not None else None
    if found is None or found.text is None:
        return default
    return found.text


def _lobby_int(node, path, default=0):
    try:
        return int(_lobby_text(node, path))
    except ValueError:
        return default


def _lobby_int_list(node, path):
    return [int(v) for v in _lobby_text(node, path).split() if v.strip()]


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
    return E.data(
        E.roomid(room["roomid"], __type="s64", master="1"),
        E.refresh_intr(LOBBY_REFRESH_INTR, __type="s16"),
        _music_node(room["mid"], room["seq"]),
        E.random_seed(room["random_seed"], __type="u32"),
    )


def _guest_response(room, music=None):
    # music: (id, seq) the guest plays instead of the room's song
    mid, seq = music or (room["mid"], room["seq"])
    return E.data(
        E.roomid(room["roomid"], __type="s64", master="0"),
        E.connect(
            E.key(room["key"], __type="u8"),
            E("global", room["global"], __type="str"),
            E.private(room["private"], __type="str"),
        ),
        _music_node(mid, seq),
        E.random_seed(room["random_seed"], __type="u32"),
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

    return E.data(
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
    )


def lobby_entry(pcbid, game_version, data):
    now = int(time.time())
    _prune_rooms(now)

    mid = _lobby_int(data, "music/id")
    seq = _lobby_int(data, "music/seq")
    level = _lobby_int(data, "music/level")
    # random/level is only present in random matching
    random_level = _lobby_int(data, "random/level", -1) if data.find("random/level") is not None else None
    is_random = random_level is not None
    all_music = _lobby_text(data, "all_music_matching/is_available", "1") not in ("0", "false")
    key = _lobby_int_list(data, "connect/key")
    glob = _lobby_text(data, "connect/global", "0.0.0.0:0")
    priv = _lobby_text(data, "connect/private", "0.0.0.0:0")
    exception = set(_lobby_int_list(data, "exception"))
    lkey = _lobby_int_list(data, "local_matching/key")
    local_cnt = max(_lobby_int(data, "local_matching/count", 1), 1)
    # The game prints its 4 digit password with "%d", so 0401 arrives as "401".
    password = _lobby_text(data, "matching_password").strip()
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
    room = rooms.get(_lobby_int(data, "roomid"))
    if room is not None and room["owner"] == pcbid:
        room["time"] = now
        if time.time() >= room["start_at"]:
            room["started"] = start = True

    return E.data(
        E.refresh_intr(LOBBY_REFRESH_INTR, __type="s16"),
        E.start(start, __type="bool"),
    )


def lobby_report(pcbid, data):
    roomid = _lobby_int(data, "roomid")
    room = rooms.get(roomid)
    if room is not None and room["owner"] == pcbid:
        del rooms[roomid]

    return E.data(
        E.refresh_intr(LOBBY_REFRESH_INTR, __type="s16"),
    )


def _pcbid(request_info, request=None):
    """What tells the cabinets of the lobby apart: the srcid of the request (the PCBID). Cabinets left on the
    ea3-config default PCBID (0100...0) all send the same one, and then the second cabinet's entry looked like the
    first one's re-entry: both were made master of one room and never connected (festo local matching,
    2026-10-14). Such an id gets the cabinet's address appended, which is stable for the few minutes a room lives."""
    srcid = request_info["root"].attrib.get("srcid", "")
    if srcid.strip("0") in ("", "1") and request is not None and request.client is not None:
        return f"{srcid}@{request.client.host}"
    return srcid


# ====================================================================================================
# logger  (was logger_ave2.py)
# ====================================================================================================


# ====================================================================================================
# the handlers
# ====================================================================================================
class JubeatGame:
    """The handlers of one game version: <module>_<method> answers the request <module>_<suffix>.<method>, where the
    suffix is MODULE_SUFFIX ("ave2" for beyond the Ave, "" for festo whose modules have none).

    What a version answers differently (its <info>, its own part of the player data) is a method below that its
    subclass overrides: JubeatFestoGame in festo.py. Everything else is the same for every version."""

    MODULE_SUFFIX = "ave2"

    def __init__(self, lightchat_maps, module_suffix=None):
        self.lightchat_maps = lightchat_maps  # see beyondtheave.py
        if module_suffix is not None:
            self.MODULE_SUFFIX = module_suffix

    def module(self, name, *children, **attributes):
        """The root node of an answer: <name>_<suffix>, or <name> for a version without a suffix."""
        return E(f"{name}_{self.MODULE_SUFFIX}" if self.MODULE_SUFFIX else name, *children, **attributes)

    # ---- what a version answers differently; beyond the Ave's answers are the defaults ----
    def global_info(self, game_version):
        """The <info> node of shopinfo.regist, gametop.get_info and the player data."""
        return jubeat_ave2_global_info(self.lightchat_maps, game_version)

    def shop_nodes(self):
        """Children of shopinfo.regist's data besides cabid, locationid, facility and info."""
        return []

    def player_event_nodes(self, profile):
        """The children of the player data's event_info."""
        return []

    def player_nodes(self, profile):
        """The version's own children of the player data, after the shared ones (format_profile)."""
        return [_lightchat_node(profile["lightchat"], self.lightchat_maps)]

    def save_player_nodes(self, profile, player):
        """The version's own part of gameend.regist's data/player, into the profile (gameend_regist saves it)."""
        update_lightchat(profile, player.find("lightchat"))

    async def shopinfo_regist(self, request):
        request_info = await core_process_request(request)

        response = E.response(
            self.module("shopinfo",
                E.data(
                    E.cabid(1, __type="u32"),
                    E.locationid("EA000001", __type="str"),
                    E.facility(
                        E.exist(1, __type="u32"),
                    ),
                    *self.shop_nodes(),
                    self.global_info(request_info["game_version"]),
                ),
            )
        )

        response_body, response_headers = await core_prepare_response(request, response)
        return Response(content=response_body, headers=response_headers)

    async def gametop_get_info(self, request):
        request_info = await core_process_request(request)

        response = E.response(
            self.module("gametop",
                E.data(
                    self.global_info(request_info["game_version"]),
                ),
            )
        )

        response_body, response_headers = await core_prepare_response(request, response)
        return Response(content=response_body, headers=response_headers)

    async def gametop_regist(self, request):
        request_info = await core_process_request(request)
        game_version = request_info["game_version"]

        player = request_info["root"][0].find("data/player")
        refid = player.find("refid").text
        name_node = player.find("name")
        name = name_node.text if name_node is not None and name_node.text else "なし"

        db = get_db().table("jubeat_profile")
        all_profiles_for_card = db.get(where("card") == refid)

        if all_profiles_for_card is None:
            all_profiles_for_card = {"card": refid, "version": {}}

        if "jubeat_id" not in all_profiles_for_card:
            all_profiles_for_card["jubeat_id"] = random.randint(10000000, 99999999)

        all_profiles_for_card["version"][str(game_version)] = new_game_profile(game_version, name)
        fc_challenge_refresh(all_profiles_for_card["version"][str(game_version)], all_profiles_for_card["jubeat_id"], game_version)

        db.upsert(all_profiles_for_card, where("card") == refid)

        response = format_profile(self, game_version, all_profiles_for_card["jubeat_id"], all_profiles_for_card["version"][str(game_version)])

        response_body, response_headers = await core_prepare_response(request, response)
        return Response(content=response_body, headers=response_headers)

    async def gametop_get_pdata(self, request):
        request_info = await core_process_request(request)
        game_version = request_info["game_version"]

        refid = request_info["root"][0].find("data/player/refid").text

        profile = get_game_profile(refid, game_version)
        card = get_profile(refid)
        if profile is None and card is not None and card.get("version") and "jubeat_id" in card:
            # The card has played another jubeat version: cardmng said it is registered, so the game expects its data
            # here and takes 109 as an error ("eamuse_gametop_get_pdata failed!!(r=7)"); only a card the game has never
            # seen goes to regist by itself. This version's profile starts now, with the name of the newest one.
            profile = start_version_profile(card, game_version)

        if profile is None:
            # 109 = NO_PROFILE, game falls back to gametop.regist
            response = E.response(self.module("gametop", status=109))
        else:
            if fc_challenge_refresh(profile, card["jubeat_id"], game_version):
                # a new day: keep the new challenge so gameend can update the same songs
                card["version"][str(game_version)] = profile
                get_db().table("jubeat_profile").upsert(card, where("card") == refid)
            response = format_profile(self, game_version, card["jubeat_id"], profile)

        response_body, response_headers = await core_prepare_response(request, response)
        return Response(content=response_body, headers=response_headers)

    async def gametop_get_mdata(self, request):
        request_info = await core_process_request(request)
        game_version = request_info["game_version"]

        player = request_info["root"][0].find("data/player")
        jid = int(player.find("jid").text)
        mdata_ver_node = player.find("mdata_ver")
        mdata_ver = int(mdata_ver_node.text) if mdata_ver_node is not None and mdata_ver_node.text else 1

        response = format_scores(self, jid, game_version, mdata_ver)

        response_body, response_headers = await core_prepare_response(request, response)
        return Response(content=response_body, headers=response_headers)

    async def gametop_get_meeting(self, request):
        request_info = await core_process_request(request)

        response = E.response(
            self.module("gametop",
                E.data(
                    E.meeting(
                        E.single(count=0),
                    ),
                    E.reward(
                        E.total(0, __type="s32"),
                        E.point(0, __type="s32"),
                    ),
                ),
            )
        )

        response_body, response_headers = await core_prepare_response(request, response)
        return Response(content=response_body, headers=response_headers)

    async def gameend_regist(self, request):
        request_info = await core_process_request(request)
        game_version = request_info["game_version"]

        data = request_info["root"][0].find("data")
        info = data.find("info") if data is not None else None
        result = data.find("result") if data is not None else None
        player = data.find("player") if data is not None else None

        refid = _text(player, "refid")
        profile = get_game_profile(refid, game_version) if refid else None

        if profile is not None:
            card = get_profile(refid)
            jid = card.get("jubeat_id", _int(player, "jid"))
            update_profile_from_regist(profile, info, player)
            self.save_player_nodes(profile, player)
            save_scores(jid, game_version, result, profile)
            update_fc_challenge(profile, result, player)
            _save_profile(refid, game_version, profile)

        response = E.response(
            self.module("gameend",
                E.data(
                    E.player(
                        E.session_id(1, __type="s32"),
                        E.end_final_session_id(1, __type="s32"),
                    ),
                ),
            )
        )

        response_body, response_headers = await core_prepare_response(request, response)
        return Response(content=response_body, headers=response_headers)

    async def gameend_final(self, request):
        request_info = await core_process_request(request)
        game_version = request_info["game_version"]

        data = request_info["root"][0].find("data")
        player = data.find("player") if data is not None else None

        refid = _text(player, "refid")
        profile = get_game_profile(refid, game_version) if refid else None

        if profile is not None:
            update_profile_from_final(profile, player)
            _save_profile(refid, game_version, profile)

        response = E.response(
            self.module("gameend"),
        )

        response_body, response_headers = await core_prepare_response(request, response)
        return Response(content=response_body, headers=response_headers)

    async def recommend_get_recommend(self, request):
        request_info = await core_process_request(request)

        player = request_info["root"][0].find("data/player")
        jid, played = 0, []
        if player is not None:
            try:
                jid = int(player.findtext("jid") or 0)
            except ValueError:
                jid = 0
            for music in player.findall("music_list/music"):
                try:
                    played.append((int(music.get("order") or 0), int(music.findtext("music_id") or 0), int(music.findtext("seq") or 0)))
                except ValueError:
                    continue
        played = [(music_id, seq) for _, music_id, seq in sorted(played)]

        picks = recommend(request_info["game_version"], jid, played)
        response = E.response(
            self.module("recommend",
                E.data(
                    E.player(
                        E.music_list(
                            *[
                                E.music(
                                    E.music_id(music_id, __type="s32"),
                                    E.seq(seq, __type="s8"),
                                    order=str(order),
                                )
                                for order, (music_id, seq) in enumerate(picks)
                            ]
                        ),
                    ),
                ),
            )
        )

        response_body, response_headers = await core_prepare_response(request, response)
        return Response(content=response_body, headers=response_headers)

    async def demodata_get_info(self, request):
        request_info = await core_process_request(request)

        response = E.response(
            self.module("demodata",
                E.data(
                    E.info(
                        E.black_jacket_list([0] * 64, __type="s32"),
                    ),
                ),
            )
        )

        response_body, response_headers = await core_prepare_response(request, response)
        return Response(content=response_body, headers=response_headers)

    async def demodata_get_jbox_list(self, request):
        request_info = await core_process_request(request)

        response = E.response(
            self.module("demodata",
                E.selection_list(),
            )
        )

        response_body, response_headers = await core_prepare_response(request, response)
        return Response(content=response_body, headers=response_headers)

    async def demodata_get_news(self, request):
        request_info = await core_process_request(request)

        response = E.response(
            self.module("demodata",
                E.data(
                    E.officialnews(count=0),
                )
            ),
        )

        response_body, response_headers = await core_prepare_response(request, response)
        return Response(content=response_body, headers=response_headers)

    async def demodata_get_hitchart(self, request):
        request_info = await core_process_request(request)

        charts = hitchart(request_info["game_version"])
        response = E.response(
            self.module("demodata",
                E.data(
                    E.update(time.strftime("%Y/%m/%d %H:%M"), __type="str"),
                    _hitchart_node("hitchart_lic", charts["licensed"]),
                    _hitchart_node("hitchart_org", charts["original"]),
                )
            ),
        )

        response_body, response_headers = await core_prepare_response(request, response)
        return Response(content=response_body, headers=response_headers)

    async def lobby_check(self, request):
        request_info = await core_process_request(request)

        response = E.response(self.module("lobby", lobby_check(_pcbid(request_info, request), request_info["game_version"])))

        response_body, response_headers = await core_prepare_response(request, response)
        return Response(content=response_body, headers=response_headers)

    async def lobby_entry(self, request):
        request_info = await core_process_request(request)

        data = request_info["root"][0].find("data")
        response = E.response(self.module("lobby", lobby_entry(_pcbid(request_info, request), request_info["game_version"], data)))

        response_body, response_headers = await core_prepare_response(request, response)
        return Response(content=response_body, headers=response_headers)

    async def lobby_refresh(self, request):
        request_info = await core_process_request(request)

        data = request_info["root"][0].find("data")
        response = E.response(self.module("lobby", lobby_refresh(_pcbid(request_info, request), data)))

        response_body, response_headers = await core_prepare_response(request, response)
        return Response(content=response_body, headers=response_headers)

    async def lobby_report(self, request):
        request_info = await core_process_request(request)

        data = request_info["root"][0].find("data")
        response = E.response(self.module("lobby", lobby_report(_pcbid(request_info, request), data)))

        response_body, response_headers = await core_prepare_response(request, response)
        return Response(content=response_body, headers=response_headers)

    # logger_ave2.report (from jubeat.dll, sender sub_10171410 / receiver sub_101714A0):
    #   req: retry(s32) data/code(str) data/information(str)   the game reporting an error or an odd state
    #   res: nothing is read, any well formed response ends the request; without a handler the game
    #        gets a 404 and retries the report.
    # The reports are printed so they show up next to the request log.
    async def logger_report(self, request):
        request_info = await core_process_request(request)

        root = request_info["root"]
        data = root[0].find("data")
        code = (data.findtext("code") if data is not None else "") or ""
        information = (data.findtext("information") if data is not None else "") or ""
        print(f"{time.strftime('%H:%M:%S')} jubeat logger_ave2.report from {root.attrib.get('srcid', '?')}: code={code!r} information={information!r}")

        response = E.response(self.module("logger"))

        response_body, response_headers = await core_prepare_response(request, response)
        return Response(content=response_body, headers=response_headers)

    # jbox_ave2: the JBOX, emblem boxes bought with PASELI (menu on the mode select). From jubeat.dll, module table
    # off_102E95E8 = (request builder / response reader) per method, all on the service "local":
    #   get_list       sub_1016DCE0 / sub_1016E350 + sub_1017CC70   req: data/player/jid(s32)
    #   confirm        sub_1016D830 / sub_1016D980   req: data/player{jid jbox@id}   res: transaction_id(str) insert_date(s64)
    #   buy            sub_1016D320 / sub_1016D440   req: data/player{jid jbox@transaction_id jbox/insert_date(s64)}
    #                                                res: emblem_list/emblem@index (the pieces won)
    #   get_image      sub_1016E080 / sub_1016E0E0   the pictures of a selection, cached under /dev/raw
    #   get_agreement  sub_1016DCE0 / sub_1016DDC0   req: data/player/jid(s32)   res: is_agreement(bool)
    #   set_agreement  sub_1016E700                  req: data/player{jid is_agreement(bool)}
    # What the game reads from get_list (up to 4 selections of up to 6 targets):
    #   selection_list/selection@id{name(str, up to 36) image_id(s32) etime(s64)
    #       target_list/target@id@status{appearlist emblemlist definedlist (s32 x96: 3072 bit flags of emblem pieces)
    #                                    rarity rarity_special (s32 x5) is_defined(bool)}}
    #   is_cache_clear(bool, optional)
    # The boxes and their pictures come from KONAMI and there is no data for them here, so the list is empty. Only
    # get_list and get_agreement are answered; the four others still get a 404.
    async def jbox_get_list(self, request):
        request_info = await core_process_request(request)

        response = E.response(
            self.module("jbox",
                E.selection_list(),
            )
        )

        response_body, response_headers = await core_prepare_response(request, response)
        return Response(content=response_body, headers=response_headers)

    # is_agreement 0 makes the game show the JBOX terms and call set_agreement (not implemented), so it is always 1.
    async def jbox_get_agreement(self, request):
        request_info = await core_process_request(request)

        response = E.response(
            self.module("jbox",
                E.is_agreement(True, __type="bool"),
            )
        )

        response_body, response_headers = await core_prepare_response(request, response)
        return Response(content=response_body, headers=response_headers)

    # lab.get_ranking (festo's jubeat lab, player made charts; service "slocal"; jubeat.dll 2022-05-24 sender sub_10158B40 /
    # receiver sub_10158BA0): req: retry(s32) category(s8)   res: category(s8) entries(@count, up to 10)/entry/seq_id(str)
    # There are no lab charts here, so the ranking is empty; the other lab methods (get_fumen, set_music_result) get a 404.
    async def lab_get_ranking(self, request):
        request_info = await core_process_request(request)

        response = E.response(
            self.module(
                "lab",
                E.category(_int(request_info["root"][0], "category"), __type="s8"),
                E.entries(count="0"),
            )
        )

        response_body, response_headers = await core_prepare_response(request, response)
        return Response(content=response_body, headers=response_headers)

    # ins.netlog (service "netlog"): avs2-ea3's netlog appender posts log lines here when ea3-config switches it on
    # (netlog_appender_boot); nothing of the answer is read. Offered so that the service exists: bemaniutils found festo
    # dying at coin-in without it.
    async def ins_netlog(self, request):
        request_info = await core_process_request(request)

        response = E.response(E.ins())

        response_body, response_headers = await core_prepare_response(request, response)
        return Response(content=response_body, headers=response_headers)
