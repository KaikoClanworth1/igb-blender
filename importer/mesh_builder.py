"""Build Blender meshes from parsed IGB geometry data.

Converts ParsedGeometry objects into Blender mesh objects with:
- Positions, normals, UVs, vertex colors
- Triangle strip to triangle list conversion
- Degenerate triangle removal (before mesh creation)
- Proper coordinate system handling (Z-up for both Alchemy XML2 and Blender)

Following IGBConverter approach: strip->triangulate->remove degenerates->build mesh.
"""

import bpy
from mathutils import Matrix


def build_mesh(geom, name="IGB_Mesh", transform=None, options=None, profile=None):
    """Create a Blender mesh object from parsed geometry.

    Args:
        geom: ParsedGeometry instance with vertex/index data
        name: name for the Blender mesh and object
        transform: optional 4x4 transform matrix (tuple of 16 floats, row-major)
        options: import options dict (import_normals, import_uvs, import_vertex_colors)
        profile: GameProfile instance (optional, controls UV flip etc.)

    Returns:
        bpy.types.Object: the created Blender object, or None on failure
    """
    if options is None:
        options = {
            'import_normals': True,
            'import_uvs': True,
            'import_vertex_colors': True,
        }

    if geom.num_verts == 0:
        return None

    # Convert triangle strips to triangle list
    tri_indices = geom.triangulate()
    if not tri_indices:
        return None

    num_tris = len(tri_indices) // 3
    if num_tris == 0:
        return None

    # Remove degenerate triangles BEFORE building mesh
    # (Following IGBConverter: removeDegenerate() after unStripGeometry())
    clean_indices = []
    for t in range(num_tris):
        i0 = tri_indices[t * 3]
        i1 = tri_indices[t * 3 + 1]
        i2 = tri_indices[t * 3 + 2]
        # Skip degenerate triangles (two or more identical indices)
        if i0 == i1 or i1 == i2 or i0 == i2:
            continue
        # Skip out-of-range indices
        if i0 >= geom.num_verts or i1 >= geom.num_verts or i2 >= geom.num_verts:
            continue
        # Skip zero-area triangles (all three vertices at same position)
        if geom.positions[i0] == geom.positions[i1] == geom.positions[i2]:
            continue
        clean_indices.extend([i0, i1, i2])

    num_tris = len(clean_indices) // 3
    if num_tris == 0:
        return None

    # Also remove duplicate faces (same 3 vertex indices in any order)
    seen_faces = set()
    final_indices = []
    for t in range(num_tris):
        i0 = clean_indices[t * 3]
        i1 = clean_indices[t * 3 + 1]
        i2 = clean_indices[t * 3 + 2]
        face_key = tuple(sorted((i0, i1, i2)))
        if face_key in seen_faces:
            continue
        seen_faces.add(face_key)
        final_indices.extend([i0, i1, i2])

    num_tris = len(final_indices) // 3
    if num_tris == 0:
        return None

    # Create mesh data
    mesh = bpy.data.meshes.new(name)

    # Set vertices
    vertices = geom.positions
    mesh.vertices.add(len(vertices))
    mesh.vertices.foreach_set("co", [c for v in vertices for c in v])

    # Set faces (triangles) using clean indices
    num_loops = num_tris * 3
    mesh.loops.add(num_loops)
    mesh.loops.foreach_set("vertex_index", final_indices)

    mesh.polygons.add(num_tris)
    loop_starts = [i * 3 for i in range(num_tris)]
    loop_totals = [3] * num_tris
    mesh.polygons.foreach_set("loop_start", loop_starts)
    mesh.polygons.foreach_set("loop_total", loop_totals)

    # Update mesh geometry first
    mesh.update()

    # Set UVs BEFORE validate - uses the clean final_indices
    if options.get('import_uvs', True) and geom.has_uvs:
        uv_v_flip = True  # default: DirectX convention
        if profile is not None:
            uv_v_flip = profile.texture.uv_v_flip
        _set_uv_layer(mesh, geom.uvs, final_indices, "UVMap", uv_v_flip=uv_v_flip)

    # Set vertex colors BEFORE validate
    if options.get('import_vertex_colors', True) and geom.has_colors:
        _set_vertex_colors(mesh, geom.colors, final_indices, "Color")

    # Validate mesh (should be minimal changes since we pre-cleaned)
    mesh.validate(clean_customdata=False)
    mesh.update()

    # Set normals AFTER validate, using the actual loop count from the mesh
    if options.get('import_normals', True) and geom.has_normals:
        _set_custom_normals(mesh, geom.normals, final_indices)

    # Create object
    obj = bpy.data.objects.new(name, mesh)

    # Apply transform
    if transform is not None:
        mat = _tuple_to_matrix(transform)
        obj.matrix_world = mat

    return obj


