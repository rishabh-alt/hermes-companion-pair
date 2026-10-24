"""Minimal, dependency-free QR encoder (byte mode, EC level M) plus SVG and
ASCII renderers.

Why hand-rolled: the plugin must install on a bare Hermes host with no JS
toolchain and no third-party Python packages, yet the QR code *is* the
product (dashboard tab + CLI). Byte mode + level M covers pairing payloads
(roughly 250-500 bytes JSON -> versions 9-16).

Correctness strategy: fixed mask 0 (a valid mask; skipping optimal-mask
search keeps the output deterministic) and a module-for-module test against
the reference ``qrcode`` library in the repo's test script. If that test
ever fails, the renderers must not ship a code that does not scan.

Block table and alignment positions below are the standard QR values for
EC level M, versions 1-20 (payloads here never need more).
"""

from __future__ import annotations

# (count, total_codewords, data_codewords) triples, EC level M.
_BLOCKS_M: dict[int, tuple[int, ...]] = {
    1: (1, 26, 16),
    2: (1, 44, 28),
    3: (1, 70, 44),
    4: (2, 50, 32),
    5: (2, 67, 43),
    6: (4, 43, 27),
    7: (4, 49, 31),
    8: (2, 60, 38, 2, 61, 39),
    9: (3, 58, 36, 2, 59, 37),
    10: (4, 69, 43, 1, 70, 44),
    11: (1, 80, 50, 4, 81, 51),
    12: (6, 58, 36, 2, 59, 37),
    13: (8, 59, 37, 1, 60, 38),
    14: (4, 64, 40, 5, 65, 41),
    15: (5, 65, 41, 5, 66, 42),
    16: (7, 73, 45, 3, 74, 46),
    17: (10, 74, 46, 1, 75, 47),
    18: (9, 69, 43, 4, 70, 44),
    19: (3, 70, 44, 11, 71, 45),
    20: (3, 67, 41, 13, 68, 42),
}

_ALIGN_M: dict[int, list[int]] = {
    1: [],
    2: [6, 18],
    3: [6, 22],
    4: [6, 26],
    5: [6, 30],
    6: [6, 34],
    7: [6, 22, 38],
    8: [6, 24, 42],
    9: [6, 26, 46],
    10: [6, 28, 50],
    11: [6, 30, 54],
    12: [6, 32, 58],
    13: [6, 34, 62],
    14: [6, 26, 46, 66],
    15: [6, 26, 48, 70],
    16: [6, 26, 50, 74],
    17: [6, 30, 54, 78],
    18: [6, 30, 56, 82],
    19: [6, 30, 58, 86],
    20: [6, 34, 62, 90],
}

# GF(256) with primitive polynomial 0x11D.
_EXP = [0] * 512
_LOG = [0] * 256
_x = 1
for _i in range(255):
    _EXP[_i] = _x
    _LOG[_x] = _i
    _x <<= 1
    if _x & 0x100:
        _x ^= 0x11D
for _i in range(255, 512):
    _EXP[_i] = _EXP[_i - 255]


def _gf_mul(a: int, b: int) -> int:
    if a == 0 or b == 0:
        return 0
    return _EXP[_LOG[a] + _LOG[b]]


def _rs_generator(ec_count: int) -> list[int]:
    """Generator polynomial in DESCENDING order (leading 1 first), as the
    synthetic-division remainder loop requires."""
    poly = [1]  # ascending order during construction
    for i in range(ec_count):
        root = _EXP[i]
        nxt = [0] * (len(poly) + 1)
        for j, coef in enumerate(poly):
            nxt[j] ^= _gf_mul(coef, root)
            nxt[j + 1] ^= coef
        poly = nxt
    return poly[::-1]


def _rs_remainder(data: list[int], ec_count: int) -> list[int]:
    gen = _rs_generator(ec_count)
    res = [0] * ec_count
    for byte in data:
        factor = byte ^ res[0]
        res = res[1:] + [0]
        for i in range(ec_count):
            res[i] ^= _gf_mul(gen[i + 1], factor)
    return res


def _data_capacity_bytes(version: int) -> int:
    """Usable payload bytes in byte mode at EC level M (after mode/count
    overhead, before terminator/padding)."""
    triples = _BLOCKS_M[version]
    total_data = 0
    for i in range(0, len(triples), 3):
        total_data += triples[i] * triples[i + 2]
    count_bits = 8 if version <= 9 else 16
    bits = total_data * 8 - 4 - count_bits
    return bits // 8


