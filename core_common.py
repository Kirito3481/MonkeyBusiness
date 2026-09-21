import config

import time

from lxml.builder import ElementMaker

from kbinxml import KBinXML

from utils.arc4 import EamuseARC4
from utils.lz77 import lz77_decode, lz77_encode


def _add_val_as_str(elm, val):
    new_val = str(val)

    if elm is not None:
        elm.text = new_val

    else:
        return new_val


def _add_bool_as_str(elm, val):
    return _add_val_as_str(elm, 1 if val else 0)


def _add_list_as_str(elm, vals):
    new_val = " ".join([str(val) for val in vals])

    if elm is not None:
        elm.text = new_val
        elm.attrib["__count"] = str(len(vals))

    else:
        return new_val


def _prng():
    state = 0x41C64E6D
    while True:
        x = (state * 0x838C9CDA) + 0x6072
        # state = (state * 0x41C64E6D + 0x3039)
        # state = (state * 0x41C64E6D + 0x3039)
        state = (state * 0xC2A29A69 + 0xD3DC167E) & 0xFFFFFFFF
        yield (x & 0x7FFF0000) | state >> 0xF & 0xFFFF
prng_init = _prng()


E = ElementMaker(
    typemap={
        int: _add_val_as_str,
        bool: _add_bool_as_str,
        list: _add_list_as_str,
        float: _add_val_as_str,
    }
)


async def core_get_game_version_from_software_version(software_version):
    _, model, dest, spec, rev, ext = software_version
    ext = int(ext)

    if model == "LDJ":
        if ext >= 2025091700:
            return 33
        elif ext >= 2024100900:
            return 32
        elif ext >= 2023101800:
            return 31
        elif ext >= 2022101700:
            return 30
        elif ext >= 2021101300:
            return 29
        # TODO: Consolidate IIDX modules to easily support versions 21-28 (probably never)
        elif ext >= 2020102800:
            return 28
        elif ext >= 2019101600:
            return 27
        elif ext >= 2018110700:
            return 26
        elif ext >= 2017122100:
            return 25
        elif ext >= 2016102400:
            return 24
        elif ext >= 2015111100:
            return 23
        elif ext >= 2014091700:
            return 22
        elif ext >= 2013100200:
            return 21
        elif ext >= 2012010100:
            return 20
    elif model == "KDZ":
        return 19
    elif model == "JDZ":
        return 18

    elif model == "M32":
        if ext >= 2024031300:
            return 10
        elif ext >= 2022121400:
            return 9
        elif ext >= 2021042100:
            return 8
        elif ext >= 2019100200:
            return 7
        elif ext >= 2018072700:
            return 6
        # TODO: Support versions 1-5 (never)
        elif ext >= 2017090600:
            return 5
        elif ext >= 2017011800:
            return 4
        elif ext >= 2015042100:
            return 3
        elif ext >= 2014021400:
            return 2
        elif ext >= 2013012400:
            return 1

    elif model == "MDX":
        if ext >= 2024061200 and ext not in (2024042069, 2025042069): # GF
            return 20
        if ext >= 2019022600:  # ???
            return 19

    elif model == "KFC":
        if ext >= 2025122401:  # NABLA: its first songs were distributed on 2025-12-24, EXCEED GEAR's last on 2025-12-18
            return 7
        elif ext >= 2021021700 and ext < 2025122401:  # EXCEED GEAR
            return 6

    elif model == "REC":
        return 1

    # TODO: ???
    # elif model == "PAN":
    #     return 0

    elif model == "L44":
        if ext >= 2023092001:
            return 12

    elif model == "M39":
        # pcb24/info24/player24 protocol (pop'n 24 Usaneko era modules)
        return 24

    elif model == "MBR":
        if ext >= 2016100400:  # REFLEC BEAT VOLZZA 2
            return 6
        return 5

    elif model == "PIX":
        if ext >= 2016072600:  # MUSECA 1+1/2 (museca plus builds keep this branch)
            return 2
        return 1

    else:
        return 0


def lz77_log_line(what, plain_size, packed_size, seconds=None, sent=True):
    # one line per compressed body, e.g. "LZ77 response: 73,548 -> 12,993 bytes (17.7%), 21.3 ms"
    ratio = f"{packed_size / plain_size:.1%}" if plain_size else "-"
    took = f", {seconds * 1000:.1f} ms" if seconds is not None else ""
    if what == "request":
        sizes = f"{packed_size:,} -> {plain_size:,} bytes unpacked"
    else:
        sizes = f"{plain_size:,} -> {packed_size:,} bytes"
    skipped = "" if sent else " - no gain, sent uncompressed"
    return f"\033[93mLZ77 {what}\033[0m: {sizes} ({ratio}){took}{skipped}"


