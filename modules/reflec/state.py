# In-memory state shared by the Reflec Beat modules.
#
# modules/__init__.py executes every file as an anonymous module to collect the routers, and a
# `from modules.reflec.player import ...` elsewhere imports a second copy of that file. Objects
# defined in player.py or lobby.py therefore exist twice, and the handlers and the importer do
# not see the same dict. Everything here is reached through `modules.reflec.state` only
# (sys.modules keeps one copy), so both sides share it.

# refid -> {"plyid", "extid", "ga", "gp", "la", "pnid", "time"}; cleared on player_end / restart
play_sessions = {}

# eid -> lobby entry dict
lobbies = {}
