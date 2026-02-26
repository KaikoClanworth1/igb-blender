"""ZAM minimap file I/O — read/write .zam automap files.

Supports two format versions:
  v9 (XML2, ML Proto): 8 bytes/vertex — x:i16, y:i16, r:u8, g:u8, b:u8, a:u8
  v10 (MUA PS2/PSP/Wii): 6 bytes/vertex — x:i16, y:i16, alpha:u8, pad:u8

Common layout:
  - 8-byte header: version(u16), offset_x(i16), offset_y(i16), num_vertices(u16)
  - Vertex array: num_vertices * (8 or 6) bytes depending on version
  - 41x41 spatial grid: 1681 * int32 (-1=empty, >=0=polygon index)
  - Polygon data: repeated (count:u16, indices:u16[count]) — triangle strips

Grid cell assignment formula (reverse-engineered, 99.94% validated):
  - cell_size = 240 world units (constant across all games)
  - origin_x = offset_x * 240, origin_y = offset_y * 240
  - x_cell = floor((X - origin_x) / 240)
  - y_cell = floor((Y - origin_y) / 240)
  - cell_idx = x_cell * 41 + y_cell  (X-major storage, NOT Y-major!)
  - offset_x = floor(min_x / 240), offset_y = floor(min_y / 240)
"""

import math
import struct

import bpy
import bmesh
from mathutils import Vector


# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

GRID_SIZE = 41
GRID_CELLS = GRID_SIZE * GRID_SIZE
GRID_BYTES = GRID_CELLS * 4
CELL_SIZE = 240  # world units per grid cell (constant across all games)


# ---------------------------------------------------------------------------
# Read
# ---------------------------------------------------------------------------

def parse_zam(filepath):
    """Parse a .zam minimap file.

    Handles both v9 (8 bytes/vertex) and v10 (6 bytes/vertex) formats.
    All vertices are normalized to (x, y, r, g, b, a) tuples regardless
    of source version.

    Returns dict with keys:
        version (int): File version (9=XML2/ML, 10=MUA)
        offset (tuple): (offset_x, offset_y) grid alignment offsets
        vertices (list): [(x, y, r, g, b, a), ...] vertex data
        grid (list): 1681 int32 spatial grid values
        polygons (list): [indices_list, ...] triangle strip index arrays
    """
    with open(filepath, 'rb') as f:
        data = f.read()

    version = struct.unpack_from('<H', data, 0)[0]
    offset_x = struct.unpack_from('<h', data, 2)[0]
    offset_y = struct.unpack_from('<h', data, 4)[0]
    num_vertices = struct.unpack_from('<H', data, 6)[0]

    # Version determines bytes per vertex
    # v9: 8 bytes (x:i16, y:i16, r:u8, g:u8, b:u8, a:u8)
    # v10: 6 bytes (x:i16, y:i16, alpha:u8, pad:u8) — RGB always white
    bpv = 6 if version >= 10 else 8

    # Vertex data
    vertices = []
    for i in range(num_vertices):
        off = 8 + i * bpv
        if off + bpv > len(data):
            break
        if bpv == 8:
            x, y, r, g, b, a = struct.unpack_from('<hhBBBB', data, off)
        else:
            x, y, alpha, _pad = struct.unpack_from('<hhBB', data, off)
            r, g, b, a = 255, 255, 255, alpha
        vertices.append((x, y, r, g, b, a))

    # 41x41 spatial lookup grid
    grid_start = 8 + num_vertices * bpv
    raw_grid = []
    if grid_start + GRID_BYTES <= len(data):
        for i in range(GRID_CELLS):
            raw_grid.append(struct.unpack_from('<I', data, grid_start + i * 4)[0])

    # Detect v10 sentinel variant: polygon section starts with 0xFFFF
    poly_start = grid_start + GRID_BYTES
    sentinel = False
    if poly_start + 2 <= len(data):
        first_u16 = struct.unpack_from('<H', data, poly_start)[0]
        if first_u16 == 0xFFFF:
            sentinel = True

    grid = []
    if sentinel:
        # Sentinel variant: grid hi-word = polygon index
        for val in raw_grid:
            if val == 0xFFFFFFFF or (val >> 16) == 0xFFFF:
                grid.append(-1)
            else:
                grid.append((val >> 16) & 0xFFFF)
        pos = poly_start + 2  # skip sentinel
    else:
        # Normal variant: simple int32 polygon indices
        for val in raw_grid:
            grid.append(val if val != 0xFFFFFFFF else -1)
        pos = poly_start

    # Polygon index data (triangle strips)
    polygons = []
    while pos + 2 <= len(data):
        n = struct.unpack_from('<H', data, pos)[0]
        pos += 2
        if n == 0 or n > 5000:
            break
        if pos + n * 2 > len(data):
            break
        indices = list(struct.unpack_from(f'<{n}H', data, pos))
        pos += n * 2
        if all(idx < num_vertices for idx in indices):
            polygons.append(indices)

    return {
        'version': version,
        'offset': (offset_x, offset_y),
        'vertices': vertices,
        'grid': grid,
        'polygons': polygons,
    }


