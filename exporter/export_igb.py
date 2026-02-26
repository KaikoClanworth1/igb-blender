"""Standalone IGB file export for Blender.

Exports all mesh objects in the scene as an Alchemy Engine IGB file.
No template file needed — builds the IGB from scratch using IGBBuilder.

Pipeline:
    1. Collect all mesh objects in the scene (excluding "Colliders" collection)
    2. For each object, extract per-material submeshes (mesh_extractor)
    3. For each submesh:
       - Get Blender material properties
       - Extract texture image → RGBA → DXT5 compress with mipmaps
    4. Based on collision_source setting:
       - "Colliders Collection": build collision from 'Colliders' collection objects
       - "Visual Mesh": build collision from the same visible mesh objects
       - "None": skip collision entirely
    5. IGBBuilder.build(all_submesh_data, collision_data) → IGBWriter
    6. Write to disk

Supports multi-object scenes and multi-material meshes: every material
slot on every object gets its own igAttrSet branch in the scene graph
with independent geometry, material, and texture.

Collision: Objects in a "Colliders" collection are always excluded from
visual geometry. Collision hull source is controlled by the collision_source
setting (Colliders Collection, Visual Mesh, or None).
"""

import bpy
import os
import time


def export_igb(context, filepath, operator=None):
    """Export all scene meshes as a standalone IGB file.

    Args:
        context: Blender context
        filepath: output .igb file path
        operator: the export operator (for options and error reporting)

    Returns:
        {'FINISHED'} or {'CANCELLED'}
    """
    t_start = time.time()

    # Read collision options from operator
    collision_source = 'COLLIDERS'  # default for backward compat
    surface_type = 0
    swap_rb = False
    texture_mode = 'dxt5'  # 'dxt5' or 'clut'
    if operator is not None:
        if hasattr(operator, 'collision_source'):
            collision_source = operator.collision_source
        elif hasattr(operator, 'export_collision'):
            # Legacy backward compat: old BoolProperty -> new enum
            collision_source = 'COLLIDERS' if operator.export_collision else 'NONE'
        if hasattr(operator, 'surface_type'):
            surface_type = operator.surface_type
        # Texture format: new texture_format property or legacy game_preset
        if hasattr(operator, 'texture_format'):
            fmt = operator.texture_format
            if fmt == 'clut':
                texture_mode = 'clut'
                swap_rb = False
            elif fmt == 'dxt5_mua':
                texture_mode = 'dxt5'
                swap_rb = True
            else:  # dxt5_xml2
                texture_mode = 'dxt5'
                swap_rb = False
        elif hasattr(operator, 'game_preset'):
            swap_rb = 'mua' in operator.game_preset

    # Build set of objects to EXCLUDE from visual export:
    # 1. "Colliders" collection (collision-only geometry)
    # 2. Any collection prefixed with "[MapMaker]" (editor-only objects)
    excluded_objects = set()

    colliders_coll = bpy.data.collections.get("Colliders")
    if colliders_coll is not None:
        for obj in colliders_coll.objects:
            if obj.type == 'MESH':
                excluded_objects.add(obj)
        collider_count = sum(1 for o in colliders_coll.objects if o.type == 'MESH')
        if collider_count:
            _report(operator, 'INFO',
                    f"Found {collider_count} collider object(s) "
                    f"in 'Colliders' collection")

    # Exclude all [MapMaker] collections (entities, automap, etc.)
    mapmaker_excluded = 0
    for coll in bpy.data.collections:
        if coll.name.startswith("[MapMaker]"):
            for obj in coll.objects:
                excluded_objects.add(obj)
                mapmaker_excluded += 1
    if mapmaker_excluded:
        _report(operator, 'INFO',
                f"Excluding {mapmaker_excluded} [MapMaker] object(s) from export")

    # Separate collider objects for collision hull building
    collider_objects = set()
    if colliders_coll is not None:
        for obj in colliders_coll.objects:
            if obj.type == 'MESH':
                collider_objects.add(obj)

    # Collect all VISIBLE mesh objects EXCLUDING colliders, [MapMaker], and hidden objects
    mesh_objects = [
        obj for obj in context.scene.objects
        if obj.type == 'MESH'
        and obj not in excluded_objects
        and not obj.hide_viewport
    ]

    if not mesh_objects:
        _report(operator, 'ERROR',
                "No visible mesh objects in the scene. Add a mesh to export.")
        return {'CANCELLED'}

    # Log which objects will be exported so the user can verify
    obj_names = [obj.name for obj in mesh_objects]
    _report(operator, 'INFO',
            f"Exporting {len(mesh_objects)} visual mesh object(s) to IGB: {obj_names}")

    # Step 1: Extract per-material submeshes from ALL objects
    from .mesh_extractor import extract_mesh_per_material
    from ..utils.dxt_compress import compress_with_mipmaps

    # Cache compressed textures by Blender image name to avoid
    # re-compressing the same texture for multiple objects/materials
    texture_cache = {}  # image_name -> (texture_levels, texture_name)

    builder_submeshes = []
    total_objects = 0
    total_submeshes = 0

    for obj in mesh_objects:
        _report(operator, 'INFO', f"  Object '{obj.name}':")

        try:
            submeshes = extract_mesh_per_material(obj, uv_v_flip=True)
        except ValueError as e:
            _report(operator, 'WARNING',
                    f"    Skipping '{obj.name}': {e}")
            continue
        except Exception as e:
            _report(operator, 'WARNING',
                    f"    Skipping '{obj.name}': unexpected error: {e}")
            continue

        total_objects += 1

        for sub_mesh in submeshes:
            total_tris = len(sub_mesh.indices) // 3
            _report(operator, 'INFO',
                    f"    Submesh '{sub_mesh.name}': {len(sub_mesh.positions)} verts, "
                    f"{total_tris} tris, mat_idx={sub_mesh.material_index}")

            # Get Blender material for this submesh
            bl_mat = None
            if (sub_mesh.material_index >= 0 and
                    sub_mesh.material_index < len(obj.material_slots)):
                bl_mat = obj.material_slots[sub_mesh.material_index].material

            # Extract material properties
            if bl_mat is not None:
                material_props = _extract_material_props(bl_mat)
            else:
                material_props = _default_material()

            # Extract and compress texture (with caching)
            if texture_mode == 'clut':
                clut_data, texture_name = _get_texture_clut_for_material(
                    bl_mat, texture_cache, operator
                )
                builder_submeshes.append({
                    'mesh': sub_mesh,
                    'material': material_props,
                    'material_state': material_props.get('material_state', {}),
                    'clut_data': clut_data,
                    'texture_levels': None,
                    'texture_name': texture_name,
                })
            else:
                texture_levels, texture_name = _get_texture_for_material(
                    bl_mat, texture_cache, operator, swap_rb=swap_rb
                )
                builder_submeshes.append({
                    'mesh': sub_mesh,
                    'material': material_props,
                    'material_state': material_props.get('material_state', {}),
                    'texture_levels': texture_levels,
                    'texture_name': texture_name,
                })
            total_submeshes += 1

    if not builder_submeshes:
        _report(operator, 'ERROR',
                "No valid mesh data found in scene objects.")
        return {'CANCELLED'}

    _report(operator, 'INFO',
            f"  Total: {total_objects} objects, {total_submeshes} submeshes")

    # Step 2: Build collision data based on collision_source setting
    collision_data = None
    collision_objects = None

    if collision_source == 'COLLIDERS' and collider_objects:
        collision_objects = list(collider_objects)
        _report(operator, 'INFO',
                f"Building collision hull from Colliders collection "
                f"({len(collision_objects)} objects, surface_type={surface_type})...")
    elif collision_source == 'VISUAL':
        collision_objects = list(mesh_objects)
        _report(operator, 'INFO',
                f"Building collision hull from visual mesh "
                f"({len(collision_objects)} objects, surface_type={surface_type})...")

    if collision_objects:
        from .collide_hull import build_collision_data

        try:
            collision_data = build_collision_data(
                collision_objects, surface_type=surface_type)
            if collision_data is not None:
                num_tris = collision_data['num_triangles']
                num_nodes = collision_data['num_tree_nodes_minus_1'] + 1
                _report(operator, 'INFO',
                        f"  Collision: {num_tris} triangles, {num_nodes} BVH nodes")
                if num_tris > 17000:
                    _report(operator, 'WARNING',
                            f"  Collision has {num_tris} tris — game files max ~17000! "
                            f"Consider using Generate Box/Hull Colliders in the IGB panel")
            else:
                _report(operator, 'WARNING',
                        "  No collision triangles extracted")
        except Exception as e:
            import traceback
            traceback.print_exc()
            _report(operator, 'WARNING',
                    f"  Collision hull build failed: {e}")
            collision_data = None

    # Step 2.5: Collect scene lights
    export_lights = True
    if operator is not None and hasattr(operator, 'export_lights'):
        export_lights = operator.export_lights

    light_data_list = []
    if export_lights:
        light_data_list = _collect_scene_lights(context)
        if light_data_list:
            _report(operator, 'INFO',
                    f"Exporting {len(light_data_list)} light(s)")

    # Step 3: Build IGB via IGBBuilder
    from .igb_builder import IGBBuilder

    try:
        builder = IGBBuilder()
        writer = builder.build(
            builder_submeshes,
            collision_data=collision_data,
            lights=light_data_list if light_data_list else None,
        )
    except Exception as e:
        import traceback
        traceback.print_exc()
        _report(operator, 'ERROR', f"IGB build failed: {e}")
        return {'CANCELLED'}

    # Step 4: Write to disk
    try:
        writer.write(filepath)
    except Exception as e:
        import traceback
        traceback.print_exc()
        _report(operator, 'ERROR', f"IGB write failed: {e}")
        return {'CANCELLED'}

    t_elapsed = time.time() - t_start
    file_size = os.path.getsize(filepath)
    collision_msg = ""
    if collision_data is not None:
        collision_msg = f", collision={collision_data['num_triangles']} tris"
    light_msg = ""
    if light_data_list:
        light_msg = f", {len(light_data_list)} lights"
    _report(operator, 'INFO',
            f"Exported {total_objects} object(s) ({total_submeshes} submeshes"
            f"{collision_msg}{light_msg}) "
            f"to {os.path.basename(filepath)} "
            f"({file_size:,} bytes, {t_elapsed:.2f}s)")

    return {'FINISHED'}


