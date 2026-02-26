"""Build Blender materials from parsed IGB material and texture data.

Creates Principled BSDF shader nodes with:
- Diffuse/specular/emission colors from igMaterialAttr
- DXT-compressed textures from igImage -> Image Texture nodes
- Alpha transparency handling
- Texture coordinate mapping
"""

import logging
import os

import bpy

from ..scene_graph.sg_materials import (
    ParsedMaterial, ParsedTexture, ParsedImage,
    WRAP_REPEAT, WRAP_CLAMP,
)
from ..utils.image_convert import convert_image_to_rgba


# Debug logger — activate with IGB_DEBUG_BLEND=1 environment variable
_blend_debug = os.environ.get('IGB_DEBUG_BLEND', '') == '1'
_log = logging.getLogger("igb_blend")


# Cache: (image object index) -> Blender image name
# Prevents creating duplicate Blender images for the same IGB image
_image_cache = {}

# Cache: (material_idx, texture_idx) -> Blender material
# Prevents creating duplicate materials for reused material+texture combos
_material_cache = {}


def clear_caches():
    """Clear the image and material caches. Call at start of each import."""
    global _image_cache, _material_cache
    _image_cache = {}
    _material_cache = {}


def _obj_cache_key(source_obj):
    """Get a hashable cache key from an IGB or IGZ source object."""
    if source_obj is None:
        return -1
    # IGB objects have .index, IGZ objects have .global_offset
    idx = getattr(source_obj, 'index', None)
    if idx is not None:
        return idx
    return getattr(source_obj, 'global_offset', -1)


