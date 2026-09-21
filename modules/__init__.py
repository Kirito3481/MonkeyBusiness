import inspect
from glob import glob
from importlib import util
from os import path
from typing import Optional

from fastapi import APIRouter, Request, Response

# Every .py file below this folder is executed once to collect what it offers:
#   routers   the file's `router`, for url_slash 1 (pyeamu mounts them and builds services.get from them)
#   handlers  the file's public coroutines by lower-case name, for url_slash 0 (see forward_slashless)
# The files run as anonymous modules, NOT as `modules.<game>.<file>`: a `from modules.x.y import z`
# elsewhere therefore imports a second copy of that file. State that two files share has to live in
# a module that is only ever reached through such an import (see modules/reflec/state.py).
routers = []
handlers = {}

for module_path in glob(path.join(path.dirname(__file__), "**/*.py"), recursive=True):
    if path.basename(module_path) == "__init__.py":
        continue

    spec = util.spec_from_file_location("", module_path)
    module = util.module_from_spec(spec)
    spec.loader.exec_module(module)

    if getattr(module, "router", None) is not None:
        routers.append(module.router)

    if path.basename(module_path) == "api.py":
        continue  # web api of a game, never requested by a cabinet
    for name, obj in vars(module).items():
        # only what the file defines itself: a coroutine it imported belongs to another copy
        if name.startswith("_") or not inspect.iscoroutinefunction(obj) or obj.__module__ != module.__name__:
            continue
        previous = handlers.get(name.lower())
        if previous is not None:
            print(f"url_slash 0: handler {name} of {inspect.getsourcefile(previous)} is replaced by {module_path}")
        handlers[name.lower()] = obj


# With url_slash 0 a game posts everything to /fwdr?model=..&f=<module>.<method> and the handler is
# found by name. Most handlers are simply called <module>_<method>. The games below differ, each
# entry turns (module, method) into (handler name, arguments passed before the request):
def _ddr(module, method):
    # only DDR's eventlog modules share their name with other games
    return (f"ddr_{module}_{method}", ()) if module.startswith("eventlo") else None


def _sdvx(module, method):
    if module == "eventlog":
        return f"sdvx_{module}_{method}", ()
    # game.sv6_common -> game_sv_common("6", request): one handler serves every version
    version = "".join(c for c in method if c.isdigit())
    return f"{module}_{''.join(c for c in method if not c.isdigit())}", (version,)


def _gitadora(module, method):
    if module == "lobby":
        return f"gitadora_{module}_{method}", ()
    # bh_gametop.get -> gitadora_gametop_get("bh", request): the module carries the cabinet type
    parts = module.split("_")
    return f"gitadora_{parts[-1]}_{method}", (parts[0],)


GAME_HANDLER_NAMES = {
    "MDX": _ddr,
    "REC": lambda module, method: (f"drs_{module}_{method}", ()),
    "KFC": _sdvx,
    "M32": _gitadora,
    "PIX": lambda module, method: (f"museca_{module}_{method}", ()),
}


def find_handler(game_code, module, method):
    """The handler for <module>.<method> of a game and the arguments that go before the request, or None."""
    candidates = [(f"{module}_{method}", ())]
    if game_code in GAME_HANDLER_NAMES:
        candidates.append(GAME_HANDLER_NAMES[game_code](module, method))
    for candidate in candidates:
        if candidate is not None and candidate[0].lower() in handlers:
            return handlers[candidate[0].lower()], candidate[1]
    return None


router = APIRouter(tags=["slashless_forwarder"])


@router.post("/fwdr")
async def forward_slashless(
    request: Request,
    model: Optional[str] = None,
    f: Optional[str] = None,
    module: Optional[str] = None,
    method: Optional[str] = None,
):
    if f is not None:
        module, _, method = f.partition(".")
    game_code = (model or "").split(":")[0]

    found = find_handler(game_code, module or "", method or "")
    if found is None:
        print(f"url_slash 0: no handler for {module}.{method} ({game_code or 'no model'}). Try URL Slash 1 (On) if this game is supported.")
        return Response(status_code=404)
    handler, arguments = found
    return await handler(*arguments, request)


routers.append(router)