_seen_request_encodings = set()


async def core_process_request(request):
    cl = request.headers.get("Content-Length")
    data = await request.body()

    if not cl or not data:
        return {}

    request.compress = request.headers.get("X-Compress", "none") # intentionally lowercase 'none' (NOT None)

    if "X-Eamuse-Info" in request.headers:
        xeamuseinfo = request.headers.get("X-Eamuse-Info")
        version, unix_time, prng = xeamuseinfo.split("-")
        xml_dec = EamuseARC4(bytes.fromhex(unix_time), bytes.fromhex(prng)).decrypt(data[: int(cl)])
        request.is_encrypted = True
    else:
        xml_dec = data[: int(cl)]
        request.is_encrypted = False

    lz77_note = None
    if request.compress == "lz77":
        packed_size = len(xml_dec)
        xml_dec = lz77_decode(xml_dec)
        lz77_note = lz77_log_line("request", len(xml_dec), packed_size)

    xml = KBinXML(xml_dec, convert_illegal_things=True)
    root = xml.xml_doc
    xml_text = xml.to_text()
    request.is_binxml = KBinXML.is_binary_xml(xml_dec)
    # The third byte of a binary request names the encoding of its strings (0x80 Shift-JIS, 0xa0 UTF-8, ...);
    # it comes from <encoding> in the game's ea3-config.xml. Said once per game, it is worth knowing.
    request.xml_encoding = xml.encoding if request.is_binxml else None
    game_code = root.attrib.get("model", "?").split(":")[0]
    if (game_code, request.xml_encoding) not in _seen_request_encodings:
        _seen_request_encodings.add((game_code, request.xml_encoding))
        print(f"XML: {game_code} sends {'binary requests, strings in ' + str(request.xml_encoding) if request.is_binxml else 'plain XML requests'}")

    if config.verbose_log:
        print()
        print("\033[94mREQUEST\033[0m:")
        if lz77_note:
            print(lz77_note)
        print(xml_text)
    elif lz77_note:
        print(lz77_note)

    model_parts = (root.attrib["model"], *root.attrib["model"].split(":"))
    module = root[0].tag
    method = root[0].attrib["method"] if "method" in root[0].attrib else None
    command = root[0].attrib["command"] if "command" in root[0].attrib else None
    game_version = await core_get_game_version_from_software_version(model_parts)

    return {
        "root": root,
        "text": xml_text,
        "module": module,
        "method": method,
        "command": command,
        "model": model_parts[1],
        "dest": model_parts[2],
        "spec": model_parts[3],
        "rev": model_parts[4],
        "ext": model_parts[5],
        "game_version": game_version,
    }


async def core_prepare_response(request, xml, encoding=None):
    # encoding: the string encoding of a binary answer ("UTF-8", ...); None = kbinxml's default, Shift-JIS.
    # A game reads the bytes of a string as they are, so an answer whose text the game decodes as UTF-8
    # (SOUND VOLTEX's skill course names) has to be sent as UTF-8.
    binxml = KBinXML(xml)

    if request.is_binxml:
        xml_binary = binxml.to_binary(encoding=encoding) if encoding else binxml.to_binary()
    else:
        xml_binary = binxml.to_text().encode("utf-8")  # TODO: Proper encoding

    if config.verbose_log:
        print("\033[91mRESPONSE\033[0m:")
        print(binxml.to_text())

    response_headers = {"User-Agent": "EAMUSE.Httpac/1.0"}

    # X-Compress says how THIS body is packed, so a game that asked with lz77 can be answered
    # with "none" (that is what it gets whenever response_compression is off).
    response_headers["X-Compress"] = "none" # intentionally lowercase 'none' (NOT None)
    response = xml_binary
    if config.response_compression and request.compress == "lz77":
        started = time.perf_counter()
        packed = lz77_encode(xml_binary)  # roughly 2 MB/s, about 20 ms for a 70 KB profile
        # Small bodies grow: the stream spends a flag bit per item and ends with a three byte
        # marker. Only send the packed body when it really is the smaller one.
        keep = len(packed) < len(xml_binary)
        print(lz77_log_line("response", len(xml_binary), len(packed), time.perf_counter() - started, sent=keep))
        if keep:
            response_headers["X-Compress"] = "lz77"
            response = packed


    if request.is_encrypted:
        version = 1
        unix_time = int(time.time())
        prng = next(prng_init) & 0xFFFF
        response_headers["X-Eamuse-Info"] = f"{version}-{unix_time:08x}-{prng:04x}"
        response = EamuseARC4(unix_time.to_bytes(4), prng.to_bytes(2)).encrypt(response)
    else:
        response = bytes(response)

    return response, response_headers
