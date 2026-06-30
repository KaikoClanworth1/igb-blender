"""GameCube / Wii CMPR (GX-tiled S3TC) texture ENCODER.

The exact inverse of ``utils.image_convert.decompress_wii_cmpr``. CMPR is DXT1
colour compression arranged in 8x8 macro-blocks — each macro-block holds four
DXT1 4x4 sub-blocks laid out as::

    [A][B]
    [C][D]

(sub 0 = top-left, 1 = top-right, 2 = bottom-left, 3 = bottom-right). Within
each sub-block the RGB565 endpoints AND the 2-bit index dword are stored
BIG-ENDIAN, even though the IGB container itself is little-endian.

Used by the GameCube export profile (igImage pfmt 34) for XML1 / XML2 GameCube
actors. Verified against native GC actors: a 128x128 image = (128/8)*(128/8)*32
= 8192 bytes, byte-identical layout to what the decoder reads back.
"""

import struct

from .dxt_compress import _extract_block, _compress_dxt1_block


def _cmpr_subblock(block):
    """Encode one 4x4 RGBA block to an 8-byte CMPR (big-endian DXT1) sub-block."""
    # _compress_dxt1_block returns a standard little-endian DXT1 block:
    #   struct.pack('<HHI', c0, c1, indices)
    # CMPR stores the same fields big-endian (the decoder reads them with >I /
    # manual <<8), so just byte-swap c0/c1/indices into big-endian order.
    c0, c1, idx = struct.unpack("<HHI", _compress_dxt1_block(block))
    return struct.pack(">HHI", c0, c1, idx)


def compress_rgba_to_cmpr(rgba_data, width, height):
    """Compress RGBA8888 bytes to a single-level CMPR stream (GX 8x8 tiling).

    Dimensions are padded up to a multiple of 8 (edge-clamped pixels fill the
    pad, exactly as the decoder discards them). Output length is always
    (pad_w/8) * (pad_h/8) * 32 bytes = 0.5 byte/pixel.
    """
    pad_w = (width + 7) & ~7
    pad_h = (height + 7) & ~7
    macro_cols = pad_w // 8
    macro_rows = pad_h // 8
    out = bytearray(macro_cols * macro_rows * 32)
    o = 0
    for my in range(macro_rows):
        for mx in range(macro_cols):
            for sub in range(4):                      # TL, TR, BL, BR
                sx = mx * 8 + (sub % 2) * 4
                sy = my * 8 + (sub // 2) * 4
                block = _extract_block(rgba_data, width, height, sx, sy)
                out[o:o + 8] = _cmpr_subblock(block)
                o += 8
    return bytes(out)


def compress_with_mipmaps_cmpr(rgba_data, width, height):
    """Return ``[(cmpr_bytes, w, h)]`` for the texture-chain builder.

    Base level only — native GC actors store a single CMPR igImage (one image
    object, not a per-level chain); a full mip pyramid is a future refinement.
    """
    return [(compress_rgba_to_cmpr(rgba_data, width, height), width, height)]