def build_material(parsed_material, parsed_texture=None,
                   extra_state=None, name="IGB_Material", profile=None):
    """Create a Blender material from parsed IGB data.

    Args:
        parsed_material: ParsedMaterial from sg_materials
        parsed_texture: ParsedTexture from sg_materials (optional)
        extra_state: dict of additional material state (blend, alpha, color, etc.)
        name: base name for the material
        profile: GameProfile instance (used for profile-aware blend heuristics)

    Returns:
        bpy.types.Material or None
    """
    if parsed_material is None:
        return None
    if extra_state is None:
        extra_state = {}

    # Check cache - include extra state object indices for uniqueness
    # IGB objects have .index, IGZ objects have .global_offset
    mat_idx = _obj_cache_key(parsed_material.source_obj)
    tex_idx = -1
    if parsed_texture is not None and parsed_texture.source_obj is not None:
        tex_idx = _obj_cache_key(parsed_texture.source_obj)

    # Build a hashable extra key from the extra_state contents
    extra_key_parts = []
    for key in sorted(extra_state.keys()):
        val = extra_state[key]
        if isinstance(val, dict):
            extra_key_parts.append((key, tuple(sorted(val.items()))))
        elif isinstance(val, (list, tuple)):
            extra_key_parts.append((key, tuple(val)))
        else:
            extra_key_parts.append((key, val))
    extra_key = tuple(extra_key_parts) if extra_key_parts else ()

    cache_key = (mat_idx, tex_idx, extra_key)
    if cache_key in _material_cache:
        return _material_cache[cache_key]

    # --- Determine blend mode BEFORE building the node tree ---
    # Alchemy IGB materials have explicit state attrs that control transparency.
    # We analyze them upfront so texture alpha connections and Blender blend
    # modes are set correctly in one pass.
    blend_decision = _decide_blend_mode(parsed_material, parsed_texture,
                                         extra_state, profile=profile)

    # Create material
    mat = bpy.data.materials.new(name=name)
    mat.use_nodes = True
    nodes = mat.node_tree.nodes
    links = mat.node_tree.links

    # Clear default nodes
    nodes.clear()

    # Create output node
    output_node = nodes.new(type='ShaderNodeOutputMaterial')
    output_node.location = (400, 0)

    # Create Principled BSDF
    bsdf = nodes.new(type='ShaderNodeBsdfPrincipled')
    bsdf.location = (0, 0)
    links.new(bsdf.outputs['BSDF'], output_node.inputs['Surface'])

    # Set material properties
    diffuse = parsed_material.diffuse
    bsdf.inputs['Base Color'].default_value = (
        diffuse[0], diffuse[1], diffuse[2], 1.0
    )

    # Specular
    specular = parsed_material.specular
    spec_intensity = max(specular[0], specular[1], specular[2])
    bsdf.inputs['Specular IOR Level'].default_value = spec_intensity

    # Roughness (inverse of shininess, normalized)
    # Alchemy shininess range: 0-128, higher = shinier
    shininess = parsed_material.shininess
    if shininess > 0:
        roughness = 1.0 - min(shininess / 128.0, 1.0)
    else:
        roughness = 1.0
    bsdf.inputs['Roughness'].default_value = roughness

    # Emission
    emission = parsed_material.emission
    emission_strength = max(emission[0], emission[1], emission[2])
    if emission_strength > 0.001:
        bsdf.inputs['Emission Color'].default_value = (
            emission[0], emission[1], emission[2], 1.0
        )
        bsdf.inputs['Emission Strength'].default_value = 1.0

    # Alpha/transparency from material diffuse alpha
    alpha = parsed_material.alpha
    if alpha < 0.999 and blend_decision != 'OPAQUE':
        bsdf.inputs['Alpha'].default_value = alpha

    # Add texture if available (pass blend_decision to control alpha wiring)
    if parsed_texture is not None and parsed_texture.image is not None:
        _add_texture_to_material(mat, bsdf, parsed_texture, links, nodes,
                                 blend_decision, profile=profile)

    # --- Store igMaterialAttr color properties as custom properties ---
    # These are used for round-trip fidelity (export reads them back) and
    # are displayed in the IGB Material State panel for user editing.
    mat["igb_diffuse"] = list(parsed_material.diffuse)
    mat["igb_ambient"] = list(parsed_material.ambient)
    mat["igb_specular"] = list(parsed_material.specular)
    mat["igb_emission"] = list(parsed_material.emission)
    mat["igb_shininess"] = parsed_material.shininess
    mat["igb_flags"] = parsed_material.flags

    # --- Apply extra material state attributes (custom props + visual) ---
    _apply_extra_state(mat, bsdf, extra_state, nodes, links, blend_decision)

    # --- Apply final Blender blend mode ---
    _apply_blend_decision(mat, blend_decision, extra_state)

    # Cache the material
    _material_cache[cache_key] = mat

    return mat


