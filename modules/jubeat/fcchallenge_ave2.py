"""FULL COMBO CHALLENGE of jubeat beyond the Ave (data/player/fc_challenge in gametop / gameend).

From jubeat.dll (profile parser sub_100ED0F0, result handling sub_100A6C80, gameend sender
sub_100F47E0):
  gametop sends  fc_challenge/today{music_id s32, state u8}  (required)
                 fc_challenge/whim {music_id s32, state u8}  (optional)
  A music_id the game knows becomes a challenge song: it is shown on the information screen
  after login, playing it shows the challenge mark until the first miss, and a full combo
  completes it. "today" is the same song for everybody, "whim" is a personal one that can be
  tried once (the game gives it one attempt as long as state has no 0x40).
  state bits:  1 << (2 * seq) played on that chart      2 << (2 * seq) full combo on that chart
               0x40 played                               0x80 completed
  gameend sends fc_challenge/today{music_id} and fc_challenge/whim{music_id, state}, each only
  when that song was played in the credit. The state of "today" does not come back, so it is
  worked out here from the tune results.
Both songs change at midnight (server time). Needs music_db_ave2.json, without it the
challenge stays off (music_id 0).
"""

import random
import time

from modules.jubeat.musicdb_ave2 import JUBEAT_AVE2_MUSIC, ave2_level, ave2_player_level

FCC_WHIM_LEVEL_RANGE = 1.0  # the personal song has a chart within +- this of the level the player plays

CLEAR_FLAG_FULL_COMBO = 0x04
CLEAR_FLAG_EXCELLENT = 0x08
STATE_PLAYED = 0x40
STATE_COMPLETED = 0x80


def fc_challenge_today(today=None):
    """Today's challenge song, the same for every player."""
    songs = sorted(JUBEAT_AVE2_MUSIC)
    if not songs:
        return 0
    return random.Random(f"fcc-today-{today or time.strftime('%Y%m%d')}").choice(songs)


def _whim(jid, game_version, today, avoid):
    level, _ = ave2_player_level(jid, game_version)
    songs = [
        music_id
        for music_id in sorted(JUBEAT_AVE2_MUSIC)
        if music_id != avoid and any(abs(ave2_level(music_id, seq) - level) <= FCC_WHIM_LEVEL_RANGE for seq in (0, 1, 2))
    ] or [music_id for music_id in sorted(JUBEAT_AVE2_MUSIC) if music_id != avoid]
    return random.Random(f"fcc-whim-{today}-{jid}").choice(songs) if songs else 0


def fc_challenge_refresh(profile, jid, game_version, today=None):
    """Make profile["fc_challenge"] the challenge of today. Returns True when it changed (save it)."""
    today = today or time.strftime("%Y%m%d")
    current = profile.get("fc_challenge")
    if isinstance(current, dict) and current.get("date") == today:
        return False
    music_id = fc_challenge_today(today)
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