# ===========================================================================
# Light collection
# ===========================================================================

def _collect_scene_lights(context):
    """Collect all visible light objects from the scene.

    Converts Blender light properties to Alchemy igLightAttr format:
        SUN  -> DIRECTIONAL (type 0)
        POINT -> POINT (type 1)
        SPOT -> SPOT (type 2)

    Energy is converted back to attenuation coefficients using the
    inverse of the IGBConverter import formulas.

    Returns:
        list of dicts suitable for IGBBuilder.build(lights=...)
    """
    import math

    lights = []
    for obj in context.scene.objects:
        if obj.type != 'LIGHT' or obj.hide_viewport:
            continue

        light = obj.data

        # Map Blender type -> Alchemy type
        type_map = {'SUN': 0, 'POINT': 1, 'SPOT': 2, 'AREA': 1}
        light_type = type_map.get(light.type, 1)

        # Position from object location
        pos = obj.location
        position = (pos.x, pos.y, pos.z)

        # Direction from object's -Z axis (Blender lights emit along local -Z)
        from mathutils import Vector
        direction = (obj.matrix_world.to_3x3() @ Vector((0, 0, -1))).normalized()
        direction = (direction.x, direction.y, direction.z)

        # In Alchemy, brightness is baked into the diffuse color RGB.
        # Blender separates color (0-1 normalized) from energy (watts).
        # We recombine them: diffuse = color * (energy / 10.0)
        # The /10 matches the *10 multiplier on import.
        c = light.color
        energy = max(light.energy, 0.001)
        brightness = energy / 10.0
        # Clamp to 0-1 range (Alchemy uses 0-1 color values)
        diffuse = (
            min(1.0, c[0] * brightness),
            min(1.0, c[1] * brightness),
            min(1.0, c[2] * brightness),
            1.0,
        )
        specular = diffuse
        ambient = (0.0, 0.0, 0.0, 1.0)

        # Attenuation — use the same values as the original game files
        if light_type == 0:
            # Directional: constant attenuation, no distance falloff
            attenuation = (1.0, 0.0, 0.0)
            cutoff = 180.0
        elif light_type == 1:
            # Point: quadratic attenuation (matches game files)
            attenuation = (0.0, 0.0, 0.0001)
            cutoff = -0.5
        else:
            # Spot: quadratic attenuation
            attenuation = (0.0, 0.0, 0.0001)
            cutoff = math.degrees(light.spot_size) / 2.0

        # Spot falloff
        falloff = 0.0
        if light_type == 2:
            # spot_blend: 0 = sharp edge, 1 = fully soft
            # inner_ratio = 1.0 - spot_blend
            # From import: inner_ratio = max(0, 1 - falloff*30)
            # So: falloff = (1 - inner_ratio) / 30 = spot_blend / 30
            falloff = light.spot_blend / 30.0

        light_name = obj.name
        lights.append({
            'name': light_name,
            'type': light_type,
            'position': position,
            'direction': direction,
            'diffuse': diffuse,
            'ambient': ambient,
            'specular': specular,
            'attenuation': attenuation,
            'falloff': falloff,
            'cutoff': cutoff,
        })

    return lights