def _decide_blend_mode(parsed_material, parsed_texture, extra_state,
                       profile=None):
    """Determine the correct Blender blend mode from IGB material state.

    Alchemy's render pipeline uses explicit state attributes:
    - igBlendStateAttr._enabled   → alpha blending on/off
    - igAlphaStateAttr._enabled   → alpha test (cutout) on/off
    - igBlendFunctionAttr._src/dst → blend function (additive, standard, etc.)

    Mapping to Blender:
    - blend ON                        → 'BLEND'  (smooth semi-transparency)
    - alpha test ON, blend OFF        → 'CLIP'   (hard cutout)
    - blend ON + alpha test ON        → 'BLEND'  (game uses both; Blender
                                                   handles threshold via clip)
    - neither                         → 'OPAQUE' (no transparency at all)

    When NO extra_state is provided (e.g. IGZ path or minimal IGB), we fall
    back to heuristics: texture alpha + low material alpha → BLEND.

    For XML1 profiles (which often lack explicit igBlendStateAttr/igAlphaStateAttr),
    additional heuristics use the blend function src/dst values when available.

    Args:
        parsed_material: ParsedMaterial
        parsed_texture: ParsedTexture or None
        extra_state: dict of IGB state dicts
        profile: GameProfile or None

    Returns:
        str: 'OPAQUE', 'CLIP', or 'BLEND'
    """
    from ..scene_graph.sg_materials import (
        BLEND_SRC_ALPHA, BLEND_ONE_MINUS_SRC_ALPHA, BLEND_ONE,
    )

    blend_state = extra_state.get('blend_state')
    alpha_state = extra_state.get('alpha_state')
    blend_func = extra_state.get('blend_func')

    has_blend_info = blend_state is not None
    has_alpha_info = alpha_state is not None

    # Determine profile name for logging and heuristics
    profile_name = getattr(profile, 'game_name', 'unknown') if profile else 'unknown'
    profile_id = getattr(profile, 'game_id', '') if profile else ''
    is_xml1 = profile_id.lower().startswith('xml1') if profile_id else False

    mat_name = "(no material)"
    if parsed_material:
        mat_name = getattr(parsed_material, 'name', None) or str(
            _obj_cache_key(parsed_material.source_obj))

    if has_blend_info or has_alpha_info:
        # Explicit IGB state attributes are present — use them
        blend_on = blend_state.get('enabled', False) if blend_state else False
        alpha_on = alpha_state.get('enabled', False) if alpha_state else False

        # XML1 override: XML1 files commonly set igBlendStateAttr._enabled=True
        # globally as a default state, even on fully opaque geometry. We must
        # NOT treat this as a blend request unless there's also a meaningful
        # blend function (non-default src/dst) or the material alpha is low.
        if is_xml1 and blend_on and not alpha_on:
            has_meaningful_blend_func = False
            if blend_func is not None:
                src = blend_func.get('src', 0)
                dst = blend_func.get('dst', 0)
                # Standard alpha blend or additive = meaningful
                if (src == BLEND_SRC_ALPHA and
                        dst == BLEND_ONE_MINUS_SRC_ALPHA):
                    has_meaningful_blend_func = True
                elif src == BLEND_ONE and dst == BLEND_ONE:
                    has_meaningful_blend_func = True
                elif src == BLEND_SRC_ALPHA and dst == BLEND_ONE:
                    has_meaningful_blend_func = True

            mat_alpha_low = (parsed_material and
                             parsed_material.alpha < 0.999)

            if has_meaningful_blend_func or mat_alpha_low:
                decision = 'BLEND'
            else:
                decision = 'OPAQUE'

            if _blend_debug:
                _log.info(
                    f"[BLEND] {mat_name} [{profile_name}] XML1_OVERRIDE "
                    f"blend_state={blend_state} blend_func={blend_func} "
                    f"meaningful_func={has_meaningful_blend_func} "
                    f"mat_alpha_low={mat_alpha_low} → {decision}"
                )
            return decision

        if blend_on:
            decision = 'BLEND'
        elif alpha_on:
            decision = 'CLIP'
        else:
            # Check if material diffuse alpha itself is < 1 with blend on
            # (some files set low material alpha without explicit blend attr)
            if parsed_material and parsed_material.alpha < 0.999:
                decision = 'BLEND'
            else:
                decision = 'OPAQUE'

        if _blend_debug:
            _log.info(
                f"[BLEND] {mat_name} [{profile_name}] EXPLICIT "
                f"blend_state={blend_state} alpha_state={alpha_state} "
                f"blend_func={blend_func} → {decision}"
            )
        return decision
    else:
        # No explicit state — use heuristics
        has_tex_alpha = False
        if parsed_texture and parsed_texture.image:
            img = parsed_texture.image
            has_tex_alpha = (img.bits_alpha > 0 or
                            img.pixel_format in (15, 16, 65536, 65537))

        mat_alpha_low = (parsed_material and parsed_material.alpha < 0.999)

        # Color attr alpha
        color = extra_state.get('color')
        color_alpha_low = (color is not None and color[3] < 0.999)

        # XML1 profile-aware heuristics: use blend function when available
        # XML1 files often have blend_func but no blend_state/alpha_state attrs.
        if blend_func is not None:
            src = blend_func.get('src', 0)
            dst = blend_func.get('dst', 0)

            # Standard alpha blending: src=SRC_ALPHA, dst=ONE_MINUS_SRC_ALPHA
            if src == BLEND_SRC_ALPHA and dst == BLEND_ONE_MINUS_SRC_ALPHA:
                if _blend_debug:
                    _log.info(
                        f"[BLEND] {mat_name} [{profile_name}] HEURISTIC "
                        f"blend_func src={src} dst={dst} (standard alpha) → BLEND"
                    )
                return 'BLEND'

            # Additive blending: src=ONE, dst=ONE (glow effects)
            if src == BLEND_ONE and dst == BLEND_ONE:
                if _blend_debug:
                    _log.info(
                        f"[BLEND] {mat_name} [{profile_name}] HEURISTIC "
                        f"blend_func src={src} dst={dst} (additive) → BLEND"
                    )
                return 'BLEND'

            # src=SRC_ALPHA, dst=ONE (additive with alpha)
            if src == BLEND_SRC_ALPHA and dst == BLEND_ONE:
                if _blend_debug:
                    _log.info(
                        f"[BLEND] {mat_name} [{profile_name}] HEURISTIC "
                        f"blend_func src={src} dst={dst} (additive alpha) → BLEND"
                    )
                return 'BLEND'

        if mat_alpha_low or color_alpha_low:
            decision = 'BLEND'
        elif has_tex_alpha:
            # XML1 with texture alpha but no blend info:
            # Default to BLEND (smooth transparency) instead of CLIP for XML1,
            # since XML1 files rarely have explicit state attrs and the game
            # typically renders DXT3 textures with smooth alpha blending.
            if is_xml1:
                decision = 'BLEND'
            else:
                # XML2 / other: default to CLIP (cutout) as a safe visual default.
                # This prevents everything with DXT3 from going transparent.
                decision = 'CLIP'
        else:
            decision = 'OPAQUE'

        if _blend_debug:
            _log.info(
                f"[BLEND] {mat_name} [{profile_name}] HEURISTIC "
                f"has_tex_alpha={has_tex_alpha} mat_alpha_low={mat_alpha_low} "
                f"color_alpha_low={color_alpha_low} blend_func={blend_func} "
                f"is_xml1={is_xml1} → {decision}"
            )
        return decision


