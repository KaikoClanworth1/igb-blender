"""Image format conversion utilities for IGB textures.

Handles decompression and pixel format conversion:
- DXT1/DXT3/DXT5 -> RGBA8888
- PS2 CLUT-indexed (4-bit/8-bit paletted) -> RGBA8888
- Wii CMPR (GC S3TC in tiled 8x8 macro-blocks) -> RGBA8888
- PSP tiled CLUT-indexed (4-bit/8-bit) -> RGBA8888
- DXN / BC5 (Xbox 360 two-channel normal maps) -> RGBA8888
- BGRA -> RGBA byte reordering
- Various pixel formats to standard RGBA

XML2 PC textures use DXT3 compression (pfmt=15).
MUA2 PS2 textures use CLUT-indexed formats (pfmt=65536 for 8bpp, 65537 for 4bpp).
MUA Wii uses CMPR (pfmt=21), PSP uses tiled CLUT (pfmt=42/43).
MUA Xbox 360 uses DXN (pfmt=44) for normal maps.
"""

import struct


def decode_dxt1_block(data, offset=0):
    """Decode a single DXT1 4x4 pixel block (8 bytes) to 16 RGBA pixels.

    DXT1 block format:
        2 bytes: RGB565 color0
        2 bytes: RGB565 color1
        4 bytes: 4x4 2-bit index table

    Returns:
        list of 16 tuples (R, G, B, A) as 0-255 integers, row by row
    """
    c0_raw, c1_raw, indices = struct.unpack_from("<HHI", data, offset)

    # Decode RGB565 colors
    c0 = _rgb565_to_rgba(c0_raw)
    c1 = _rgb565_to_rgba(c1_raw)

    # Build color palette
    if c0_raw > c1_raw:
        # 4-color mode: c2 = 2/3*c0 + 1/3*c1, c3 = 1/3*c0 + 2/3*c1
        c2 = tuple((2 * a + b + 1) // 3 for a, b in zip(c0, c1))
        c3 = tuple((a + 2 * b + 1) // 3 for a, b in zip(c0, c1))
    else:
        # 3-color + transparent mode
        c2 = tuple((a + b) // 2 for a, b in zip(c0, c1))
        c3 = (0, 0, 0, 0)

    palette = [c0, c1, c2, c3]

    # Decode 4x4 pixel block using 2-bit indices
    pixels = []
    for row in range(4):
        for col in range(4):
            bit_pos = (row * 4 + col) * 2
            idx = (indices >> bit_pos) & 0x03
            pixels.append(palette[idx])

    return pixels


def decode_dxt3_block(data, offset=0):
    """Decode a single DXT3 4x4 pixel block (16 bytes) to 16 RGBA pixels.

    DXT3 block format:
        8 bytes: 4-bit alpha values for each of 16 pixels
        8 bytes: DXT1 color block (RGB565 + 2-bit indices)

    Returns:
        list of 16 tuples (R, G, B, A) as 0-255 integers, row by row
    """
    # First 8 bytes: explicit 4-bit alpha for each pixel
    alpha_data = struct.unpack_from("<8B", data, offset)

    # Second 8 bytes: DXT1 color block
    color_pixels = decode_dxt1_block(data, offset + 8)

    # Combine: use color from DXT1 block, replace alpha with explicit values
    pixels = []
    for i in range(16):
        r, g, b, _ = color_pixels[i]
        # Each byte contains two 4-bit alpha values (low nibble first)
        byte_idx = i // 2
        if i % 2 == 0:
            alpha4 = alpha_data[byte_idx] & 0x0F
        else:
            alpha4 = (alpha_data[byte_idx] >> 4) & 0x0F
        # Expand 4-bit alpha to 8-bit
        alpha = (alpha4 << 4) | alpha4
        pixels.append((r, g, b, alpha))

    return pixels


def decode_dxt5_block(data, offset=0):
    """Decode a single DXT5 4x4 pixel block (16 bytes) to 16 RGBA pixels.

    DXT5 block format:
        2 bytes: alpha0, alpha1 (interpolation endpoints)
        6 bytes: 4x4 3-bit alpha index table
        8 bytes: DXT1 color block

    Returns:
        list of 16 tuples (R, G, B, A) as 0-255 integers, row by row
    """
    # Alpha endpoints
    alpha0 = data[offset]
    alpha1 = data[offset + 1]

    # Build alpha palette
    if alpha0 > alpha1:
        alpha_palette = [
            alpha0,
            alpha1,
            (6 * alpha0 + 1 * alpha1 + 3) // 7,
            (5 * alpha0 + 2 * alpha1 + 3) // 7,
            (4 * alpha0 + 3 * alpha1 + 3) // 7,
            (3 * alpha0 + 4 * alpha1 + 3) // 7,
            (2 * alpha0 + 5 * alpha1 + 3) // 7,
            (1 * alpha0 + 6 * alpha1 + 3) // 7,
        ]
    else:
        alpha_palette = [
            alpha0,
            alpha1,
            (4 * alpha0 + 1 * alpha1 + 2) // 5,
            (3 * alpha0 + 2 * alpha1 + 2) // 5,
            (2 * alpha0 + 3 * alpha1 + 2) // 5,
            (1 * alpha0 + 4 * alpha1 + 2) // 5,
            0,
            255,
        ]

    # Read 48-bit (6 byte) alpha index table
    alpha_bits = struct.unpack_from("<Q", data, offset)[0] >> 16  # skip first 2 bytes
    alpha_bits &= 0xFFFFFFFFFFFF  # mask to 48 bits

    # DXT1 color block
    color_pixels = decode_dxt1_block(data, offset + 8)

    # Combine
    pixels = []
    for i in range(16):
        r, g, b, _ = color_pixels[i]
        alpha_idx = (alpha_bits >> (i * 3)) & 0x07
        alpha = alpha_palette[alpha_idx]
        pixels.append((r, g, b, alpha))

    return pixels


def decompress_dxt(pixel_data, width, height, pixel_format):
    """Decompress DXT-compressed image data to RGBA8888.

    Args:
        pixel_data: raw compressed bytes
        width: image width in pixels
        height: image height in pixels
        pixel_format: one of PFMT_RGB_DXT1, PFMT_RGBA_DXT1, PFMT_RGBA_DXT3, PFMT_RGBA_DXT5

    Returns:
        bytearray of width*height*4 RGBA bytes, or None on error
    """
    from ..scene_graph.sg_materials import (
        PFMT_RGB_DXT1, PFMT_RGBA_DXT1, PFMT_RGBA_DXT3, PFMT_RGBA_DXT5,
    )

    blocks_x = max(1, (width + 3) // 4)
    blocks_y = max(1, (height + 3) // 4)

    if pixel_format in (PFMT_RGB_DXT1, PFMT_RGBA_DXT1):
        block_size = 8
        decode_fn = decode_dxt1_block
    elif pixel_format == PFMT_RGBA_DXT3:
        block_size = 16
        decode_fn = decode_dxt3_block
    elif pixel_format == PFMT_RGBA_DXT5:
        block_size = 16
        decode_fn = decode_dxt5_block
    else:
        return None

    expected_size = blocks_x * blocks_y * block_size
    if len(pixel_data) < expected_size:
        return None

    # Allocate output
    output = bytearray(width * height * 4)

    for by in range(blocks_y):
        for bx in range(blocks_x):
            block_offset = (by * blocks_x + bx) * block_size
            pixels = decode_fn(pixel_data, block_offset)

            # Write 4x4 block to output
            for row in range(4):
                py = by * 4 + row
                if py >= height:
                    break
                for col in range(4):
                    px = bx * 4 + col
                    if px >= width:
                        continue
                    pixel = pixels[row * 4 + col]
                    out_offset = (py * width + px) * 4
                    output[out_offset] = pixel[0]      # R
                    output[out_offset + 1] = pixel[1]  # G
                    output[out_offset + 2] = pixel[2]  # B
                    output[out_offset + 3] = pixel[3]  # A

    return output


def convert_image_to_rgba(parsed_image):
    """Convert a ParsedImage to RGBA8888 pixel data.

    Handles DXT decompression, PS2 CLUT decoding, and various pixel
    format conversions.

    Args:
        parsed_image: ParsedImage from sg_materials

    Returns:
        bytearray of width*height*4 RGBA bytes, or None on error
    """
    from ..scene_graph.sg_materials import (
        PFMT_RGB_DXT1, PFMT_RGBA_DXT1, PFMT_RGBA_DXT3, PFMT_RGBA_DXT5,
        PFMT_RGBA_8888_32, PFMT_RGB_888_24, PFMT_RGBA_5551_16,
        PFMT_RGBA_4444_16, PFMT_RGB_565_16, PFMT_L_8, PFMT_A_8,
        PFMT_LA_88_16, PFMT_PS2_PSMT8, PFMT_PS2_PSMT4,
        PFMT_WII_CMPR, PFMT_PSP_TILED_8, PFMT_PSP_TILED_4, PFMT_DXN,
    )

    if parsed_image is None or parsed_image.pixel_data is None:
        return None

    w = parsed_image.width
    h = parsed_image.height
    data = parsed_image.pixel_data
    pfmt = parsed_image.pixel_format

    if w == 0 or h == 0:
        return None

    # PS2 CLUT-indexed formats
    if pfmt in (PFMT_PS2_PSMT8, PFMT_PS2_PSMT4):
        clut = getattr(parsed_image, 'clut_data', None)
        if clut is None:
            return None
        bpp = 8 if pfmt == PFMT_PS2_PSMT8 else 4
        return decode_clut_ps2(data, clut, w, h, bpp)

    # PSP tiled CLUT-indexed formats
    if pfmt in (PFMT_PSP_TILED_8, PFMT_PSP_TILED_4):
        clut = getattr(parsed_image, 'clut_data', None)
        if clut is None:
            return None
        bpp = 8 if pfmt == PFMT_PSP_TILED_8 else 4
        return decode_clut_psp(data, clut, w, h, bpp)

    # DXT compressed formats
    if pfmt in (PFMT_RGB_DXT1, PFMT_RGBA_DXT1, PFMT_RGBA_DXT3, PFMT_RGBA_DXT5):
        return decompress_dxt(data, w, h, pfmt)

    # Wii CMPR (GameCube S3TC in tiled 8x8 macro-blocks)
    if pfmt == PFMT_WII_CMPR:
        return decompress_wii_cmpr(data, w, h)

    # DXN / BC5 (Xbox 360 two-channel normal map)
    if pfmt == PFMT_DXN:
        return decompress_dxn(data, w, h)

    # Uncompressed RGBA 8888 (32-bit)
    if pfmt == PFMT_RGBA_8888_32:
        if len(data) >= w * h * 4:
            return bytearray(data[:w * h * 4])
        return None

    # Uncompressed RGB 888 (24-bit) -> RGBA
    if pfmt == PFMT_RGB_888_24:
        if len(data) < w * h * 3:
            return None
        output = bytearray(w * h * 4)
        for i in range(w * h):
            src = i * 3
            dst = i * 4
            output[dst] = data[src]         # R
            output[dst + 1] = data[src + 1]  # G
            output[dst + 2] = data[src + 2]  # B
            output[dst + 3] = 255            # A
        return output

    # RGBA 5551 (16-bit) -> RGBA
    if pfmt == PFMT_RGBA_5551_16:
        if len(data) < w * h * 2:
            return None
        output = bytearray(w * h * 4)
        for i in range(w * h):
            val = struct.unpack_from("<H", data, i * 2)[0]
            r = ((val >> 11) & 0x1F) * 255 // 31
            g = ((val >> 6) & 0x1F) * 255 // 31
            b = ((val >> 1) & 0x1F) * 255 // 31
            a = (val & 0x01) * 255
            output[i * 4] = r
            output[i * 4 + 1] = g
            output[i * 4 + 2] = b
            output[i * 4 + 3] = a
        return output

    # RGBA 4444 (16-bit) -> RGBA
    if pfmt == PFMT_RGBA_4444_16:
        if len(data) < w * h * 2:
            return None
        output = bytearray(w * h * 4)
        for i in range(w * h):
            val = struct.unpack_from("<H", data, i * 2)[0]
            r = ((val >> 12) & 0x0F) * 17
            g = ((val >> 8) & 0x0F) * 17
            b = ((val >> 4) & 0x0F) * 17
            a = (val & 0x0F) * 17
            output[i * 4] = r
            output[i * 4 + 1] = g
            output[i * 4 + 2] = b
            output[i * 4 + 3] = a
        return output

    # RGB 565 (16-bit) -> RGBA
    if pfmt == PFMT_RGB_565_16:
        if len(data) < w * h * 2:
            return None
        output = bytearray(w * h * 4)
        for i in range(w * h):
            val = struct.unpack_from("<H", data, i * 2)[0]
            r, g, b, a = _rgb565_to_rgba(val)
            output[i * 4] = r
            output[i * 4 + 1] = g
            output[i * 4 + 2] = b
            output[i * 4 + 3] = a
        return output

    # Luminance 8 -> RGBA
    if pfmt == PFMT_L_8:
        if len(data) < w * h:
            return None
        output = bytearray(w * h * 4)
        for i in range(w * h):
            l = data[i]
            output[i * 4] = l
            output[i * 4 + 1] = l
            output[i * 4 + 2] = l
            output[i * 4 + 3] = 255
        return output

    # Alpha 8 -> RGBA (white with alpha)
    if pfmt == PFMT_A_8:
        if len(data) < w * h:
            return None
        output = bytearray(w * h * 4)
        for i in range(w * h):
            output[i * 4] = 255
            output[i * 4 + 1] = 255
            output[i * 4 + 2] = 255
            output[i * 4 + 3] = data[i]
        return output

    # Luminance-Alpha 88 -> RGBA
    if pfmt == PFMT_LA_88_16:
        if len(data) < w * h * 2:
            return None
        output = bytearray(w * h * 4)
        for i in range(w * h):
            l = data[i * 2]
            a = data[i * 2 + 1]
            output[i * 4] = l
            output[i * 4 + 1] = l
            output[i * 4 + 2] = l
            output[i * 4 + 3] = a
        return output

    # Unknown format - return None
    return None


def _rgb565_to_rgba(val):
    """Convert an RGB565 value to (R, G, B, A) tuple with 8-bit components."""
    r = ((val >> 11) & 0x1F) * 255 // 31
    g = ((val >> 5) & 0x3F) * 255 // 63
    b = (val & 0x1F) * 255 // 31
    return (r, g, b, 255)


def bgra_to_rgba(data, width, height):
    """Convert BGRA byte order to RGBA.

    Some platforms store pixels as BGRA (DirectX convention).
    This swaps the R and B channels.

    Args:
        data: bytearray or bytes of BGRA pixel data
        width: image width
        height: image height

    Returns:
        bytearray of RGBA pixel data
    """
    output = bytearray(data)
    for i in range(width * height):
        offset = i * 4
        # Swap R and B
        output[offset], output[offset + 2] = output[offset + 2], output[offset]
    return output


# ---------------------------------------------------------------------------
# PS2 texture support (CLUT-indexed and GS format handling)
# ---------------------------------------------------------------------------

def unswizzle_ps2(data, width, height, bpp):
    """Unswizzle PS2 GS tile layout to linear pixel order.

    PS2 stores texture data in a hardware-specific tile layout that must
    be converted to linear row-major order for standard image processing.

    For 8-bit and 4-bit indexed textures in MUA2 PS2 IGB files, the pixel
    data appears to already be in linear order (de-swizzled by the Alchemy
    engine during IGB export). This function is kept as a hook for future
    games that may store raw GS-swizzled data.

    Args:
        data: raw swizzled pixel data bytes
        width: image width in pixels
        height: image height in pixels
        bpp: bits per pixel

    Returns:
        bytearray of unswizzled pixel data, or None if not needed/implemented
    """
    # MUA2 PS2 IGB files store pixel data in linear order already.
    # Return None to indicate no unswizzling was performed (caller uses
    # original data).
    return None


def decode_clut_ps2(data, clut, width, height, bpp=8):
    """Decode PS2 CLUT-indexed (paletted) texture to RGBA.

    PS2 textures use 4-bit or 8-bit indexed color with a Color Lookup
    Table (CLUT) stored in an igClut object. Each pixel in the index
    data references a 32-bit RGBA palette entry.

    Supported modes:
        bpp=8: Each byte is an index into a 256-entry CLUT (pfmt=65536)
        bpp=4: Each byte contains two 4-bit indices (pfmt=65537)
               Low nibble = first pixel, high nibble = second pixel

    The CLUT palette is stored in linear order (no CSM1 reordering needed)
    and alpha values are in standard 0-255 range.

    Args:
        data: raw indexed pixel data bytes
        clut: color lookup table bytes (RGBA32 entries, 4 bytes each)
        width: image width in pixels
        height: image height in pixels
        bpp: bits per pixel (4 or 8 for indexed)

    Returns:
        bytearray of width*height*4 RGBA bytes, or None on error
    """
    num_pixels = width * height
    if num_pixels == 0:
        return None

    # Parse CLUT palette into list of (R, G, B, A) tuples for fast lookup
    num_entries = len(clut) // 4
    if num_entries == 0:
        return None

    palette = []
    for i in range(num_entries):
        offset = i * 4
        r = clut[offset]
        g = clut[offset + 1]
        b = clut[offset + 2]
        a = clut[offset + 3]
        palette.append((r, g, b, a))

    # Allocate output RGBA buffer
    output = bytearray(num_pixels * 4)

    if bpp == 8:
        # 8-bit indexed: 1 byte per pixel, direct index into 256-entry palette
        expected_size = num_pixels
        if len(data) < expected_size:
            return None

        for i in range(num_pixels):
            idx = data[i]
            if idx < num_entries:
                r, g, b, a = palette[idx]
            else:
                r, g, b, a = 0, 0, 0, 0
            out = i * 4
            output[out] = r
            output[out + 1] = g
            output[out + 2] = b
            output[out + 3] = a

    elif bpp == 4:
        # 4-bit indexed: 2 pixels per byte, low nibble first
        expected_size = (num_pixels + 1) // 2
        if len(data) < expected_size:
            return None

        for i in range(num_pixels):
            byte_idx = i // 2
            if i % 2 == 0:
                idx = data[byte_idx] & 0x0F
            else:
                idx = (data[byte_idx] >> 4) & 0x0F

            if idx < num_entries:
                r, g, b, a = palette[idx]
            else:
                r, g, b, a = 0, 0, 0, 0
            out = i * 4
            output[out] = r
            output[out + 1] = g
            output[out + 2] = b
            output[out + 3] = a

    else:
        return None

    return output


# ---------------------------------------------------------------------------
# Wii / GameCube CMPR (S3TC) texture decompression
# ---------------------------------------------------------------------------

def decompress_wii_cmpr(data, width, height):
    """Decompress Wii/GameCube CMPR texture to RGBA8888.

    CMPR is S3TC/DXT1 compression arranged in an 8x8 macro-block tile
    layout specific to GX hardware. Each 8x8 macro-block contains four
    DXT1 4x4 sub-blocks arranged as:

        [A][B]
        [C][D]

    Macro-blocks are tiled across the image in row-major order, with the
    image dimensions padded up to multiples of 8.

    The sub-blocks use standard DXT1 encoding but with **big-endian**
    color endpoints (RGB565 stored as big-endian uint16).

    Args:
        data: raw CMPR compressed bytes
        width: image width in pixels
        height: image height in pixels

    Returns:
        bytearray of width*height*4 RGBA bytes, or None on error
    """
    # Pad dimensions to multiples of 8 for macro-block tiling
    pad_w = (width + 7) & ~7
    pad_h = (height + 7) & ~7
    macro_cols = pad_w // 8
    macro_rows = pad_h // 8

    # Each 8x8 macro-block = 4 DXT1 blocks = 4 * 8 = 32 bytes
    expected_size = macro_cols * macro_rows * 32
    if len(data) < expected_size:
        return None

    output = bytearray(width * height * 4)

    for my in range(macro_rows):
        for mx in range(macro_cols):
            macro_offset = (my * macro_cols + mx) * 32

            # Four sub-blocks within the 8x8 macro-block:
            # sub 0 = top-left 4x4, sub 1 = top-right 4x4
            # sub 2 = bottom-left 4x4, sub 3 = bottom-right 4x4
            for sub in range(4):
                sub_offset = macro_offset + sub * 8

                # Read big-endian color endpoints and convert to little-endian
                # for our existing DXT1 decoder
                c0_be = (data[sub_offset] << 8) | data[sub_offset + 1]
                c1_be = (data[sub_offset + 2] << 8) | data[sub_offset + 3]

                # Repack as little-endian DXT1 block for decode_dxt1_block
                le_block = struct.pack("<HHI",
                                       c0_be, c1_be,
                                       struct.unpack_from(">I", data,
                                                          sub_offset + 4)[0])
                pixels = decode_dxt1_block(le_block, 0)

                # Sub-block position within the 8x8 macro-block
                sub_x = (sub % 2) * 4
                sub_y = (sub // 2) * 4

                # Write pixels to output
                base_px = mx * 8 + sub_x
                base_py = my * 8 + sub_y

                for row in range(4):
                    py = base_py + row
                    if py >= height:
                        break
                    for col in range(4):
                        px = base_px + col
                        if px >= width:
                            continue
                        pixel = pixels[row * 4 + col]
                        out_off = (py * width + px) * 4
                        output[out_off] = pixel[0]
                        output[out_off + 1] = pixel[1]
                        output[out_off + 2] = pixel[2]
                        output[out_off + 3] = pixel[3]

    return output


# ---------------------------------------------------------------------------
# PSP tiled CLUT texture decompression
# ---------------------------------------------------------------------------

def decode_clut_psp(data, clut, width, height, bpp=8):
    """Decode PSP tiled CLUT-indexed texture to RGBA.

    PSP stores indexed texture data in a tiled/swizzled layout rather than
    linear row-major order. The swizzle pattern uses 16-byte-wide strips
    with a tile height of 8 rows.

    For 8-bit (bpp=8): each row of a tile is 16 bytes = 16 pixels
    For 4-bit (bpp=4): each row of a tile is 16 bytes = 32 pixels

    The unswizzle converts the tiled layout to linear, then applies
    the CLUT palette lookup identical to PS2.

    Args:
        data: raw tiled pixel data bytes
        clut: CLUT palette bytes (RGBA32 entries, 4 bytes each)
        width: image width in pixels
        height: image height in pixels
        bpp: bits per pixel (4 or 8)

    Returns:
        bytearray of width*height*4 RGBA bytes, or None on error
    """
    # Unswizzle PSP tile layout to linear
    if bpp == 8:
        row_bytes = width
    elif bpp == 4:
        row_bytes = (width + 1) // 2
    else:
        return None

    linear = _psp_unswizzle(data, row_bytes, height)

    # Apply CLUT lookup using existing function
    return decode_clut_ps2(linear, clut, width, height, bpp)


def _psp_unswizzle(data, row_bytes, height):
    """Unswizzle PSP tile layout to linear pixel data.

    PSP swizzle pattern: 16-byte-wide strips, 8-row tiles.
    Data is stored as a series of 16×8 byte tiles in row-major tile order.

    Args:
        data: swizzled data bytes
        row_bytes: bytes per pixel row (width for 8bpp, width/2 for 4bpp)
        height: image height in rows

    Returns:
        bytearray of linear pixel data
    """
    TILE_W = 16   # bytes per tile row
    TILE_H = 8    # rows per tile

    # Pad row_bytes to multiple of TILE_W
    pitch = max(TILE_W, row_bytes)
    tiles_per_row = (pitch + TILE_W - 1) // TILE_W
    tile_rows = (height + TILE_H - 1) // TILE_H

    output = bytearray(pitch * height)
    src = 0

    for ty in range(tile_rows):
        for tx in range(tiles_per_row):
            for row in range(TILE_H):
                dst_y = ty * TILE_H + row
                if dst_y >= height:
                    src += TILE_W
                    continue
                dst_x = tx * TILE_W
                dst = dst_y * pitch + dst_x
                chunk = min(TILE_W, len(data) - src)
                if chunk <= 0:
                    src += TILE_W
                    continue
                end_dst = min(dst + chunk, len(output))
                copy_len = end_dst - dst
                if copy_len > 0:
                    output[dst:dst + copy_len] = data[src:src + copy_len]
                src += TILE_W

    # Trim to actual row_bytes if pitch was padded
    if pitch != row_bytes:
        trimmed = bytearray(row_bytes * height)
        for y in range(height):
            trimmed[y * row_bytes:(y + 1) * row_bytes] = \
                output[y * pitch:y * pitch + row_bytes]
        return trimmed

    return output


# ---------------------------------------------------------------------------
# DXN / BC5 (Xbox 360 two-channel normal map) decompression
# ---------------------------------------------------------------------------

def decompress_dxn(data, width, height):
    """Decompress DXN (BC5 / ATI2N) texture to RGBA8888.

    DXN stores two independent channels (typically X and Y of a normal map)
    using two BC4 blocks per 4x4 pixel tile. Each BC4 block is identical
    in structure to the DXT5 alpha block (2 endpoints + 48-bit index table).

    The output reconstructs a full normal map:
        R = channel 0 (X)
        G = channel 1 (Y)
        B = derived Z = sqrt(1 - X² - Y²) mapped to 0-255
        A = 255

    Args:
        data: raw DXN compressed bytes
        width: image width in pixels
        height: image height in pixels

    Returns:
        bytearray of width*height*4 RGBA bytes, or None on error
    """
    import math

    blocks_x = max(1, (width + 3) // 4)
    blocks_y = max(1, (height + 3) // 4)
    block_size = 16  # Two BC4 blocks = 8 + 8 bytes

    expected_size = blocks_x * blocks_y * block_size
    if len(data) < expected_size:
        return None

    output = bytearray(width * height * 4)

    for by in range(blocks_y):
        for bx in range(blocks_x):
            block_offset = (by * blocks_x + bx) * block_size

            # Decode two BC4 channels
            red_values = _decode_bc4_block(data, block_offset)
            green_values = _decode_bc4_block(data, block_offset + 8)

            # Write pixels
            for row in range(4):
                py = by * 4 + row
                if py >= height:
                    break
                for col in range(4):
                    px = bx * 4 + col
                    if px >= width:
                        continue
                    idx = row * 4 + col
                    r = red_values[idx]
                    g = green_values[idx]

                    # Reconstruct Z from X, Y (assuming unit normal)
                    nx = (r / 255.0) * 2.0 - 1.0
                    ny = (g / 255.0) * 2.0 - 1.0
                    nz_sq = max(0.0, 1.0 - nx * nx - ny * ny)
                    nz = math.sqrt(nz_sq)
                    b = int(((nz + 1.0) * 0.5) * 255.0 + 0.5)
                    b = max(0, min(255, b))

                    out_off = (py * width + px) * 4
                    output[out_off] = r
                    output[out_off + 1] = g
                    output[out_off + 2] = b
                    output[out_off + 3] = 255

    return output


def _decode_bc4_block(data, offset):
    """Decode a single BC4 block (8 bytes) to 16 uint8 values.

    BC4 is identical in structure to the DXT5 alpha block:
        1 byte: endpoint0
        1 byte: endpoint1
        6 bytes: 4x4 3-bit index table

    Args:
        data: raw block data
        offset: byte offset into data

    Returns:
        list of 16 uint8 values (0-255)
    """
    a0 = data[offset]
    a1 = data[offset + 1]

    # Build interpolation palette
    if a0 > a1:
        palette = [
            a0, a1,
            (6 * a0 + 1 * a1 + 3) // 7,
            (5 * a0 + 2 * a1 + 3) // 7,
            (4 * a0 + 3 * a1 + 3) // 7,
            (3 * a0 + 4 * a1 + 3) // 7,
            (2 * a0 + 5 * a1 + 3) // 7,
            (1 * a0 + 6 * a1 + 3) // 7,
        ]
    else:
        palette = [
            a0, a1,
            (4 * a0 + 1 * a1 + 2) // 5,
            (3 * a0 + 2 * a1 + 2) // 5,
            (2 * a0 + 3 * a1 + 2) // 5,
            (1 * a0 + 4 * a1 + 2) // 5,
            0, 255,
        ]

    # Read 48-bit index table
    bits = struct.unpack_from("<Q", data, offset)[0] >> 16
    bits &= 0xFFFFFFFFFFFF

    values = []
    for i in range(16):
        idx = (bits >> (i * 3)) & 0x07
        values.append(palette[idx])

    return values