# ===========================================================================
# Texture extraction with caching
# ===========================================================================

def _get_texture_for_material(bl_mat, texture_cache, operator, swap_rb=False):
    """Get compressed texture levels for a material, using cache.

    Returns:
        (texture_levels, texture_name)
    """
    from ..utils.dxt_compress import compress_with_mipmaps

    texture_levels = None
    texture_name = ''

    if bl_mat is not None:
        bl_image = _find_texture_image(bl_mat)
        if bl_image is not None:
            texture_name = bl_image.name

            # Check cache first
            if texture_name in texture_cache:
                cached_levels, cached_name = texture_cache[texture_name]
                _report(operator, 'INFO',
                        f"      Texture: {texture_name} (cached)")
                return cached_levels, cached_name

            _report(operator, 'INFO',
                    f"      Texture: {bl_image.name} "
                    f"({bl_image.size[0]}x{bl_image.size[1]})")

            # Extract RGBA pixels
            rgba_data, img_w, img_h = _extract_image_pixels(bl_image)
            if rgba_data is not None:
                # Ensure power-of-2 dimensions
                img_w, img_h, rgba_data = _ensure_power_of_2(
                    img_w, img_h, rgba_data)

                # DXT5 compress with mipmaps
                try:
                    texture_levels = compress_with_mipmaps(
                        rgba_data, img_w, img_h, swap_rb=swap_rb)
                    _report(operator, 'INFO',
                            f"      Compressed: {img_w}x{img_h}, "
                            f"{len(texture_levels)} mip levels")

                    # Cache for reuse
                    texture_cache[texture_name] = (texture_levels, texture_name)
                except Exception as e:
                    _report(operator, 'WARNING',
                            f"      Texture compression failed: {e}")

    # If no texture found, create a 4x4 white placeholder
    if texture_levels is None:
        texture_levels = _create_placeholder_texture(swap_rb=swap_rb)
        texture_name = texture_name or 'placeholder'
        _report(operator, 'INFO', "      Using 4x4 white placeholder texture")

    return texture_levels, texture_name