def _set_custom_normals(mesh, normals, tri_indices):
    """Set custom split normals on the mesh.

    Uses the actual mesh loop count to avoid mismatch errors.
    If validate() removed any faces, we rebuild the normals list
    from the mesh's actual loops.

    Args:
        mesh: Blender mesh data
        normals: list of (nx, ny, nz) per vertex
        tri_indices: flat list of triangle vertex indices (pre-validate)
    """
    actual_loop_count = len(mesh.loops)

    if actual_loop_count == len(tri_indices):
        # No faces were removed by validate - use the original indices
        loop_normals = []
        for idx in tri_indices:
            if idx < len(normals):
                loop_normals.append(normals[idx])
            else:
                loop_normals.append((0.0, 0.0, 1.0))
    else:
        # validate() changed the loop count - read actual vertex indices from mesh
        loop_normals = []
        for loop in mesh.loops:
            idx = loop.vertex_index
            if idx < len(normals):
                loop_normals.append(normals[idx])
            else:
                loop_normals.append((0.0, 0.0, 1.0))

    if len(loop_normals) != actual_loop_count:
        return  # Safety - should not happen

    mesh.normals_split_custom_set(loop_normals)


def _set_uv_layer(mesh, uvs, tri_indices, layer_name="UVMap", uv_v_flip=True):
    """Set UV coordinates on the mesh.

    Args:
        mesh: Blender mesh data
        uvs: list of (u, v) per vertex
        tri_indices: flat list of triangle vertex indices
        layer_name: name for the UV layer
        uv_v_flip: if True, apply v = 1.0 - v (DirectX -> OpenGL convention)
    """
    uv_layer = mesh.uv_layers.new(name=layer_name)
    uv_data = uv_layer.data

    # Use the smaller of tri_indices length and actual loop count
    count = min(len(tri_indices), len(uv_data))
    for i in range(count):
        idx = tri_indices[i]
        if idx < len(uvs):
            u, v = uvs[idx]
            if uv_v_flip:
                uv_data[i].uv = (u, 1.0 - v)
            else:
                uv_data[i].uv = (u, v)
        else:
            uv_data[i].uv = (0.0, 0.0)


def _set_vertex_colors(mesh, colors, tri_indices, layer_name="Color"):
    """Set vertex colors on the mesh.

    Args:
        mesh: Blender mesh data
        colors: list of (r, g, b, a) per vertex, 0.0-1.0
        tri_indices: flat list of triangle vertex indices
        layer_name: name for the color attribute
    """
    color_attr = mesh.color_attributes.new(
        name=layer_name,
        type='FLOAT_COLOR',
        domain='CORNER'
    )

    count = min(len(tri_indices), len(color_attr.data))
    for i in range(count):
        idx = tri_indices[i]
        if idx < len(colors):
            color_attr.data[i].color = colors[idx]
        else:
            color_attr.data[i].color = (1.0, 1.0, 1.0, 1.0)


def _tuple_to_matrix(t):
    """Convert an Alchemy 4x4 matrix to a Blender Matrix.

    Alchemy stores matrices with translation in the LAST ROW (row 3):
        [Xx  Xy  Xz  0 ]
        [Yx  Yy  Yz  0 ]
        [Zx  Zy  Zz  0 ]
        [Tx  Ty  Tz  1 ]

    Blender expects translation in the LAST COLUMN (column 3):
        [Xx  Yx  Zx  Tx]
        [Xy  Yy  Zy  Ty]
        [Xz  Yz  Zz  Tz]
        [0   0   0   1 ]

    So we need to transpose the matrix.
    """
    return Matrix((
        (t[0], t[4], t[8],  t[12]),
        (t[1], t[5], t[9],  t[13]),
        (t[2], t[6], t[10], t[14]),
        (t[3], t[7], t[11], t[15]),
    ))
