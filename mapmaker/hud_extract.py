"""HUD head texture extractor — extracts character portrait PNGs from IGB files.

Batch-extracts hud_head_*.igb textures to PNG for use in the conversation editor.
Uses the existing IGB parser + DXT3 decompressor — no external dependencies.
"""

import os
import struct
import zlib
from pathlib import Path


def write_png(rgba_data, width, height, path):
    """Write RGBA pixel data as a PNG file (pure stdlib, no PIL).

    Uses unfiltered rows with zlib deflate — produces valid PNGs.
    """
    def _chunk(chunk_type, data):
        c = chunk_type + data
        return struct.pack(">I", len(data)) + c + struct.pack(">I", zlib.crc32(c) & 0xFFFFFFFF)

    # PNG signature
    sig = b'\x89PNG\r\n\x1a\n'

    # IHDR: width, height, bit_depth=8, color_type=6 (RGBA), compression=0, filter=0, interlace=0
    ihdr_data = struct.pack(">IIBBBBB", width, height, 8, 6, 0, 0, 0)
    ihdr = _chunk(b'IHDR', ihdr_data)

    # IDAT: raw pixel rows with filter byte 0 (None) prepended
    raw_rows = bytearray()
    for y in range(height):
        raw_rows.append(0)  # filter byte = None
        offset = y * width * 4
        raw_rows.extend(rgba_data[offset:offset + width * 4])

    compressed = zlib.compress(bytes(raw_rows), 9)
    idat = _chunk(b'IDAT', compressed)

    # IEND
    iend = _chunk(b'IEND', b'')

    with open(path, 'wb') as f:
        f.write(sig + ihdr + idat + iend)


def extract_hud_head_png(igb_path, output_dir):
    """Extract the first texture from a hud_head IGB file to PNG.

    Args:
        igb_path: Path to hud_head_XXXX.igb
        output_dir: Directory to write PNG to

    Returns:
        Path to output PNG, or None on failure
    """
    from ..igb_format.igb_reader import IGBReader
    from ..igb_format.igb_objects import IGBObject
    from ..scene_graph.sg_materials import extract_image
    from ..utils.image_convert import convert_image_to_rgba

    igb_path = Path(igb_path)
    output_dir = Path(output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)

    stem = igb_path.stem  # e.g. "hud_head_0101"
    out_path = output_dir / f"{stem}.png"

    try:
        reader = IGBReader(str(igb_path))
        reader.read()
    except Exception:
        return None

    # Find the first igImage object and extract it
    for obj in reader.objects:
        if not isinstance(obj, IGBObject):
            continue
        if not obj.is_type(b"igImage"):
            continue

        parsed_img = extract_image(reader, obj)
        if parsed_img is None or parsed_img.pixel_data is None:
            continue

        rgba = convert_image_to_rgba(parsed_img)
        if rgba is None or parsed_img.width == 0 or parsed_img.height == 0:
            continue

        write_png(rgba, parsed_img.width, parsed_img.height, str(out_path))
        return str(out_path)

    return None


def extract_all_hud_heads(hud_dir, output_dir):
    """Batch-extract all hud_head_*.igb textures to PNG.

    Skips files that already have a cached PNG.

    Args:
        hud_dir: Directory containing hud_head_*.igb files
        output_dir: Directory to write PNGs to

    Returns:
        dict mapping stem (e.g. 'hud_head_0101') to PNG path
    """
    hud_dir = Path(hud_dir)
    output_dir = Path(output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)

    results = {}
    extracted = 0
    skipped = 0

    igb_files = sorted(hud_dir.glob("hud_head_*.igb"))

    for igb_path in igb_files:
        stem = igb_path.stem
        out_path = output_dir / f"{stem}.png"

        # Skip if already extracted
        if out_path.exists():
            results[stem] = str(out_path)
            skipped += 1
            continue

        result = extract_hud_head_png(igb_path, output_dir)
        if result:
            results[stem] = result
            extracted += 1

    return results, extracted, skipped


def get_hud_cache_dir():
    """Get the HUD cache directory path (alongside this module)."""
    return Path(__file__).parent / "hud_cache"


def get_cached_hud_path(hud_name):
    """Get path to a cached HUD head PNG, or None if not extracted.

    Args:
        hud_name: HUD head name (e.g. 'hud_head_0101')

    Returns:
        Path string if cached PNG exists, None otherwise
    """
    cache_dir = get_hud_cache_dir()
    png_path = cache_dir / f"{hud_name}.png"
    if png_path.exists():
        return str(png_path)
    return None