def _get_texture_clut_for_material(bl_mat, texture_cache, operator):
    """Get CLUT-quantized texture data for a material, using cache.

    Returns:
        (clut_data, texture_name) where clut_data is
        (palette_data, index_data, width, height) or None
    """
    from ..utils.clut_compress import quantize_rgba_to_clut

    clut_data = None
    texture_name = ''

    if bl_mat is not None:
        bl_image = _find_texture_image(bl_mat)
        if bl_image is not None:
            texture_name = bl_image.name

            # Check cache first
            cache_key = texture_name + '_clut'
            if cache_key in texture_cache:
                cached_data, cached_name = texture_cache[cache_key]
                _report(operator, 'INFO',
                        f"      Texture: {texture_name} (cached CLUT)")
                return cached_data, cached_name

            _report(operator, 'INFO',
                    f"      Texture: {bl_image.name} "
                    f"({bl_image.size[0]}x{bl_image.size[1]}) → CLUT")

            rgba_data, img_w, img_h = _extract_image_pixels(bl_image)
            if rgba_data is not None:
                img_w, img_h, rgba_data = _ensure_power_of_2(
                    img_w, img_h, rgba_data)
                try:
                    palette_data, index_data = quantize_rgba_to_clut(
                        rgba_data, img_w, img_h)
                    clut_data = (palette_data, index_data, img_w, img_h)
                    _report(operator, 'INFO',
                            f"      Quantized: {img_w}x{img_h}, 256 colors")
                    texture_cache[cache_key] = (clut_data, texture_name)
                except Exception as e:
                    _report(operator, 'WARNING',
                            f"      CLUT quantization failed: {e}")

    # If no texture found, create a 4x4 white placeholder CLUT
    if clut_data is None:
        palette_data = bytearray(1024)
        # First entry: white
        palette_data[0] = 255
        palette_data[1] = 255
        palette_data[2] = 255
        palette_data[3] = 255
        index_data = bytes(16)  # 4x4 pixels, all index 0
        clut_data = (bytes(palette_data), index_data, 4, 4)
        texture_name = texture_name or 'placeholder'
        _report(operator, 'INFO', "      Using 4x4 white placeholder CLUT")

    return clut_data, texture_name