def _apply_blend_decision(mat, blend_decision, extra_state):
    """Apply the final Blender blend mode from the computed decision.

    Args:
        mat: Blender material
        blend_decision: 'OPAQUE', 'CLIP', or 'BLEND'
        extra_state: dict with IGB state (for alpha_func ref value)
    """
    if blend_decision == 'OPAQUE':
        if hasattr(mat, 'surface_render_method'):
            mat.surface_render_method = 'DITHERED'
        elif hasattr(mat, 'blend_method'):
            mat.blend_method = 'OPAQUE'
        if hasattr(mat, 'shadow_method'):
            mat.shadow_method = 'OPAQUE'

    elif blend_decision == 'CLIP':
        # Alpha clip / cutout — hard threshold
        if hasattr(mat, 'surface_render_method'):
            mat.surface_render_method = 'DITHERED'
        elif hasattr(mat, 'blend_method'):
            mat.blend_method = 'CLIP'
        if hasattr(mat, 'shadow_method'):
            mat.shadow_method = 'CLIP'
        # Set threshold from alpha function if available
        alpha_func = extra_state.get('alpha_func')
        if alpha_func is not None and hasattr(mat, 'alpha_threshold'):
            mat.alpha_threshold = alpha_func.get('ref', 0.5)
        elif hasattr(mat, 'alpha_threshold'):
            mat.alpha_threshold = 0.5

    elif blend_decision == 'BLEND':
        if hasattr(mat, 'surface_render_method'):
            mat.surface_render_method = 'BLENDED'
        elif hasattr(mat, 'blend_method'):
            mat.blend_method = 'BLEND'
        if hasattr(mat, 'shadow_method'):
            mat.shadow_method = 'CLIP'
        # Also set alpha threshold if alpha test is active alongside blend
        alpha_func = extra_state.get('alpha_func')
        if alpha_func is not None and hasattr(mat, 'alpha_threshold'):
            mat.alpha_threshold = alpha_func.get('ref', 0.5)


