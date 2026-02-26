"""CLUT (Color Lookup Table) texture compression.

Reduces RGBA8888 pixel data to 256-color palettized format (PSMT8).
This format is platform-neutral (no RGB565 encoding) and works
in both XML2 and MUA game engines.

Output:
    palette_data: 256 * 4 = 1024 bytes of RGBA palette entries
    index_data: width * height bytes of palette indices

Uses numpy (bundled with Blender) for fast vectorized quantization.
"""

try:
    import numpy as np
    _HAS_NUMPY = True
except ImportError:
    _HAS_NUMPY = False


def quantize_rgba_to_clut(rgba_data, width, height):
    """Quantize RGBA8888 pixels to 256-color palette + indices.

    Args:
        rgba_data: bytes/bytearray of width*height*4 RGBA pixels
        width: image width
        height: image height

    Returns:
        (palette_data, index_data) tuple:
            palette_data: 1024 bytes (256 RGBA entries)
            index_data: width*height bytes (palette indices)
    """
    if _HAS_NUMPY:
        return _quantize_numpy(rgba_data, width, height)
    return _quantize_pure(rgba_data, width, height)


# ── numpy implementation (fast, used inside Blender) ─────────────────

def _quantize_numpy(rgba_data, width, height):
    pixels = np.frombuffer(rgba_data, dtype=np.uint8).reshape(-1, 4)
    unique, inverse = np.unique(pixels, axis=0, return_inverse=True)

    if len(unique) <= 256:
        # Direct palette — no quantization loss
        palette = np.zeros((256, 4), dtype=np.uint8)
        palette[:len(unique)] = unique
        index_data = inverse.astype(np.uint8)
    else:
        # Weighted median-cut on unique colors
        # Count occurrences of each unique color
        _, counts = np.unique(inverse, return_counts=True)
        palette = _median_cut_np(unique, counts, 256)

        # Nearest-neighbor via ||u-p||^2 = ||u||^2 + ||p||^2 - 2*u·p
        # Uses BLAS matmul for the dot product — much faster than broadcasting
        u32 = unique.astype(np.int32)
        p32 = palette.astype(np.int32)
        u_sq = (u32 * u32).sum(axis=1)       # (N,)
        p_sq = (p32 * p32).sum(axis=1)       # (256,)
        dot = u32 @ p32.T                     # (N, 256) — BLAS
        dist = u_sq[:, np.newaxis] + p_sq[np.newaxis, :] - 2 * dot
        nearest = dist.argmin(axis=1).astype(np.uint8)

        # Map unique→palette index back to all pixels
        index_data = nearest[inverse]

    # Build palette bytes
    palette_data = bytearray(1024)
    for i in range(min(256, len(palette))):
        off = i * 4
        palette_data[off] = int(palette[i, 0])
        palette_data[off + 1] = int(palette[i, 1])
        palette_data[off + 2] = int(palette[i, 2])
        palette_data[off + 3] = int(palette[i, 3])

    return bytes(palette_data), bytes(index_data)


