import time

from tinydb import where

from fastapi import APIRouter, Request, Response

from core_common import core_process_request, core_prepare_response, E
from core_database import get_db

# reflecbeat.dll: info, player, pcb and shop are CXrpcModules on "local2", lobby on "lobby2".
router = APIRouter(prefix="/local2", tags=["local2"])
router.model_whitelist = ["MBR"]

# REFLEC BEAT VOLZZA 2 (MBR-2016100400) "info" module, ported from bemaniutils
# volzzabase.py. url_slash 0 forwarder looks up "<module>_<method>" so the
# handler names below must stay info_rb5_<method>.

SECONDS_IN_DAY = 86400


def _int(node, tag, default=0):
    found = node.find(tag) if node is not None else None
    try:
        return int(found.text)
    except (AttributeError, TypeError, ValueError):
        return default


async def _respond(request, response):
    response_body, response_headers = await core_prepare_response(request, response)
    return Response(content=response_body, headers=response_headers)


@router.post("/{gameinfo}/info/rb5_info_read")
async def info_rb5_info_read(request: Request):
    request_info = await core_process_request(request)

    # Same event/lock/mycourse block as player_start (see player.event_info_nodes).
    from modules.reflec.player import event_info_nodes

    response = E.response(E.info(*event_info_nodes()))
    return await _respond(request, response)


@router.post("/{gameinfo}/info/rb5_info_read_hit_chart")
async def info_rb5_info_read_hit_chart(request: Request):
    request_info = await core_process_request(request)
    root = request_info["root"][0]
    ver = _int(root, "ver")
    now = int(time.time())

    def hitchart(days):
        counts = {}
        for row in get_db().table("reflec_scores_best").search(
            where("timestamp") >= now - days * SECONDS_IN_DAY
        ):
            counts[row["music_id"]] = counts.get(row["music_id"], 0) + row.get("cnt", 1)
        return sorted(counts.items(), key=lambda kv: -kv[1])[:1024]

    def ranking(name, days):
        return E(
            name,
            E.bt(now - days * SECONDS_IN_DAY, __type="s32"),
            E.et(now, __type="s32"),
            E.new(
                *[
                    E.d(E.mid(mid, __type="s16"), E.cnt(cnt, __type="s32"))
                    for mid, cnt in hitchart(days)
                ]
            ),
        )

    response = E.response(
        E.info(
            E.ver(ver, __type="s32"),
            E.ranking(
                ranking("weekly", 7),
                ranking("monthly", 30),
                ranking("total", 365),
            ),
        )
    )
    return await _respond(request, response)


@router.post("/{gameinfo}/info/rb5_info_read_shop_ranking")
async def info_rb5_info_read_shop_ranking(request: Request):
    request_info = await core_process_request(request)
    root = request_info["root"][0]
    start = _int(root, "min")
    end = _int(root, "max")

    rows = get_db().table("reflec_scores_best").search(
        (where("music_id") >= start) & (where("music_id") <= end)
    )
    names = {}
    for card in get_db().table("reflec_profile").all():
        if "reflec_id" in card:
            prof = next(iter(card.get("version", {}).values()), {})
            names[card["reflec_id"]] = (prof.get("name", ""), prof.get("icon_id", 0))

    entries = []
    by_chart = {}
    for row in rows:
        by_chart.setdefault((row["music_id"], row["note_grade"]), []).append(row)
    for (music_id, note_grade), chart_rows in sorted(by_chart.items()):
        chart_rows.sort(key=lambda r: -r["score"])
        for rank, row in enumerate(chart_rows, start=1):
            name, icon_id = names.get(row["reflec_id"], ("", 0))
            entries.append(
                E.data(
                    E.rank(rank, __type="s32"),
                    E.music_id(music_id, __type="s16"),
                    E.note_grade(note_grade, __type="s8"),
                    E.clear_type(row.get("clear_type", 0), __type="s8"),
                    E.user_id(row["reflec_id"], __type="s32"),
                    E.icon_id(icon_id, __type="s16"),
                    E.score(row["score"], __type="s32"),
                    E.time(row.get("timestamp", 0), __type="s32"),
                    E.name(name, __type="str"),
                )
            )

    response = E.response(
        E.info(
            E.shop_score(
                E.time(int(time.time()), __type="s32"),
                *entries,
            ),
        )
    )
    return await _respond(request, response)