def _apply_extra_state(mat, bsdf, extra_state, nodes, links, blend_decision):
    """Apply IGB material state attributes as Blender custom properties.

    Sets custom properties on the material for round-trip fidelity,
    and adjusts visual Blender settings where applicable.

    The Blender blend mode is NOT set here — that's done by
    _apply_blend_decision() after this function returns.

    Args:
        mat: Blender material
        bsdf: Principled BSDF node
        extra_state: dict of IGB state dicts
        nodes: material node tree nodes
        links: material node tree links
        blend_decision: 'OPAQUE', 'CLIP', or 'BLEND' (from _decide_blend_mode)
    """
    from ..scene_graph.sg_materials import (
        BLEND_SRC_ALPHA, BLEND_ONE_MINUS_SRC_ALPHA, BLEND_ONE,
    )

    # --- Blend State ---
    blend_state = extra_state.get('blend_state')
    if blend_state is not None:
        mat["igb_blend_enabled"] = int(blend_state.get('enabled', False))

    # --- Blend Function ---
    blend_func = extra_state.get('blend_func')
    if blend_func is not None:
        mat["igb_blend_src"] = blend_func.get('src', BLEND_SRC_ALPHA)
        mat["igb_blend_dst"] = blend_func.get('dst', BLEND_ONE_MINUS_SRC_ALPHA)
        # Store PS2 fields too for completeness
        mat["igb_blend_eq"] = blend_func.get('eq', 0)
        mat["igb_blend_constant"] = blend_func.get('blend_constant', 0)
        mat["igb_blend_stage"] = blend_func.get('blend_stage', 0)
        mat["igb_blend_a"] = blend_func.get('blend_a', 0)
        mat["igb_blend_b"] = blend_func.get('blend_b', 0)
        mat["igb_blend_c"] = blend_func.get('blend_c', 0)
        mat["igb_blend_d"] = blend_func.get('blend_d', 0)

    # --- Alpha State ---
    alpha_state = extra_state.get('alpha_state')
    if alpha_state is not None:
        mat["igb_alpha_test_enabled"] = int(alpha_state.get('enabled', False))

    # --- Alpha Function ---
    alpha_func = extra_state.get('alpha_func')
    if alpha_func is not None:
        mat["igb_alpha_func"] = alpha_func.get('func', 6)
        mat["igb_alpha_ref"] = alpha_func.get('ref', 0.5)

    # --- Color Attr ---
    color = extra_state.get('color')
    if color is not None:
        # Store as individual floats (Blender custom props don't support arrays well)
        mat["igb_color_r"] = color[0]
        mat["igb_color_g"] = color[1]
        mat["igb_color_b"] = color[2]
        mat["igb_color_a"] = color[3]
        # If not white, insert a Multiply node for visual feedback
        is_white = (abs(color[0] - 1.0) < 0.001 and abs(color[1] - 1.0) < 0.001
                    and abs(color[2] - 1.0) < 0.001)
        if not is_white:
            _insert_color_multiply(mat, bsdf, color, nodes, links)
        # If color alpha < 1.0 and blend is active, set BSDF alpha
        if color[3] < 0.999 and blend_decision != 'OPAQUE':
            bsdf.inputs['Alpha'].default_value = color[3]

    # --- Lighting State ---
    lighting_state = extra_state.get('lighting_state')
    if lighting_state is not None:
        mat["igb_lighting_enabled"] = int(lighting_state.get('enabled', True))

    # --- Texture Matrix State ---
    tex_matrix = extra_state.get('tex_matrix_state')
    if tex_matrix is not None:
        mat["igb_tex_matrix_enabled"] = int(tex_matrix.get('enabled', False))
        mat["igb_tex_matrix_unit_id"] = tex_matrix.get('unit_id', 0)

    # --- Backface Culling ---
    cull_face = extra_state.get('cull_face')
    if cull_face is not None:
        mat["igb_cull_face_enabled"] = int(cull_face.get('enabled', True))
        mat["igb_cull_face_mode"] = cull_face.get('mode', 0)
        # Apply to Blender material setting
        mat.use_backface_culling = bool(cull_face.get('enabled', True))


