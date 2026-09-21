# The LZSS variant e-amusement calls "lz77" (X-Compress: lz77, also used inside .arc files).
#
# Stream: a flag byte, then one item per bit from bit 0 up. Bit 1 = a literal byte. Bit 0 = a
# big-endian 16 bit word: distance (12 bits) and length - 3 (4 bits), a copy of 3..18 bytes that
# starts `distance` bytes back; distance 0 ends the stream. The game's decoder (avs2-core,
# avs-cstream-lz77) keeps a 4096 byte ring that starts out empty and copies byte by byte, so
#   - a distance of 1..4095 is valid,
#   - a copy may overlap what it is writing (length > distance repeats the last bytes),
#   - bytes "before the start" read as zero. The encoder never refers to them, the decoder
#     accepts them because Konami's own compressor does.

WINDOW_SIZE = 0x1000
MAX_DISTANCE = WINDOW_SIZE - 1
THRESHOLD = 3
MAX_LEN = 0xF + THRESHOLD

# How many earlier positions with the same three bytes are tried per match. More finds slightly
# longer matches and costs time on repetitive data; 32 is within about 1% of an exhaustive search
# on binary xml.
MAX_CANDIDATES = 32


def lz77_encode(data: bytes) -> bytes:
    data = bytes(data)
    size = len(data)
    output = bytearray()
    positions = {}  # three bytes -> positions where they occur, oldest first
    pos = 0
    items = 8  # items in the current flag group; 8 = full, the next item needs a new flag byte

    while pos < size:
        flag_at = len(output)
        output.append(0)
        flags = 0
        items = 0
        while items < 8 and pos < size:
            best_len = 0
            best_distance = 0
            max_len = min(MAX_LEN, size - pos)
            if max_len >= THRESHOLD:
                candidates = positions.get(data[pos:pos + THRESHOLD])
                if candidates:
                    oldest = pos - MAX_DISTANCE
                    for candidate in reversed(candidates[-MAX_CANDIDATES:]):
                        if candidate < oldest:
                            break
                        # a longer match has to agree at the byte that would make it longer
                        if best_len and data[candidate + best_len] != data[pos + best_len]:
                            continue
                        length = THRESHOLD
                        while length < max_len and data[candidate + length] == data[pos + length]:
                            length += 1
                        if length > best_len:
                            best_len = length
                            best_distance = pos - candidate
                            if length == max_len:
                                break

            if best_len:
                output.append(best_distance >> 4)
                output.append((best_distance & 0x0F) << 4 | (best_len - THRESHOLD))
                step = best_len
            else:
                flags |= 1 << items
                output.append(data[pos])
                step = 1
            items += 1

            for p in range(pos, min(pos + step, size - THRESHOLD + 1)):
                key = data[p:p + THRESHOLD]
                found = positions.get(key)
                if found is None:
                    positions[key] = [p]
                else:
                    found.append(p)
                    if len(found) > 4 * MAX_CANDIDATES:
                        del found[:-MAX_CANDIDATES]
            pos += step
        output[flag_at] = flags

    # The end marker (distance 0) is an item like any other: it takes the next flag bit, which is
    # 0 already, or a new flag byte when the last group is full.
    if items == 8:
        output.append(0)
    output += b"\x00\x00"
    return bytes(output)


def lz77_decode(data: bytes) -> bytes:
    data = bytes(data)
    size = len(data)
    output = bytearray()
    at = 0

    while at < size:
        flags = data[at]
        at += 1
        for bit in range(8):
            if flags >> bit & 1:
                if at >= size:
                    return bytes(output)
                output.append(data[at])
                at += 1
                continue

            if at + 1 >= size:
                return bytes(output)
            word = data[at] << 8 | data[at + 1]
            at += 2
            distance = word >> 4
            if distance == 0:
                return bytes(output)
            length = (word & 0x0F) + THRESHOLD

            start = len(output) - distance
            if start >= 0 and distance >= length:
                output += output[start:start + length]
            else:
                # overlapping copy, or one reaching before the start (zeros)
                for _ in range(length):
                    output.append(output[start] if start >= 0 else 0)
                    start += 1

    return bytes(output)