def _median_cut_np(unique_colors, counts, target):
    """Weighted median-cut using numpy arrays with heap-based selection.

    Uses a max-heap to avoid rescanning all boxes every iteration.
    Box scores and split axes are computed once and cached.
    """
    import heapq

    def _box_stats(colors, cnts):
        """Compute (score, best_axis) for a box."""
        if len(colors) < 2:
            return 0, 0
        ranges = colors.max(axis=0).astype(np.int32) - colors.min(axis=0).astype(np.int32)
        axis = int(ranges.argmax())
        return int(ranges[axis]) * int(cnts.sum()), axis

    # Heap entries: (-score, unique_id, colors, counts, axis)
    # Negative score for max-heap behavior with heapq (min-heap)
    next_id = 0
    heap = []
    done = []  # boxes that can't be split further

    score, axis = _box_stats(unique_colors, counts)
    heapq.heappush(heap, (-score, next_id, unique_colors.copy(), counts.copy(), axis))
    next_id += 1

    while len(done) + len(heap) < target and heap:
        neg_score, _, colors, cnts, axis = heapq.heappop(heap)

        if -neg_score <= 0 or len(colors) < 2:
            done.append((colors, cnts))
            continue

        # Sort by chosen axis and split at weighted median
        order = colors[:, axis].argsort()
        colors = colors[order]
        cnts = cnts[order]

        cumsum = cnts.cumsum()
        mid = int(np.searchsorted(cumsum, cumsum[-1] // 2))
        mid = max(1, min(mid, len(colors) - 1))

        for c, n in [(colors[:mid], cnts[:mid]), (colors[mid:], cnts[mid:])]:
            s, a = _box_stats(c, n)
            if s <= 0 or len(c) < 2:
                done.append((c, n))
            else:
                heapq.heappush(heap, (-s, next_id, c, n, a))
                next_id += 1

    # Collect all boxes
    all_boxes = done
    while heap:
        _, _, c, n, _ = heapq.heappop(heap)
        all_boxes.append((c, n))

    # Weighted average of each box
    palette = np.zeros((target, 4), dtype=np.uint8)
    for i, (colors, cnts) in enumerate(all_boxes):
        if i >= target:
            break
        if len(colors) == 0:
            continue
        weights = cnts.astype(np.float64)
        total = weights.sum()
        avg = (colors.astype(np.float64) * weights[:, np.newaxis]).sum(axis=0) / total
        palette[i] = np.clip(avg + 0.5, 0, 255).astype(np.uint8)

    return palette


# ── pure Python fallback ─────────────────────────────────────────────

def _quantize_pure(rgba_data, width, height):
    """Pure Python fallback for environments without numpy."""
    pixel_count = width * height
    mv = memoryview(rgba_data)

    # Build histogram: packed uint32 → count
    histogram = {}
    for i in range(pixel_count):
        off = i * 4
        key = (mv[off] | (mv[off + 1] << 8) |
               (mv[off + 2] << 16) | (mv[off + 3] << 24))
        histogram[key] = histogram.get(key, 0) + 1

    if len(histogram) <= 256:
        palette_keys = list(histogram.keys())
        while len(palette_keys) < 256:
            palette_keys.append(0)
        color_to_idx = {c: i for i, c in enumerate(palette_keys)}
        index_data = bytearray(pixel_count)
        for i in range(pixel_count):
            off = i * 4
            key = (mv[off] | (mv[off + 1] << 8) |
                   (mv[off + 2] << 16) | (mv[off + 3] << 24))
            index_data[i] = color_to_idx[key]
        palette_data = bytearray(1024)
        for i, key in enumerate(palette_keys[:256]):
            off = i * 4
            palette_data[off] = key & 0xFF
            palette_data[off + 1] = (key >> 8) & 0xFF
            palette_data[off + 2] = (key >> 16) & 0xFF
            palette_data[off + 3] = (key >> 24) & 0xFF
        return bytes(palette_data), bytes(index_data)

    # Weighted median-cut on unique colors
    color_list = list(histogram.items())
    palette_packed = _median_cut_pure(color_list, 256)

    # Build nearest-color cache for unique colors only
    pal_r = [pk & 0xFF for pk in palette_packed]
    pal_g = [(pk >> 8) & 0xFF for pk in palette_packed]
    pal_b = [(pk >> 16) & 0xFF for pk in palette_packed]
    pal_a = [(pk >> 24) & 0xFF for pk in palette_packed]
    pal_exact = {pk: i for i, pk in enumerate(palette_packed)}

    nearest_cache = {}
    for color_key in histogram:
        if color_key in pal_exact:
            nearest_cache[color_key] = pal_exact[color_key]
        else:
            cr = color_key & 0xFF
            cg = (color_key >> 8) & 0xFF
            cb = (color_key >> 16) & 0xFF
            ca = (color_key >> 24) & 0xFF
            best_idx = 0
            best_dist = 0x7FFFFFFF
            for j in range(256):
                dr = cr - pal_r[j]
                dg = cg - pal_g[j]
                db = cb - pal_b[j]
                da = ca - pal_a[j]
                d = dr * dr + dg * dg + db * db + da * da
                if d < best_dist:
                    best_dist = d
                    best_idx = j
                    if d == 0:
                        break
            nearest_cache[color_key] = best_idx

    # Map all pixels
    index_data = bytearray(pixel_count)
    for i in range(pixel_count):
        off = i * 4
        key = (mv[off] | (mv[off + 1] << 8) |
               (mv[off + 2] << 16) | (mv[off + 3] << 24))
        index_data[i] = nearest_cache[key]

    palette_data = bytearray(1024)
    for i, pk in enumerate(palette_packed[:256]):
        off = i * 4
        palette_data[off] = pk & 0xFF
        palette_data[off + 1] = (pk >> 8) & 0xFF
        palette_data[off + 2] = (pk >> 16) & 0xFF
        palette_data[off + 3] = (pk >> 24) & 0xFF

    return bytes(palette_data), bytes(index_data)


def _median_cut_pure(color_list, target_count):
    """Pure Python weighted median-cut. See _median_cut_np for numpy version."""
    if not color_list:
        return [0] * target_count

    boxes = [color_list]

    while len(boxes) < target_count:
        best_box_idx = -1
        best_score = -1
        for i, box in enumerate(boxes):
            if len(box) < 2:
                continue
            max_range = 0
            for shift in (0, 8, 16, 24):
                vals = [(c >> shift) & 0xFF for c, _ in box]
                r = max(vals) - min(vals)
                if r > max_range:
                    max_range = r
            total_count = sum(cnt for _, cnt in box)
            score = max_range * total_count
            if score > best_score:
                best_score = score
                best_box_idx = i

        if best_box_idx < 0 or best_score <= 0:
            break

        box = boxes[best_box_idx]
        best_axis_shift = 0
        best_range = 0
        for shift in (0, 8, 16, 24):
            vals = [(c >> shift) & 0xFF for c, _ in box]
            r = max(vals) - min(vals)
            if r > best_range:
                best_range = r
                best_axis_shift = shift

        box.sort(key=lambda e: (e[0] >> best_axis_shift) & 0xFF)
        total = sum(cnt for _, cnt in box)
        half = total // 2
        accum = 0
        mid = len(box) // 2
        for j, (_, cnt) in enumerate(box):
            accum += cnt
            if accum >= half and j > 0:
                mid = j
                break
        if mid == 0:
            mid = 1
        boxes[best_box_idx] = box[:mid]
        boxes.append(box[mid:])

    palette = []
    for box in boxes:
        if not box:
            palette.append(0)
            continue
        total_w = sum(cnt for _, cnt in box)
        sr = sg = sb = sa = 0
        for pk, cnt in box:
            sr += (pk & 0xFF) * cnt
            sg += ((pk >> 8) & 0xFF) * cnt
            sb += ((pk >> 16) & 0xFF) * cnt
            sa += ((pk >> 24) & 0xFF) * cnt
        r = sr // total_w
        g = sg // total_w
        b = sb // total_w
        a = sa // total_w
        palette.append(r | (g << 8) | (b << 16) | (a << 24))

    while len(palette) < target_count:
        palette.append(0)
    return palette[:target_count]
