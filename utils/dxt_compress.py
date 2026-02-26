"""DXT texture compression and mipmap generation.

Compresses RGBA8888 pixel data to DXT5 format for IGB export.
DXT5 (pfmt=16) is supported by both XML2 and MUA game engines.

DXT5 block structure (16 bytes per 4x4 pixel block):
    Bytes 0-1:  Two 8-bit alpha endpoints (alpha0, alpha1)
    Bytes 2-7:  4x4 3-bit alpha index table (48 bits = 6 bytes)
    Bytes 8-9:  RGB565 color endpoint 0
    Bytes 10-11: RGB565 color endpoint 1
    Bytes 12-15: 4x4 2-bit color index table
"""

import struct


def compress_rgba_to_dxt5(rgba_data, width, height, swap_rb=False):
    """Compress RGBA8888 pixel data to DXT5 format.

    Args:
        rgba_data: bytes/bytearray of width*height*4 RGBA pixels (row-major)
        width: image width in pixels (must be multiple of 4, or will be padded)
        height: image height in pixels (must be multiple of 4, or will be padded)
        swap_rb: if True, swap R and B channels before compression (for MUA PC
                 which expects BGR565 color endpoints in DXT blocks)

    Returns:
        bytes of DXT5-compressed data
    """
    if swap_rb:
        rgba_data = _swap_rb_channels(rgba_data)
    blocks_x = max(1, (width + 3) // 4)
    blocks_y = max(1, (height + 3) // 4)

    output = bytearray()

    for by in range(blocks_y):
        for bx in range(blocks_x):
            block = _extract_block(rgba_data, width, height, bx * 4, by * 4)
            compressed = _compress_dxt5_block(block)
            output.extend(compressed)

    return bytes(output)


def compress_rgba_to_dxt3(rgba_data, width, height):
    """Compress RGBA8888 pixel data to DXT3 format (legacy, kept for reference).

    Args:
        rgba_data: bytes/bytearray of width*height*4 RGBA pixels (row-major)
        width: image width in pixels (must be multiple of 4, or will be padded)
        height: image height in pixels (must be multiple of 4, or will be padded)

    Returns:
        bytes of DXT3-compressed data
    """
    blocks_x = max(1, (width + 3) // 4)
    blocks_y = max(1, (height + 3) // 4)

    output = bytearray()

    for by in range(blocks_y):
        for bx in range(blocks_x):
            block = _extract_block(rgba_data, width, height, bx * 4, by * 4)
            compressed = _compress_dxt3_block(block)
            output.extend(compressed)

    return bytes(output)


def generate_mipmaps(rgba_data, width, height, min_size=1):
    """Generate a mipmap chain from RGBA8888 data.

    Uses box-filter (2x2 averaging) to create each successive level.
    Generates levels until both dimensions reach min_size.

    Args:
        rgba_data: bytes/bytearray of width*height*4 RGBA pixels
        width: image width in pixels
        height: image height in pixels
        min_size: minimum dimension to stop at (default 1)

    Returns:
        list of (rgba_data, width, height) tuples for each mipmap level,
        starting from the next level down (does NOT include the base image)
    """
    mipmaps = []
    current_data = rgba_data
    current_w = width
    current_h = height

    while current_w > min_size or current_h > min_size:
        new_w = max(min_size, current_w // 2)
        new_h = max(min_size, current_h // 2)
        new_data = _downsample_2x(current_data, current_w, current_h, new_w, new_h)
        mipmaps.append((new_data, new_w, new_h))
        current_data = new_data
        current_w = new_w
        current_h = new_h

    return mipmaps


def compress_with_mipmaps(rgba_data, width, height, swap_rb=False):
    """Compress base image + generate and compress all mipmap levels as DXT5.

    Args:
        rgba_data: bytes/bytearray of width*height*4 RGBA pixels
        width: image width
        height: image height
        swap_rb: if True, swap R and B channels for MUA PC BGR565 encoding

    Returns:
        list of (compressed_data, width, height) tuples.
        First entry is the base image, followed by mipmap levels.
    """
    result = []

    # Base image
    compressed = compress_rgba_to_dxt5(rgba_data, width, height, swap_rb=swap_rb)
    result.append((compressed, width, height))

    # Generate mipmaps
    mipmaps = generate_mipmaps(rgba_data, width, height)
    for mip_data, mip_w, mip_h in mipmaps:
        mip_compressed = compress_rgba_to_dxt5(mip_data, mip_w, mip_h, swap_rb=swap_rb)
        result.append((mip_compressed, mip_w, mip_h))

    return result


# ===========================================================================
# Internal helpers
# ===========================================================================

def _extract_block(rgba_data, width, height, x0, y0):
    """Extract a 4x4 pixel block from RGBA data.

    Pixels outside image bounds are clamped to the nearest edge pixel.

    Returns:
        list of 16 tuples (R, G, B, A), row by row
    """
    block = []
    for row in range(4):
        py = min(y0 + row, height - 1)
        for col in range(4):
            px = min(x0 + col, width - 1)
            offset = (py * width + px) * 4
            r = rgba_data[offset]
            g = rgba_data[offset + 1]
            b = rgba_data[offset + 2]
            a = rgba_data[offset + 3]
            block.append((r, g, b, a))
    return block


def _compress_dxt3_block(block):
    """Compress a 4x4 pixel block to 16 bytes of DXT3 data.

    Args:
        block: list of 16 (R, G, B, A) tuples

    Returns:
        bytes of 16 bytes: 8 alpha + 8 color
    """
    # --- Alpha: explicit 4-bit per pixel (8 bytes) ---
    alpha_bytes = bytearray(8)
    for i in range(16):
        a = block[i][3]
        # Quantize 8-bit alpha to 4-bit
        a4 = (a + 8) // 17  # round to nearest 4-bit value
        if a4 > 15:
            a4 = 15
        byte_idx = i // 2
        if i % 2 == 0:
            alpha_bytes[byte_idx] |= a4
        else:
            alpha_bytes[byte_idx] |= (a4 << 4)

    # --- Color: DXT1 block (8 bytes) ---
    color_data = _compress_dxt1_block(block)

    return bytes(alpha_bytes) + color_data


def _compress_dxt5_block(block):
    """Compress a 4x4 pixel block to 16 bytes of DXT5 data.

    Args:
        block: list of 16 (R, G, B, A) tuples

    Returns:
        bytes of 16 bytes: 8 alpha (interpolated) + 8 color
    """
    # --- Alpha: interpolated endpoints + 3-bit indices (8 bytes) ---
    alphas = [block[i][3] for i in range(16)]
    alpha0 = max(alphas)
    alpha1 = min(alphas)

    if alpha0 == alpha1:
        # All same alpha — use endpoints equal, all indices 0
        alpha_block = struct.pack('<BB', alpha0, alpha1) + b'\x00\x00\x00\x00\x00\x00'
    else:
        # Build 8-entry palette (alpha0 > alpha1 → 8 interpolated values)
        palette = [alpha0, alpha1]
        for i in range(1, 7):
            palette.append(((7 - i) * alpha0 + i * alpha1 + 3) // 7)

        # Find best index for each pixel
        indices = []
        for a in alphas:
            best_idx = 0
            best_dist = abs(a - palette[0])
            for j in range(1, 8):
                d = abs(a - palette[j])
                if d < best_dist:
                    best_dist = d
                    best_idx = j
            indices.append(best_idx)

        # Pack 16 x 3-bit indices into 6 bytes (48 bits), little-endian
        # Bits 0-2 = pixel 0, bits 3-5 = pixel 1, etc.
        bits = 0
        for i in range(16):
            bits |= (indices[i] << (i * 3))

        alpha_block = struct.pack('<BB', alpha0, alpha1) + struct.pack('<Q', bits)[:6]

    # --- Color: DXT1 block (8 bytes) ---
    color_data = _compress_dxt1_block(block)

    return alpha_block + color_data


def _compress_dxt1_block(block):
    """Compress a 4x4 pixel block's RGB channels to DXT1 (8 bytes).

    Uses a simple min/max color endpoint selection strategy.

    Args:
        block: list of 16 (R, G, B, A) tuples

    Returns:
        bytes of 8 bytes: 2 RGB565 endpoints + 4 bytes index table
    """
    # Find min/max RGB (bounding box in color space)
    min_r = min_g = min_b = 255
    max_r = max_g = max_b = 0

    for r, g, b, a in block:
        if r < min_r: min_r = r
        if g < min_g: min_g = g
        if b < min_b: min_b = b
        if r > max_r: max_r = r
        if g > max_g: max_g = g
        if b > max_b: max_b = b

    # Inset the bounding box slightly for better quality
    inset_r = (max_r - min_r) >> 4
    inset_g = (max_g - min_g) >> 4
    inset_b = (max_b - min_b) >> 4
    min_r = min(255, min_r + inset_r)
    min_g = min(255, min_g + inset_g)
    min_b = min(255, min_b + inset_b)
    max_r = max(0, max_r - inset_r)
    max_g = max(0, max_g - inset_g)
    max_b = max(0, max_b - inset_b)

    # Encode as RGB565
    c0 = _rgba_to_rgb565(max_r, max_g, max_b)
    c1 = _rgba_to_rgb565(min_r, min_g, min_b)

    # Ensure c0 > c1 for 4-color mode (no transparency)
    if c0 == c1:
        # All same color — indices will all be 0
        return struct.pack("<HHI", c0, c1, 0)

    if c0 < c1:
        c0, c1 = c1, c0
        max_r, min_r = min_r, max_r
        max_g, min_g = min_g, max_g
        max_b, min_b = min_b, max_b

    # Build 4-color palette (in RGB space for distance calculations)
    colors = [
        (max_r, max_g, max_b),  # c0
        (min_r, min_g, min_b),  # c1
        ((2 * max_r + min_r + 1) // 3, (2 * max_g + min_g + 1) // 3, (2 * max_b + min_b + 1) // 3),  # 2/3 c0 + 1/3 c1
        ((max_r + 2 * min_r + 1) // 3, (max_g + 2 * min_g + 1) // 3, (max_b + 2 * min_b + 1) // 3),  # 1/3 c0 + 2/3 c1
    ]

    # Assign each pixel to closest palette color
    indices = 0
    for i in range(16):
        r, g, b = block[i][0], block[i][1], block[i][2]
        best_idx = 0
        best_dist = 0x7FFFFFFF

        for j, (pr, pg, pb) in enumerate(colors):
            dr = r - pr
            dg = g - pg
            db = b - pb
            dist = dr * dr + dg * dg + db * db
            if dist < best_dist:
                best_dist = dist
                best_idx = j

        indices |= (best_idx << (i * 2))

    return struct.pack("<HHI", c0, c1, indices)


def _rgba_to_rgb565(r, g, b):
    """Convert 8-bit RGB to RGB565."""
    return ((r >> 3) << 11) | ((g >> 2) << 5) | (b >> 3)


def _swap_rb_channels(rgba_data):
    """Swap R and B bytes in RGBA pixel data.

    Used for MUA PC export: MUA expects BGR565 color endpoints in DXT blocks.
    By pre-swapping R/B in the source RGBA, the standard RGB565 encoder
    produces BGR565 output.
    """
    data = bytearray(rgba_data)
    for i in range(0, len(data), 4):
        data[i], data[i + 2] = data[i + 2], data[i]
    return bytes(data)


def _downsample_2x(rgba_data, src_w, src_h, dst_w, dst_h):
    """Downsample RGBA image by 2x using box filter.

    Averages each 2x2 block of pixels.

    Args:
        rgba_data: source RGBA bytes
        src_w, src_h: source dimensions
        dst_w, dst_h: destination dimensions

    Returns:
        bytearray of dst_w * dst_h * 4 RGBA bytes
    """
    output = bytearray(dst_w * dst_h * 4)

    for dy in range(dst_h):
        for dx in range(dst_w):
            # Source 2x2 block
            sx = dx * 2
            sy = dy * 2

            # Gather up to 4 source pixels (handle edge cases)
            r_sum = g_sum = b_sum = a_sum = 0
            count = 0

            for oy in range(2):
                py = min(sy + oy, src_h - 1)
                for ox in range(2):
                    px = min(sx + ox, src_w - 1)
                    offset = (py * src_w + px) * 4
                    r_sum += rgba_data[offset]
                    g_sum += rgba_data[offset + 1]
                    b_sum += rgba_data[offset + 2]
                    a_sum += rgba_data[offset + 3]
                    count += 1

            dst_offset = (dy * dst_w + dx) * 4
            output[dst_offset] = r_sum // count
            output[dst_offset + 1] = g_sum // count
            output[dst_offset + 2] = b_sum // count
            output[dst_offset + 3] = a_sum // count

    return output