def _pick_version(data: bytes) -> int:
    for version in range(1, 21):
        if len(data) <= _data_capacity_bytes(version):
            return version
    raise ValueError("payload too large for QR versions 1-20 at EC level M")


def _build_codewords(data: bytes, version: int) -> list[int]:
    triples = _BLOCKS_M[version]
    total_data = sum(triples[i] * triples[i + 2] for i in range(0, len(triples), 3))
    count_bits = 8 if version <= 9 else 16
    bits: list[int] = []

    def put(value: int, length: int) -> None:
        for shift in range(length - 1, -1, -1):
            bits.append((value >> shift) & 1)

    put(0b0100, 4)  # byte mode
    put(len(data), count_bits)
    for byte in data:
        put(byte, 8)
    # Terminator + byte alignment + pad bytes.
    capacity_bits = total_data * 8
    term = min(4, capacity_bits - len(bits))
    put(0, term)
    while len(bits) % 8:
        bits.append(0)
    pads = (0xEC, 0x11)
    pi = 0
    while len(bits) < capacity_bits:
        put(pads[pi % 2], 8)
        pi += 1
    codewords = []
    for i in range(0, len(bits), 8):
        value = 0
        for bit in bits[i:i + 8]:
            value = (value << 1) | bit
        codewords.append(value)
    # Split into blocks, compute EC per block, interleave.
    blocks: list[tuple[list[int], list[int]]] = []
    offset = 0
    for i in range(0, len(triples), 3):
        count, total, data_count = triples[i], triples[i + 1], triples[i + 2]
        ec_count = total - data_count
        for _ in range(count):
            chunk = codewords[offset:offset + data_count]
            offset += data_count
            blocks.append((chunk, _rs_remainder(chunk, ec_count)))
    out: list[int] = []
    max_data = max(len(b[0]) for b in blocks)
    for i in range(max_data):
        for chunk, _ in blocks:
            if i < len(chunk):
                out.append(chunk[i])
    max_ec = max(len(b[1]) for b in blocks)
    for i in range(max_ec):
        for _, ec in blocks:
            if i < len(ec):
                out.append(ec[i])
    return out


def _bch_digit(value: int) -> int:
    digits = 0
    while value:
        digits += 1
        value >>= 1
    return digits


def _format_bits(ec_level_bits: int, mask: int) -> int:
    data = (ec_level_bits << 3) | mask
    g15 = 0b10100110111
    d = data << 10
    while _bch_digit(d) - _bch_digit(g15) >= 0:
        d ^= g15 << (_bch_digit(d) - _bch_digit(g15))
    return ((data << 10) | d) ^ 0b101010000010010


def _version_bits(version: int) -> int:
    g18 = 0b1111100100101
    d = version << 12
    while _bch_digit(d) - _bch_digit(g18) >= 0:
        d ^= g18 << (_bch_digit(d) - _bch_digit(g18))
    return (version << 12) | d