# ===========================================================================
# Material/Texture extraction helpers
# ===========================================================================

def _default_material():
    """Return default material properties.

    Ambient uses 0.588 gray to match the standard value found in game files.
    In OpenGL fixed-function: final = ambient_light * ambient_mat + diffuse_light * diffuse_mat * NdotL.
    Full-white ambient (1.0) would make surfaces bright from all angles regardless of lighting.
    """
    return {
        'diffuse': (1.0, 1.0, 1.0, 1.0),
        'ambient': (0.588, 0.588, 0.588, 1.0),
        'specular': (0.0, 0.0, 0.0, 0.0),
        'emission': (0.0, 0.0, 0.0, 0.0),
        'shininess': 0.0,
        'material_state': {},
    }


def _extract_material_props(bl_mat):
    """Extract material properties from a Blender material.

    First checks for igb_* custom properties (set during import for
    round-trip fidelity). Falls back to reading Principled BSDF inputs.

    Mapping (fallback):
        Base Color → diffuse
        Roughness → shininess (inverted: shininess = (1-roughness) * 128)
        Specular → specular
        Emission Color → emission
    """
    props = _default_material()

    # --- Try igb_* custom properties first (import round-trip) ---
    igb_diffuse = bl_mat.get("igb_diffuse")
    if igb_diffuse is not None:
        props['diffuse'] = tuple(igb_diffuse)
        props['ambient'] = tuple(bl_mat.get("igb_ambient", (0.588, 0.588, 0.588, 1.0)))
        props['specular'] = tuple(bl_mat.get("igb_specular", (0.0, 0.0, 0.0, 0.0)))
        props['emission'] = tuple(bl_mat.get("igb_emission", (0.0, 0.0, 0.0, 0.0)))
        props['shininess'] = bl_mat.get("igb_shininess", 0.0)
    elif bl_mat.use_nodes:
        # --- Fallback: read from Principled BSDF ---
        for node in bl_mat.node_tree.nodes:
            if node.type == 'BSDF_PRINCIPLED':
                base_color = node.inputs.get('Base Color')
                if base_color is not None:
                    c = base_color.default_value
                    props['diffuse'] = (c[0], c[1], c[2], c[3])

                roughness = node.inputs.get('Roughness')
                if roughness is not None:
                    r = roughness.default_value
                    props['shininess'] = (1.0 - r) * 128.0

                spec_input = (node.inputs.get('Specular IOR Level') or
                              node.inputs.get('Specular'))
                if spec_input is not None:
                    s = spec_input.default_value
                    if isinstance(s, float):
                        props['specular'] = (s, s, s, 1.0)

                emission = node.inputs.get('Emission Color')
                if emission is not None:
                    e = emission.default_value
                    props['emission'] = (e[0], e[1], e[2], 1.0)

                break

        # Ambient: use game-standard 0.588 gray, NOT the diffuse color.
        # In fixed-function: final = ambient_light * ambient_mat + diffuse_light * diffuse_mat * NdotL.
        # Full-white ambient makes everything fully lit from all angles.
        props['ambient'] = (0.588, 0.588, 0.588, 1.0)
    else:
        c = bl_mat.diffuse_color
        props['diffuse'] = (c[0], c[1], c[2], c[3])

    # --- Extract IGB material state custom properties ---
    props['material_state'] = _extract_material_state(bl_mat)

    return props