def _insert_color_multiply(mat, bsdf, color, nodes, links):
    """Insert a MixRGB Multiply node between texture/base color and BSDF.

    This provides a visual representation of igColorAttr tinting.
    """
    # Find what's currently connected to Base Color
    base_color_input = bsdf.inputs['Base Color']
    existing_link = None
    for link in mat.node_tree.links:
        if link.to_socket == base_color_input:
            existing_link = link
            break

    # Create Mix node (Multiply)
    mix_node = nodes.new(type='ShaderNodeMix')
    mix_node.location = (-200, 100)
    mix_node.data_type = 'RGBA'
    mix_node.blend_type = 'MULTIPLY'
    mix_node.inputs['Factor'].default_value = 1.0
    mix_node.inputs[7].default_value = (color[0], color[1], color[2], 1.0)

    if existing_link is not None:
        # Reconnect: texture -> Mix A, color -> Mix B, Mix -> BSDF
        source = existing_link.from_socket
        links.remove(existing_link)
        links.new(source, mix_node.inputs[6])  # A input
    else:
        # No texture, use current Base Color value
        current_color = base_color_input.default_value
        mix_node.inputs[6].default_value = (
            current_color[0], current_color[1], current_color[2], 1.0)

    links.new(mix_node.outputs[2], base_color_input)  # Result -> Base Color


def _add_texture_to_material(mat, bsdf, parsed_texture, links, nodes,
                             blend_decision='OPAQUE', profile=None):
    """Add a texture to a material's node tree.

    Creates Image Texture node connected to Principled BSDF Base Color,
    with optional UV mapping node.

    Alpha is ONLY connected to the BSDF when the blend_decision requires
    transparency (CLIP or BLEND). This prevents opaque materials from
    becoming see-through just because their DXT3 texture has an alpha channel.

    Args:
        mat: Blender material
        bsdf: Principled BSDF node
        parsed_texture: ParsedTexture from sg_materials
        links: node tree links
        nodes: node tree nodes
        blend_decision: 'OPAQUE', 'CLIP', or 'BLEND'
    """
    img = parsed_texture.image
    if img is None or img.pixel_data is None:
        return

    # Get or create the Blender image
    bl_image = _get_or_create_blender_image(img, profile=profile)
    if bl_image is None:
        return

    # Create Image Texture node
    tex_node = nodes.new(type='ShaderNodeTexImage')
    tex_node.location = (-400, 0)
    tex_node.image = bl_image

    # Set interpolation
    if parsed_texture.mag_filter == 0:  # NEAREST
        tex_node.interpolation = 'Closest'
    else:
        tex_node.interpolation = 'Linear'

    # Set extension (wrap mode)
    if parsed_texture.wrap_s == WRAP_REPEAT and parsed_texture.wrap_t == WRAP_REPEAT:
        tex_node.extension = 'REPEAT'
    else:
        tex_node.extension = 'EXTEND'

    # Connect color output to Base Color
    links.new(tex_node.outputs['Color'], bsdf.inputs['Base Color'])

    # Connect alpha output ONLY when the material actually needs transparency.
    # Many IGB textures are DXT3 (always has alpha channel) but the game
    # treats them as opaque when igBlendStateAttr._enabled = false and
    # igAlphaStateAttr._enabled = false. Connecting alpha unconditionally
    # would make every DXT3 material semi-transparent in Blender.
    if blend_decision in ('CLIP', 'BLEND'):
        has_tex_alpha = (img.bits_alpha > 0 or
                         img.pixel_format in (15, 16, 65536, 65537))
        if has_tex_alpha:
            links.new(tex_node.outputs['Alpha'], bsdf.inputs['Alpha'])

    # Add UV Map node
    uv_node = nodes.new(type='ShaderNodeUVMap')
    uv_node.location = (-700, 0)
    uv_node.uv_map = "UVMap"
    links.new(uv_node.outputs['UV'], tex_node.inputs['Vector'])


