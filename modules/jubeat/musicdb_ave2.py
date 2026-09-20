"""Song data of jubeat beyond the Ave for the modules that need more than a music id.

music_db_ave2.json is made from the game's music_info.xml by jubeat_l44_music_db.py:
    {"<music id>": [debut version, 1 = KONAMI original / 0 = licensed, level bsc, adv, ext]}
Without the file every list here is empty and the features built on it stay empty too.
"""

import json
import os
import statistics
import time

from tinydb import where

from core_database import get_db

try:
    with open(os.path.join(os.path.dirname(os.path.abspath(__file__)), "music_db_ave2.json"), encoding="utf-8") as _f:
        JUBEAT_AVE2_MUSIC = {int(music_id): song for music_id, song in json.load(_f).items()}
except (OSError, ValueError):
    JUBEAT_AVE2_MUSIC = {}

PLAY_COUNT_CACHE_SECONDS = 600
NEW_PLAYER_LEVEL = 3.0  # chart level assumed for somebody without any history
_play_count_cache = {}  # game_version -> (time, {music_id: plays})


def ave2_newest_first(original=None):
    """Music ids, newest version first; original=True/False keeps only originals / licensed songs."""
    songs = [
        (song[0], music_id)
        for music_id, song in JUBEAT_AVE2_MUSIC.items()
        if original is None or bool(song[1]) == original
    ]
    return [music_id for _, music_id in sorted(songs, reverse=True)]


def ave2_level(music_id, seq):
    song = JUBEAT_AVE2_MUSIC.get(music_id)
    return song[2 + seq] if song is not None and 0 <= seq <= 2 else 0.0


def ave2_player_history(jid, game_version):
    """Best-score rows of a player for songs and charts the music database knows."""
    if not jid:
        return []
    rows = get_db().table("jubeat_scores_best").search((where("jubeat_id") == int(jid)) & (where("game_version") == game_version))
    return [row for row in rows if row.get("music_id") in JUBEAT_AVE2_MUSIC and row.get("seq") in (0, 1, 2)]


def ave2_player_level(jid, game_version, played=(), history=None):
    """(chart level, chart) the player plays: the tunes of this credit, else the last ten charts played."""
    if history is None:
        history = ave2_player_history(jid, game_version)
    recent = sorted(history, key=lambda row: row.get("timestamp", 0))[-10:]
    charts = [(m, s) for m, s in played if m in JUBEAT_AVE2_MUSIC and s in (0, 1, 2)] or [(row["music_id"], row["seq"]) for row in recent]
    levels = [ave2_level(m, s) for m, s in charts if ave2_level(m, s) > 0]
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