def tristrip_to_triangles(indices):
    """Convert triangle strip indices to individual triangles.

    Handles degenerate triangles (repeated indices) as strip separators
    and alternates winding order for correct face normals.

    Returns list of (i0, i1, i2) tuples.
    """
    triangles = []
    for i in range(len(indices) - 2):
        i0, i1, i2 = indices[i], indices[i + 1], indices[i + 2]
        # Skip degenerate triangles (strip separators)
        if i0 == i1 or i1 == i2 or i0 == i2:
            continue
        # Alternate winding for correct normals
        if i % 2 == 0:
            triangles.append((i0, i1, i2))
        else:
            triangles.append((i1, i0, i2))
    return triangles


# ---------------------------------------------------------------------------
# Create Blender mesh
# ---------------------------------------------------------------------------

def create_mesh_from_zam(name, zam_data, scale=0.01):
    """Create a Blender mesh from ZAM indexed polygon data.

    Uses shared indexed vertices and builds triangles from polygon
    triangle strip data. Does NOT link the object to any collection —
    the caller is responsible for collection management.

    Args:
        name: Object name
        zam_data: Dict from parse_zam()
        scale: Coordinate scale factor (default 0.01)

    Returns:
        (bpy.types.Object, int): Tuple of (object, triangle_count)
    """
    mesh = bpy.data.meshes.new(name)
    obj = bpy.data.objects.new(name, mesh)

    bm = bmesh.new()
    color_layer = bm.loops.layers.color.new("Color")

    verts = zam_data['vertices']

    # Create all BMesh vertices from the shared vertex pool
    bm_verts = []
    for x, y, r, g, b, a in verts:
        pos = Vector((x * scale, y * scale, 0.0))
        bm_verts.append(bm.verts.new(pos))

    bm.verts.ensure_lookup_table()

    # Create triangles from polygon strip data
    tri_count = 0
    for poly_indices in zam_data['polygons']:
        for i0, i1, i2 in tristrip_to_triangles(poly_indices):
            try:
                face = bm.faces.new([bm_verts[i0], bm_verts[i1], bm_verts[i2]])
                # Assign vertex colors from the vertex data
                for loop, idx in zip(face.loops, [i0, i1, i2]):
                    v = verts[idx]
                    loop[color_layer] = (v[2] / 255.0, v[3] / 255.0,
                                         v[4] / 255.0, v[5] / 255.0)
                tri_count += 1
            except ValueError:
                pass  # Skip duplicate faces

    bm.to_mesh(mesh)
    bm.free()

    return obj, tri_count


# ---------------------------------------------------------------------------
# Write
# ---------------------------------------------------------------------------

def _triangles_to_strip(triangles):
    """Convert a list of triangles into a triangle strip with degenerate separators.

    Each triangle is (i0, i1, i2). The output is a single index list where
    degenerate triangles (repeated indices) separate individual triangles
    that couldn't be connected naturally.

    The game engine renders these as triangle strips, skipping degenerates.

    Returns:
        list of vertex indices forming a triangle strip
    """
    if not triangles:
        return []

    if len(triangles) == 1:
        return list(triangles[0])

    # Simple approach: concatenate triangles with degenerate connectors
    # More sophisticated strip-building could be done, but degenerate
    # connectors are the standard approach and match original game files.
    strip = list(triangles[0])
    for tri in triangles[1:]:
        # Insert degenerate connector: repeat last index of previous tri,
        # then first index of next tri
        last = strip[-1]
        first = tri[0]
        strip.append(last)   # degenerate
        strip.append(first)  # degenerate
        # If the triangle count so far is odd, we need an extra degenerate
        # to preserve correct winding order
        # Count non-degenerate triangles to determine parity
        # Simpler: check if current strip length is even or odd
        # After appending 2 connectors, the next triangle starts at an even
        # offset from the beginning. We need winding to alternate correctly.
        if (len(strip) - 3) % 2 != 0:
            strip.append(first)  # extra degenerate for winding correction
        strip.extend(tri)

    return strip