def _get_or_create_blender_image(parsed_image, profile=None):
    """Get a cached Blender image or create a new one from ParsedImage.

    Decompresses DXT data and creates a Blender image with pixel data.

    Args:
        parsed_image: ParsedImage from sg_materials
        profile: GameProfile instance (used for R/B channel swap on MUA PC)

    Returns:
        bpy.types.Image or None
    """
    swap_rb = profile is not None and profile.texture.swap_rb

    # Determine cache key: prefer explicit cache_key (file path for IGZ),
    # fall back to source object offset (fine for IGB where objects are in one file)
    cache_key = getattr(parsed_image, 'cache_key', None)
    if cache_key is None and parsed_image.source_obj is not None:
        cache_key = _obj_cache_key(parsed_image.source_obj)

    # Include swap_rb in cache key so different profiles get correct colors
    if cache_key is not None and swap_rb:
        cache_key = (cache_key, 'swap_rb')

    if cache_key is not None and cache_key in _image_cache:
        name = _image_cache[cache_key]
        if name in bpy.data.images:
            return bpy.data.images[name]

    # Generate a unique name
    base_name = parsed_image.base_name
    if not base_name:
        if parsed_image.source_obj:
            base_name = f"igb_image_{_obj_cache_key(parsed_image.source_obj)}"
        else:
            base_name = "igb_image"

    # Remove extension for Blender image name
    if '.' in base_name:
        base_name = base_name.rsplit('.', 1)[0]

    w = parsed_image.width
    h = parsed_image.height

    if w == 0 or h == 0:
        return None

    # Convert pixel data to RGBA
    rgba_data = convert_image_to_rgba(parsed_image)
    if rgba_data is None:
        return None

    # Swap R and B channels if profile requires it (e.g. MUA PC)
    if swap_rb:
        for i in range(len(rgba_data) // 4):
            off = i * 4
            rgba_data[off], rgba_data[off + 2] = rgba_data[off + 2], rgba_data[off]

    # Create Blender image
    bl_image = bpy.data.images.new(
        name=base_name,
        width=w,
        height=h,
        alpha=True,
    )

    # Set pixel data
    # Blender expects float pixels in range 0.0-1.0, bottom-to-top row order
    # Our data is top-to-bottom, so we need to flip vertically
    num_pixels = w * h
    float_pixels = [0.0] * (num_pixels * 4)

    for y in range(h):
        src_row = y
        dst_row = h - 1 - y  # flip vertically
        for x in range(w):
            src_idx = (src_row * w + x) * 4
            dst_idx = (dst_row * w + x) * 4
            float_pixels[dst_idx] = rgba_data[src_idx] / 255.0        # R
            float_pixels[dst_idx + 1] = rgba_data[src_idx + 1] / 255.0  # G
            float_pixels[dst_idx + 2] = rgba_data[src_idx + 2] / 255.0  # B
            float_pixels[dst_idx + 3] = rgba_data[src_idx + 3] / 255.0  # A

    bl_image.pixels.foreach_set(float_pixels)

    # Pack into blend file so it's not lost
    bl_image.pack()

    # Cache it
    if cache_key is not None:
        _image_cache[cache_key] = bl_image.name

    return bl_image