def _extract_material_state(bl_mat):
    """Extract IGB material state custom properties from a Blender material.

    Reads back the igb_* custom properties that were set during import,
    preserving them for round-trip export fidelity. If custom properties
    aren't present, infers blend state from Blender material settings.

    Returns:
        dict with material state keys (only present keys are included)
    """
    state = {}

    # Blend state — only include if enabled (game files omit for opaque)
    blend_enabled = bl_mat.get("igb_blend_enabled")
    if blend_enabled is not None:
        state['blend_enabled'] = bool(blend_enabled)
    else:
        # Infer from Blender blend mode
        is_blended = False
        if hasattr(bl_mat, 'surface_render_method'):
            is_blended = (bl_mat.surface_render_method == 'BLENDED')
        elif hasattr(bl_mat, 'blend_method'):
            is_blended = (bl_mat.blend_method != 'OPAQUE')
        if is_blended:
            state['blend_enabled'] = True

    # Blend function — only include when blend is actually enabled
    if state.get('blend_enabled'):
        if bl_mat.get("igb_blend_src") is not None:
            state['blend_src'] = bl_mat.get("igb_blend_src", 4)
            state['blend_dst'] = bl_mat.get("igb_blend_dst", 5)
            state['blend_eq'] = bl_mat.get("igb_blend_eq", 0)
            state['blend_constant'] = bl_mat.get("igb_blend_constant", 0)
            state['blend_stage'] = bl_mat.get("igb_blend_stage", 0)
            state['blend_a'] = bl_mat.get("igb_blend_a", 0)
            state['blend_b'] = bl_mat.get("igb_blend_b", 0)
            state['blend_c'] = bl_mat.get("igb_blend_c", 0)
            state['blend_d'] = bl_mat.get("igb_blend_d", 0)
        else:
            # Blending inferred but no custom props — standard alpha blend
            state['blend_src'] = 4   # SRC_ALPHA
            state['blend_dst'] = 5   # ONE_MINUS_SRC_ALPHA

    # Alpha test state — only include if enabled (game files omit for non-cutout)
    alpha_enabled = bl_mat.get("igb_alpha_test_enabled")
    if alpha_enabled is not None and bool(alpha_enabled):
        state['alpha_test_enabled'] = True

    # Alpha function — only include when alpha test is enabled
    if state.get('alpha_test_enabled'):
        if bl_mat.get("igb_alpha_func") is not None:
            state['alpha_func'] = bl_mat.get("igb_alpha_func", 6)
            state['alpha_ref'] = bl_mat.get("igb_alpha_ref", 0.5)
        else:
            state['alpha_func'] = 6    # GEQUAL (default)
            state['alpha_ref'] = 0.5

    # Color attr
    if bl_mat.get("igb_color_r") is not None:
        state['color_r'] = bl_mat.get("igb_color_r", 1.0)
        state['color_g'] = bl_mat.get("igb_color_g", 1.0)
        state['color_b'] = bl_mat.get("igb_color_b", 1.0)
        state['color_a'] = bl_mat.get("igb_color_a", 1.0)

    # Lighting state
    lighting = bl_mat.get("igb_lighting_enabled")
    if lighting is not None:
        state['lighting_enabled'] = bool(lighting)

    # Texture matrix state
    tex_matrix = bl_mat.get("igb_tex_matrix_enabled")
    if tex_matrix is not None:
        state['tex_matrix_enabled'] = bool(tex_matrix)
        state['tex_matrix_unit_id'] = bl_mat.get("igb_tex_matrix_unit_id", 0)

    # Backface culling
    cull_enabled = bl_mat.get("igb_cull_face_enabled")
    if cull_enabled is not None:
        state['cull_face_enabled'] = bool(cull_enabled)
        state['cull_face_mode'] = bl_mat.get("igb_cull_face_mode", 0)
    elif bl_mat.use_backface_culling:
        # Infer from Blender's backface culling setting.
        # Game files: enable=1, mode=0 is the standard for culled geometry.
        state['cull_face_enabled'] = True
        state['cull_face_mode'] = 0

    return state


