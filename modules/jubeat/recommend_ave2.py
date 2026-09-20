import random
import time

from fastapi import APIRouter, Request, Response

from core_common import core_process_request, core_prepare_response, E
from modules.jubeat.musicdb_ave2 import (
    JUBEAT_AVE2_MUSIC,
    ave2_level,
    ave2_newest_first,
    ave2_play_counts,
    ave2_player_history,
    ave2_player_level,
)

router = APIRouter(prefix="/local", tags=["local"])
router.model_whitelist = ["L44"]

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


def _closest_chart(music_id, level, preferred_seq):
    """The chart of a song whose level is closest to `level` (the preferred chart wins a tie)."""
    charts = [(abs(ave2_level(music_id, seq) - level), seq != preferred_seq, seq) for seq in (0, 1, 2) if ave2_level(music_id, seq) > 0]
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
        if len(picks) < RECOMMEND_SIZE and music_id in JUBEAT_AVE2_MUSIC and music_id not in this_credit and all(music_id != m for m, _ in picks):
            picks.append((music_id, chart))

    # 1) try again: not cleared yet, then the lowest scores; a different handful every day
    retry = sorted(
        (row for row in history if abs(ave2_level(row["music_id"], row["seq"]) - level) <= 1.5),
        key=lambda row: (bool(row.get("clear", 0) & CLEAR_FLAG_CLEARED), row.get("score", 0)),
    )[:10]
    for row in rng.sample(retry, min(RECOMMEND_RETRY, len(retry))):
        add(row["music_id"], row["seq"])

    # 2) what everybody plays here and this player has not tried
    plays = ave2_play_counts(game_version)
    popular = [m for m in sorted(plays, key=lambda m: -plays[m]) if m not in known and plays[m] > 0]
    for music_id in popular[:RECOMMEND_POPULAR]:
        add(music_id, _closest_chart(music_id, level, seq))

    # 3) new to the player, around the level they play
    around = []
    for music_id in JUBEAT_AVE2_MUSIC:
        if music_id in known:
            continue
        chart = _closest_chart(music_id, level, seq)
        if abs(ave2_level(music_id, chart) - level) <= (RECOMMEND_LEVEL_RANGE_HIGH if level >= 9 else RECOMMEND_LEVEL_RANGE):
            around.append((music_id, chart))
    rng.shuffle(around)
    for music_id, chart in around:
        add(music_id, chart)

    # 4) still room (tiny song list, player who has played everything): the newest songs
    for music_id in ave2_newest_first():
        add(music_id, _closest_chart(music_id, level, seq))
    return picks


@router.post("/{gameinfo}/recommend_ave2/get_recommend")
async def recommend_ave2_get_recommend(request: Request):
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
        E.recommend_ave2(
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