def write_zam(filepath, obj, scale=100.0, version=9):
    """Write a Blender mesh to .zam format.

    Exports the mesh with a 41x41 spatial grid where each grid cell
    maps to exactly one polygon strip entry containing ALL triangles
    for that cell. This matches the original game format:
    - Grid cell → polygon index (1:1 bijection)
    - Each polygon is a triangle strip (with degenerate separators)

    Supports:
      v9 (XML2): 8 bytes/vertex — x:i16, y:i16, r:u8, g:u8, b:u8, a:u8
      v10 (MUA): 6 bytes/vertex — x:i16, y:i16, alpha:u8, pad:u8

    Args:
        filepath: Output path
        obj: Blender mesh object
        scale: Coordinate scale factor (default 100.0)
        version: Output format version (9=XML2, 10=MUA). Default 9.

    Returns:
        Number of polygons written
    """
    mesh = obj.data
    if obj.mode != 'OBJECT':
        bpy.ops.object.mode_set(mode='OBJECT')

    # Collect unique vertices and build index map
    vert_map = {}  # (x, y) -> index
    vertices = []
    color_layer = None
    if mesh.vertex_colors:
        color_layer = mesh.vertex_colors.active

    for poly in mesh.polygons:
        for loop_idx in poly.loop_indices:
            vi = mesh.loops[loop_idx].vertex_index
            co = mesh.vertices[vi].co
            x = int(co.x * scale)
            y = int(co.y * scale)
            key = (x, y)
            if key not in vert_map:
                if color_layer:
                    c = color_layer.data[loop_idx].color
                    a = int(c[3] * 255) if len(c) > 3 else 255
                else:
                    a = 203
                vert_map[key] = len(vertices)
                vertices.append((x, y, 255, 255, 255, a))

    # Build triangle list from mesh faces
    triangles = []  # list of (i0, i1, i2) tuples
    for poly in mesh.polygons:
        if len(poly.vertices) < 3:
            continue
        indices = []
        for loop_idx in poly.loop_indices:
            vi = mesh.loops[loop_idx].vertex_index
            co = mesh.vertices[vi].co
            x = int(co.x * scale)
            y = int(co.y * scale)
            indices.append(vert_map[(x, y)])
        # Fan-triangulate n-gons
        for i in range(1, len(indices) - 1):
            triangles.append((indices[0], indices[i], indices[i + 1]))

    # Calculate coordinate bounds and grid offsets
    if vertices:
        xs = [v[0] for v in vertices]
        ys = [v[1] for v in vertices]
        min_x, max_x = min(xs), max(xs)
        min_y, max_y = min(ys), max(ys)
    else:
        min_x = max_x = min_y = max_y = 0

    # Grid origin offsets (in cell units) — matches game formula
    offset_x = math.floor(min_x / CELL_SIZE)
    offset_y = math.floor(min_y / CELL_SIZE)
    origin_x = offset_x * CELL_SIZE
    origin_y = offset_y * CELL_SIZE

    # Group triangles by grid cell (each cell gets ALL its triangles)
    # Grid is X-major: cell_idx = x_cell * 41 + y_cell
    cell_triangles = {}  # cell_index -> list of (i0, i1, i2) triangles
    for tri in triangles:
        # Use centroid for grid cell assignment
        cx = sum(vertices[i][0] for i in tri) / 3.0
        cy = sum(vertices[i][1] for i in tri) / 3.0
        gx = min(GRID_SIZE - 1, max(0, int(math.floor((cx - origin_x) / CELL_SIZE))))
        gy = min(GRID_SIZE - 1, max(0, int(math.floor((cy - origin_y) / CELL_SIZE))))
        cell = gx * GRID_SIZE + gy  # X-major storage!
        if cell not in cell_triangles:
            cell_triangles[cell] = []
        cell_triangles[cell].append(tri)

    # Build polygon strips — one per occupied grid cell
    # Polygons are ordered by cell index; grid stores polygon index
    polygons = []
    grid = [-1] * GRID_CELLS

    for cell in sorted(cell_triangles.keys()):
        poly_idx = len(polygons)
        grid[cell] = poly_idx
        strip = _triangles_to_strip(cell_triangles[cell])
        polygons.append(strip)

    # Write binary file
    with open(filepath, 'wb') as f:
        # Header
        f.write(struct.pack('<H', version))
        f.write(struct.pack('<h', offset_x))
        f.write(struct.pack('<h', offset_y))
        f.write(struct.pack('<H', len(vertices)))

        # Vertex data
        for x, y, r, g, b, a in vertices:
            if version >= 10:
                # v10 (MUA): 6 bytes — x, y, alpha, pad
                f.write(struct.pack('<hhBB', x, y, a, 0))
            else:
                # v9 (XML2): 8 bytes — x, y, r, g, b, a
                f.write(struct.pack('<hhBBBB', x, y, r, g, b, a))

        # Spatial grid
        for val in grid:
            f.write(struct.pack('<i', val))

        # Polygon data — each entry is a triangle strip for one grid cell
        for strip in polygons:
            f.write(struct.pack('<H', len(strip)))
            for idx in strip:
                f.write(struct.pack('<H', idx))

    return len(polygons)