def _find_texture_image(bl_mat):
    """Find the first Image Texture connected to a Principled BSDF.

    First checks for a texture directly linked to the Base Color input.
    Falls back to any Image Texture node in the material.
    """
    if not bl_mat.use_nodes:
        return None

    # Check Principled BSDF Base Color input
    for node in bl_mat.node_tree.nodes:
        if node.type == 'BSDF_PRINCIPLED':
            base_color = node.inputs.get('Base Color')
            if base_color is not None and base_color.is_linked:
                for link in base_color.links:
                    from_node = link.from_node
                    if from_node.type == 'TEX_IMAGE' and from_node.image is not None:
                        return from_node.image
            break

    # Fallback: any Image Texture node
    for node in bl_mat.node_tree.nodes:
        if node.type == 'TEX_IMAGE' and node.image is not None:
            return node.image

    return None


def _extract_image_pixels(bl_image):
    """Extract RGBA pixel data from a Blender image.

    Blender stores pixels bottom-to-top (OpenGL convention).
    IGB/DXT expects top-to-bottom, so we Y-flip here.

    Returns:
        (rgba_bytes, width, height) or (None, 0, 0) on failure
    """
    width = bl_image.size[0]
    height = bl_image.size[1]

    if width == 0 or height == 0:
        return None, 0, 0

    pixels = list(bl_image.pixels)
    num_pixels = width * height

    rgba = bytearray(num_pixels * 4)
    for i in range(num_pixels):
        src_y = i // width
        src_x = i % width
        # Y-flip: Blender bottom-up → DXT top-down
        dst_y = height - 1 - src_y
        dst_idx = (dst_y * width + src_x) * 4
        src_idx = i * 4

        rgba[dst_idx] = _float_to_byte(pixels[src_idx])
        rgba[dst_idx + 1] = _float_to_byte(pixels[src_idx + 1])
        rgba[dst_idx + 2] = _float_to_byte(pixels[src_idx + 2])
        rgba[dst_idx + 3] = _float_to_byte(pixels[src_idx + 3])

    return bytes(rgba), width, height


def _ensure_power_of_2(width, height, rgba_data):
    """Ensure texture dimensions are powers of 2.

    DXT compression requires POT dimensions. If the image is not POT,
    it is scaled up to the next power of 2 using nearest-neighbor sampling.

    Returns:
        (new_width, new_height, new_rgba_data)
    """
    new_w = _next_power_of_2(width)
    new_h = _next_power_of_2(height)

    if new_w == width and new_h == height:
        return width, height, rgba_data

    new_data = bytearray(new_w * new_h * 4)
    for y in range(new_h):
        src_y = min(y * height // new_h, height - 1)
        for x in range(new_w):
            src_x = min(x * width // new_w, width - 1)
            src_off = (src_y * width + src_x) * 4
            dst_off = (y * new_w + x) * 4
            new_data[dst_off:dst_off + 4] = rgba_data[src_off:src_off + 4]

    return new_w, new_h, bytes(new_data)


def _create_placeholder_texture(swap_rb=False):
    """Create a minimal 4x4 white DXT5 texture with no mipmaps.

    Used when a material has no texture image. Returns a list with
    one (compressed_data, width, height) tuple.
    """
    from ..utils.dxt_compress import compress_rgba_to_dxt5

    # 4x4 white RGBA (R/B swap doesn't matter for all-white)
    white_rgba = bytes([255, 255, 255, 255] * 16)
    compressed = compress_rgba_to_dxt5(white_rgba, 4, 4, swap_rb=swap_rb)
    return [(compressed, 4, 4)]


# ===========================================================================
# Utility helpers
# ===========================================================================

def _next_power_of_2(n):
    """Return the next power of 2 >= n."""
    if n <= 0:
        return 1
    p = 1
    while p < n:
        p *= 2
    return p


def _float_to_byte(f):
    """Convert 0.0-1.0 float to 0-255 byte, clamped."""
    return max(0, min(255, int(f * 255.0 + 0.5)))


def _report(operator, level, message):
    """Report a message through the operator or print to console."""
    if operator is not None and hasattr(operator, 'report'):
        operator.report({level}, message)
    else:
        print(f"[{level}] {message}")