def encode_matrix(data: bytes) -> list[list[bool]]:
    """Encode byte-mode QR, EC level M, fixed mask 0. Returns a square
    matrix of booleans (True = dark module), WITHOUT quiet zone."""
    version = _pick_version(data)
    size = 17 + 4 * version
    matrix: list[list[bool | None]] = [[None] * size for _ in range(size)]

    def set_module(r: int, c: int, dark: bool) -> None:
        matrix[r][c] = dark

    def finder(r0: int, c0: int) -> None:
        for dr in range(-1, 8):
            for dc in range(-1, 8):
                r, c = r0 + dr, c0 + dc
                if not (0 <= r < size and 0 <= c < size):
                    continue
                if dr == -1 or dr == 7 or dc == -1 or dc == 7:
                    set_module(r, c, False)
                elif dr in (0, 6) or dc in (0, 6):
                    set_module(r, c, True)
                elif 2 <= dr <= 4 and 2 <= dc <= 4:
                    set_module(r, c, True)
                else:
                    set_module(r, c, False)

    finder(0, 0)
    finder(0, size - 7)
    finder(size - 7, 0)
    # Timing patterns.
    for i in range(8, size - 8):
        dark = i % 2 == 0
        set_module(6, i, dark)
        set_module(i, 6, dark)
    # Alignment patterns (skip the three finder corners).
    positions = _ALIGN_M[version]
    for r in positions:
        for c in positions:
            if (r == positions[0] and c == positions[0]) or \
               (r == positions[0] and c == positions[-1]) or \
               (r == positions[-1] and c == positions[0]):
                continue
            for dr in range(-2, 3):
                for dc in range(-2, 3):
                    if max(abs(dr), abs(dc)) == 2 or (dr == 0 and dc == 0):
                        set_module(r + dr, c + dc, True)
                    else:
                        set_module(r + dr, c + dc, False)
    # Reserve format-info areas (filled in below).
    for i in range(9):
        if matrix[8][i] is None:
            set_module(8, i, False)
        if matrix[i][8] is None:
            set_module(i, 8, False)
    for i in range(8):
        if matrix[8][size - 1 - i] is None:
            set_module(8, size - 1 - i, False)
        if matrix[size - 1 - i][8] is None:
            set_module(size - 1 - i, 8, False)
    # Dark module.
    set_module(size - 8, 8, True)
    # Version info (v7+).
    if version >= 7:
        bits = _version_bits(version)
        for i in range(18):
            bit = bool((bits >> i) & 1)
            r, c = i // 3, i % 3
            set_module(r, size - 11 + c, bit)
            set_module(size - 11 + c, r, bit)
    # Data placement (zigzag, mask 0).
    codewords = _build_codewords(data, version)
    bits: list[int] = []
    for cw in codewords:
        for shift in range(7, -1, -1):
            bits.append((cw >> shift) & 1)
    bit_idx = 0
    upward = True
    col = size - 1
    while col > 0:
        if col == 6:
            col -= 1
        rows = range(size - 1, -1, -1) if upward else range(size)
        for r in rows:
            for c in (col, col - 1):
                if matrix[r][c] is None:
                    bit = bits[bit_idx] if bit_idx < len(bits) else 0
                    bit_idx += 1
                    if (r + c) % 2 == 0:  # mask 0
                        bit ^= 1
                    set_module(r, c, bool(bit))
        upward = not upward
        col -= 2
    # Format info (EC level M = 00, mask 0). Bits go MSB-first along each
    # copy: position 0 of a copy carries bit 14, position 14 carries
    # bit 0. Top-left row 8 gets the first 8 positions (timing gap at
    # column 6 skipped), top-right row 8 the rest; bottom-left column 8
    # gets the first 8 positions, top-left column 8 gets bit 8 at (7,8)
    # and the rest at rows 5..0 (row 6 stays the timing pattern). The
    # dark module is re-asserted afterwards: it shares a cell with the
    # second copy's middle position and always wins.
    fmt = _format_bits(0b00, 0)
    for i in range(15):
        bit = bool((fmt >> (14 - i)) & 1)
        if i < 6:
            set_module(8, i, bit)
        elif i < 8:
            set_module(8, i + 1, bit)
        else:
            set_module(8, size - 15 + i, bit)
        if i < 8:
            set_module(size - i - 1, 8, bit)
        elif i == 8:
            set_module(7, 8, bit)
        else:
            set_module(14 - i, 8, bit)
    set_module(size - 8, 8, True)  # dark module wins its shared cell
    return [[bool(cell) for cell in row] for row in matrix]


def matrix_to_svg(matrix: list[list[bool]], module_px: int = 6,
                  quiet_modules: int = 4) -> str:
    """Render a module matrix as a standalone SVG string (black on white)."""
    size = len(matrix)
    total = size + 2 * quiet_modules
    width = total * module_px
    rects: list[str] = []
    for r, row in enumerate(matrix):
        for c, dark in enumerate(row):
            if dark:
                x = (c + quiet_modules) * module_px
                y = (r + quiet_modules) * module_px
                rects.append(f'<rect x="{x}" y="{y}" width="{module_px}" height="{module_px}"/>')
    body = "".join(rects)
    return (f'<svg xmlns="http://www.w3.org/2000/svg" width="{width}" height="{width}"'
            f' viewBox="0 0 {width} {width}"><rect width="{width}" height="{width}" fill="white"/>'
            f'<g fill="black">{body}</g></svg>')


def matrix_to_ascii(matrix: list[list[bool]], quiet_modules: int = 2) -> str:
    """Render a module matrix as terminal ASCII (two chars per module)."""
    size = len(matrix)
    lines: list[str] = []
    blank = "  " * (size + 2 * quiet_modules)
    for _ in range(quiet_modules):
        lines.append(blank)
    for row in matrix:
        line = "  " * quiet_modules
        for dark in row:
            line += "██" if dark else "  "
        line += "  " * quiet_modules
        lines.append(line)
    for _ in range(quiet_modules):
        lines.append(blank)
    return "\n".join(lines)
