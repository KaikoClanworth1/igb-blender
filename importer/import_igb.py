"""IGB file import operator for Blender.

Reads an Alchemy Engine IGB file and creates Blender objects
with meshes, normals, UVs, vertex colors, materials, and textures.

Supports multiple games via GameProfile configuration (see game_profiles.py).
"""

import bpy
import os
import time
import struct

from ..igb_format.igb_reader import IGBReader
from ..scene_graph.sg_classes import SceneGraph
from ..scene_graph.sg_geometry import extract_geometry
from ..scene_graph.sg_materials import extract_material, extract_texture_bind
from .mesh_builder import build_mesh, _tuple_to_matrix
from .material_builder import build_material, clear_caches
from ..game_profiles import detect_profile, get_profile


class GeometryCollector:
    """Scene graph visitor that collects geometry instances with material state.

    Materials and textures are inherited through the scene graph hierarchy.
    When we encounter a material or texture attr on a parent node, it becomes
    the "current" state that applies to all child geometries until overridden.

    Alchemy scene graphs use instancing (shared nodes) - the same geometry
    can appear multiple times with different transforms. We collect every
    instance, tracking which geometry attr it references so the importer
    can create linked duplicates in Blender.
    """

    def __init__(self, reader):
        self.reader = reader
        self.instances = []  # list of (attr_index, transform, state_dict)
        self.light_sets = []  # list of (light_set_obj, transform)
        self._current_material_obj = None
        self._current_texbind_obj = None
        self._current_blend_state_obj = None
        self._current_blend_func_obj = None
        self._current_alpha_state_obj = None
        self._current_alpha_func_obj = None
        self._current_color_obj = None
        self._current_lighting_state_obj = None
        self._current_tex_matrix_state_obj = None
        self._current_cull_face_obj = None

    def visit_material_attr(self, attr, parent):
        """Called when we encounter an igMaterialAttr in the scene graph."""
        self._current_material_obj = attr

    def visit_texture_bind_attr(self, attr, parent):
        """Called when we encounter an igTextureBindAttr in the scene graph."""
        self._current_texbind_obj = attr

    def visit_blend_state_attr(self, attr, parent):
        """Called when we encounter an igBlendStateAttr."""
        self._current_blend_state_obj = attr

    def visit_blend_function_attr(self, attr, parent):
        """Called when we encounter an igBlendFunctionAttr."""
        self._current_blend_func_obj = attr

    def visit_alpha_state_attr(self, attr, parent):
        """Called when we encounter an igAlphaStateAttr."""
        self._current_alpha_state_obj = attr

    def visit_alpha_function_attr(self, attr, parent):
        """Called when we encounter an igAlphaFunctionAttr."""
        self._current_alpha_func_obj = attr

    def visit_color_attr(self, attr, parent):
        """Called when we encounter an igColorAttr."""
        self._current_color_obj = attr

    def visit_lighting_state_attr(self, attr, parent):
        """Called when we encounter an igLightingStateAttr."""
        self._current_lighting_state_obj = attr

    def visit_tex_matrix_state_attr(self, attr, parent):
        """Called when we encounter an igTextureMatrixStateAttr."""
        self._current_tex_matrix_state_obj = attr

    def visit_cull_face_attr(self, attr, parent):
        """Called when we encounter an igCullFaceAttr."""
        self._current_cull_face_obj = attr

    def _snapshot_state(self):
        """Capture current material state as a dict for geometry instances."""
        return {
            'material_obj': self._current_material_obj,
            'texbind_obj': self._current_texbind_obj,
            'blend_state_obj': self._current_blend_state_obj,
            'blend_func_obj': self._current_blend_func_obj,
            'alpha_state_obj': self._current_alpha_state_obj,
            'alpha_func_obj': self._current_alpha_func_obj,
            'color_obj': self._current_color_obj,
            'lighting_state_obj': self._current_lighting_state_obj,
            'tex_matrix_state_obj': self._current_tex_matrix_state_obj,
            'cull_face_obj': self._current_cull_face_obj,
        }

    def visit_geometry_attr(self, attr, transform, parent):
        """Called when we encounter an igGeometryAttr in the scene graph.

        The same attr can be visited multiple times (instancing) with
        different transforms. We record each instance with full material state.
        """
        self.instances.append((
            attr.index, transform,
            self._snapshot_state(),
        ))

    def visit_light_set(self, obj, transform):
        """Called when we encounter an igLightSet in the scene graph."""
        self.light_sets.append((obj, transform))


def _finalize_imported_objects(collection):
    """Make single user on object data and apply all transforms.

    After import, mesh objects may be linked duplicates sharing mesh data.
    This makes each object's data unique and bakes the world matrix into
    the mesh vertices so the object has identity transforms.
    """
    import mathutils

    identity = mathutils.Matrix.Identity(4)

    for obj in collection.objects:
        if obj.type != 'MESH' or obj.data is None:
            continue

        # Make single user — give this object its own unique mesh copy
        if obj.data.users > 1:
            obj.data = obj.data.copy()

        # Apply all transforms — bake matrix_world into mesh vertices
        mat = obj.matrix_world
        if mat == identity:
            continue

        obj.data.transform(mat)
        obj.data.update()

        # Reset object transform to identity
        obj.matrix_world = identity.copy()


def import_igb(context, filepath, operator=None):
    """Import an IGB file into the current Blender scene.

    Args:
        context: Blender context
        filepath: path to the .igb file
        operator: the import operator (for options and error reporting)

    Returns:
        {'FINISHED'} or {'CANCELLED'}
    """
    t_start = time.time()
    filename = os.path.basename(filepath)
    basename = os.path.splitext(filename)[0]

    # Check for IGZ format (Alchemy 5.0+ — MUA2 PC, Crash NST, etc.)
    if filepath.lower().endswith('.igz') or _is_igz_file(filepath):
        return _import_igz(context, filepath, operator)

    # Get import options
    options = {
        'import_normals': True,
        'import_uvs': True,
        'import_vertex_colors': True,
        'import_materials': True,
        'import_collision': True,
        'import_lights': True,
    }
    if operator is not None:
        options['import_normals'] = getattr(operator, 'import_normals', True)
        options['import_uvs'] = getattr(operator, 'import_uvs', True)
        options['import_vertex_colors'] = getattr(operator, 'import_vertex_colors', True)
        options['import_materials'] = getattr(operator, 'import_materials', True)
        options['import_collision'] = getattr(operator, 'import_collision', True)
        options['import_lights'] = getattr(operator, 'import_lights', True)

    # Clear material/image caches for fresh import
    clear_caches()

    # Parse IGB file
    try:
        reader = IGBReader(filepath)
        reader.read()
    except Exception as e:
        _report(operator, 'ERROR', f"Failed to parse IGB file: {e}")
        return {'CANCELLED'}

    # Resolve game profile (manual selection or auto-detect)
    game_preset = getattr(operator, 'game_preset', 'auto') if operator else 'auto'
    if game_preset == "auto":
        profile = detect_profile(reader)
        if profile is not None:
            _report(operator, 'INFO', f"Auto-detected game: {profile.game_name}")
        else:
            _report(operator, 'WARNING', "Could not auto-detect game, using XML2 PC defaults")
            profile = get_profile("xml2_pc")
    else:
        profile = get_profile(game_preset)
        if profile is None:
            _report(operator, 'WARNING', f"Unknown game preset '{game_preset}', using XML2 PC")
            profile = get_profile("xml2_pc")

    # Build scene graph
    try:
        sg = SceneGraph(reader)
        if not sg.build():
            _report(operator, 'ERROR', "Failed to build scene graph from IGB file")
            return {'CANCELLED'}
    except Exception as e:
        _report(operator, 'ERROR', f"Failed to build scene graph: {e}")
        return {'CANCELLED'}

    # Collect geometry instances with material state
    collector = GeometryCollector(reader)
    sg.walk(collector)

    if not collector.instances:
        _report(operator, 'WARNING', "No geometry found in IGB file")
        return {'CANCELLED'}

    # Create a collection for the imported file
    collection = bpy.data.collections.new(basename)
    context.scene.collection.children.link(collection)

    # Build Blender meshes with materials.
    # Alchemy scene graphs use instancing (shared geometry nodes referenced
    # from multiple parents with different transforms). We build the mesh
    # data once per unique geometry attr, then create linked duplicates
    # for each additional instance (sharing the same mesh data).
    created_count = 0
    materials_created = set()
    mesh_cache = {}  # attr_index -> (bpy.types.Mesh, name)

    geom_fail_count = 0
    mesh_fail_count = 0
    geom_fail_seen = set()

    for i, (attr_index, transform, state_dict) in enumerate(collector.instances):
        if attr_index in mesh_cache:
            # Linked duplicate: reuse existing mesh data, new object with own transform
            cached_mesh, base_name = mesh_cache[attr_index]
            obj_name = f"{base_name}.{i:03d}"
            obj = bpy.data.objects.new(obj_name, cached_mesh)

            # Apply instance transform
            if transform is not None:
                obj.matrix_world = _tuple_to_matrix(transform)
        else:
            # First instance: build the mesh data
            attr_obj = reader.objects[attr_index]
            try:
                geom = extract_geometry(reader, attr_obj, profile)
            except Exception as e:
                if attr_index not in geom_fail_seen:
                    geom_fail_seen.add(attr_index)
                    print(f"[IGB] extract_geometry failed for obj[{attr_index}]: {e}")
                geom_fail_count += 1
                continue
            if geom is None or geom.num_verts == 0:
                if attr_index not in geom_fail_seen:
                    geom_fail_seen.add(attr_index)
                    geom_fail_count += 1
                continue

            mesh_name = f"{basename}_{created_count:03d}"
            try:
                obj = build_mesh(geom, mesh_name, transform, options, profile)
            except Exception as e:
                print(f"[IGB] build_mesh failed for {mesh_name}: {e}")
                mesh_fail_count += 1
                continue
            if obj is None:
                mesh_fail_count += 1
                continue

            # Cache the mesh data for linked duplicates
            mesh_cache[attr_index] = (obj.data, mesh_name)

            # Build and assign material (only on first instance - shared via mesh)
            mat_obj = state_dict.get('material_obj')
            tex_obj = state_dict.get('texbind_obj')
            if options.get('import_materials', True) and mat_obj is not None:
                parsed_mat = extract_material(reader, mat_obj, profile)
                parsed_tex = None
                if tex_obj is not None:
                    parsed_tex = extract_texture_bind(reader, tex_obj, profile)

                # Extract additional material state attributes
                from ..scene_graph.sg_materials import (
                    extract_blend_state, extract_blend_function,
                    extract_alpha_state, extract_alpha_function,
                    extract_color_attr, extract_lighting_state,
                    extract_tex_matrix_state, extract_cull_face,
                )
                extra_state = {}
                if state_dict.get('blend_state_obj') is not None:
                    extra_state['blend_state'] = extract_blend_state(
                        reader, state_dict['blend_state_obj'], profile)
                if state_dict.get('blend_func_obj') is not None:
                    extra_state['blend_func'] = extract_blend_function(
                        reader, state_dict['blend_func_obj'], profile)
                if state_dict.get('alpha_state_obj') is not None:
                    extra_state['alpha_state'] = extract_alpha_state(
                        reader, state_dict['alpha_state_obj'], profile)
                if state_dict.get('alpha_func_obj') is not None:
                    extra_state['alpha_func'] = extract_alpha_function(
                        reader, state_dict['alpha_func_obj'], profile)
                if state_dict.get('color_obj') is not None:
                    extra_state['color'] = extract_color_attr(
                        reader, state_dict['color_obj'], profile)
                if state_dict.get('lighting_state_obj') is not None:
                    extra_state['lighting_state'] = extract_lighting_state(
                        reader, state_dict['lighting_state_obj'], profile)
                if state_dict.get('tex_matrix_state_obj') is not None:
                    extra_state['tex_matrix_state'] = extract_tex_matrix_state(
                        reader, state_dict['tex_matrix_state_obj'], profile)
                if state_dict.get('cull_face_obj') is not None:
                    extra_state['cull_face'] = extract_cull_face(
                        reader, state_dict['cull_face_obj'], profile)

                mat_name = _make_material_name(basename, parsed_mat, parsed_tex,
                                               created_count)
                bl_material = build_material(parsed_mat, parsed_tex,
                                             extra_state=extra_state,
                                             name=mat_name,
                                             profile=profile)

                if bl_material is not None:
                    obj.data.materials.append(bl_material)
                    materials_created.add(bl_material.name)

        collection.objects.link(obj)
        created_count += 1

    # Import collision hull(s) if requested
    collision_tris = 0
    if options.get('import_collision', True):
        collision_tris = _import_collision_hulls(reader, basename, context, operator)

    # Import lights from igLightSet nodes
    light_count = 0
    if options.get('import_lights', True) and collector.light_sets:
        light_count = _import_lights(
            reader, basename, collection, collector.light_sets,
            profile, operator)

    # Post-import: make single user + apply transforms for all mesh objects
    # so linked duplicates get their own mesh data with baked world transforms
    _finalize_imported_objects(collection)

    t_elapsed = time.time() - t_start
    unique_meshes = len(mesh_cache)

    fail_info = ""
    if geom_fail_count > 0 or mesh_fail_count > 0:
        fail_info = f" [geom_fail={geom_fail_count}, mesh_fail={mesh_fail_count}]"

    collision_info = ""
    if collision_tris > 0:
        collision_info = f", collision={collision_tris} tris"

    light_info = ""
    if light_count > 0:
        light_info = f", {light_count} lights"

    _report(operator, 'INFO',
            f"Imported {created_count} objects ({unique_meshes} unique meshes), "
            f"{len(materials_created)} materials{collision_info}{light_info} "
            f"from {filename} "
            f"[{profile.game_name}] ({t_elapsed:.2f}s){fail_info}")

    if created_count == 0 and len(collector.instances) > 0:
        _report(operator, 'WARNING',
                f"Scene graph found {len(collector.instances)} geometry instances "
                f"but none could be extracted. Check Blender console for details.")

    return {'FINISHED'}


def _import_igz(context, filepath, operator=None):
    """Import an IGZ format file (Alchemy 5.0+) into the current Blender scene.

    IGZ files use a completely different binary format from IGB. This function
    uses the dedicated IGZ parser and geometry extraction pipeline, then feeds
    the results into the shared mesh_builder.

    Args:
        context: Blender context
        filepath: path to the .igz file
        operator: the import operator (for options and error reporting)

    Returns:
        {'FINISHED'} or {'CANCELLED'}
    """
    t_start = time.time()
    filename = os.path.basename(filepath)
    basename = os.path.splitext(filename)[0]

    # Get import options
    options = {
        'import_normals': True,
        'import_uvs': True,
        'import_vertex_colors': True,
        'import_materials': True,
        'import_collision': False,
        'import_lights': False,
    }
    if operator is not None:
        options['import_normals'] = getattr(operator, 'import_normals', True)
        options['import_uvs'] = getattr(operator, 'import_uvs', True)
        options['import_vertex_colors'] = getattr(operator, 'import_vertex_colors', True)
        options['import_materials'] = getattr(operator, 'import_materials', True)

    # Parse IGZ file
    try:
        from ..igz_format.igz_reader import IGZReader
        from ..igz_format.igz_geometry import (
            IGZDataAllocator, extract_igz_geometry, walk_igz_scene_graph,
        )

        reader = IGZReader(filepath)
        reader.read()
    except Exception as e:
        _report(operator, 'ERROR', f"Failed to parse IGZ file: {e}")
        return {'CANCELLED'}

    # Build data allocator (locates raw vertex/index data in file)
    try:
        allocator = IGZDataAllocator(reader)
    except Exception as e:
        _report(operator, 'ERROR', f"Failed to build IGZ data allocator: {e}")
        return {'CANCELLED'}

    # Walk scene graph to collect geometry instances
    try:
        results = walk_igz_scene_graph(reader, allocator)
    except Exception as e:
        _report(operator, 'ERROR', f"Failed to walk IGZ scene graph: {e}")
        return {'CANCELLED'}

    if not results:
        # Fallback: extract all igGeometryAttr objects without scene graph
        geom_attrs = reader.get_objects_by_type('igGeometryAttr')
        if not geom_attrs:
            _report(operator, 'WARNING', "No geometry found in IGZ file")
            return {'CANCELLED'}
        _report(operator, 'INFO',
                f"No scene graph root found, extracting {len(geom_attrs)} "
                f"geometry attrs directly")
        identity = (1, 0, 0, 0, 0, 1, 0, 0, 0, 0, 1, 0, 0, 0, 0, 1)
        for ga in geom_attrs:
            geom = extract_igz_geometry(reader, ga, allocator)
            if geom is not None:
                results.append({
                    'geom': geom,
                    'transform': identity,
                    'material_state': {},
                })

    if not results:
        _report(operator, 'WARNING', "No geometry could be extracted from IGZ file")
        return {'CANCELLED'}

    # --- Material/texture setup ---
    tex_attr_to_path = {}
    tex_image_cache = {}  # texture_path -> ParsedImage
    mat_cache = {}  # (mat_obj_offset, texbind_obj_offset) -> bpy.types.Material

    # Get IGZ texture directory from operator (user-specified)
    igz_texture_dir = ""
    if operator is not None:
        igz_texture_dir = getattr(operator, 'igz_texture_dir', "") or ""

    if options.get('import_materials', True):
        try:
            from ..igz_format.igz_materials import (
                build_texture_path_map, extract_igz_material,
                resolve_texture_bind, load_external_texture,
                resolve_all_texture_binds, classify_texture_role,
                extract_igz_blend_state, extract_igz_blend_function,
                extract_igz_alpha_state,
            )
            from .material_builder import (
                build_material as build_mat_func,
                build_igz_multitex_material,
                clear_caches,
            )

            clear_caches()
            tex_attr_to_path = build_texture_path_map(reader)
            _report(operator, 'INFO',
                    f"Found {len(tex_attr_to_path)} texture path mappings")
        except Exception as e:
            print(f"[IGZ] Material setup failed: {e}")
            import traceback
            traceback.print_exc()

    # Create a collection for the imported file
    collection = bpy.data.collections.new(basename)
    context.scene.collection.children.link(collection)

    # Build Blender meshes.
    # Track unique geometry attrs for linked duplicates.
    created_count = 0
    geom_fail_count = 0
    tex_load_count = 0
    mesh_cache = {}  # geom_attr_global_offset -> (bpy.types.Mesh, name)

    for i, result in enumerate(results):
        geom = result['geom']
        transform = result['transform']
        mat_state = result.get('material_state', {})

        # Use the source object's global offset as a cache key
        cache_key = None
        if geom.source_obj is not None:
            cache_key = geom.source_obj.global_offset

        if cache_key is not None and cache_key in mesh_cache:
            # Linked duplicate
            cached_mesh, base_name, cached_mat = mesh_cache[cache_key]
            obj_name = f"{base_name}.{i:03d}"
            obj = bpy.data.objects.new(obj_name, cached_mesh)
            if transform is not None:
                obj.matrix_world = _tuple_to_matrix(transform)
            if cached_mat is not None:
                obj.data.materials.append(cached_mat)
        else:
            # First instance: build mesh
            if geom.num_verts == 0:
                geom_fail_count += 1
                continue

            mesh_name = f"{basename}_{created_count:03d}"
            try:
                obj = build_mesh(geom, mesh_name, transform, options, profile=None)
            except Exception as e:
                print(f"[IGZ] build_mesh failed for {mesh_name}: {e}")
                geom_fail_count += 1
                continue
            if obj is None:
                geom_fail_count += 1
                continue

            # --- Assign material ---
            bl_mat = None
            if options.get('import_materials', True) and tex_attr_to_path:
                bl_mat = _build_igz_material(
                    reader, mat_state, tex_attr_to_path,
                    tex_image_cache, mat_cache, filepath, mesh_name,
                    igz_texture_dir,
                )
                if bl_mat is not None:
                    obj.data.materials.append(bl_mat)
                    tex_load_count += 1

            if cache_key is not None:
                mesh_cache[cache_key] = (obj.data, mesh_name, bl_mat)

        collection.objects.link(obj)
        created_count += 1

    # Post-import: make single user + apply transforms
    _finalize_imported_objects(collection)

    t_elapsed = time.time() - t_start
    unique_meshes = len(mesh_cache)

    fail_info = ""
    if geom_fail_count > 0:
        fail_info = f" [{geom_fail_count} failed]"

    mat_info = ""
    if tex_load_count > 0:
        mat_info = f" [{tex_load_count} textured]"

    _report(operator, 'INFO',
            f"Imported {created_count} objects ({unique_meshes} unique meshes) "
            f"from {filename} [IGZ] ({t_elapsed:.2f}s){fail_info}{mat_info}")

    if created_count == 0:
        _report(operator, 'WARNING',
                "No geometry could be imported. Check Blender console for details.")

    return {'FINISHED'}


def _build_igz_material(reader, mat_state, tex_attr_to_path,
                        tex_image_cache, mat_cache, filepath, mesh_name,
                        igz_texture_dir=""):
    """Build a Blender material from IGZ material state with multi-texture support.

    Resolves ALL texture binds accumulated by the scene graph walker,
    classifies each by filename suffix (diffuse, normal, specular, etc.),
    and builds a Principled BSDF material with proper node wiring.

    Args:
        reader: IGZReader for the geometry file
        mat_state: dict from walk_igz_scene_graph with material/texture objects
        tex_attr_to_path: mapping from igTextureAttr2 offset -> texture file path
        tex_image_cache: dict caching texture_path -> ParsedImage
        mat_cache: dict caching cache_key -> bpy.types.Material
        filepath: path to the geometry IGZ file (for relative texture resolution)
        mesh_name: name for the material
        igz_texture_dir: user-specified directory containing materials/ subfolders

    Returns:
        bpy.types.Material or None
    """
    from ..igz_format.igz_materials import (
        extract_igz_material, resolve_all_texture_binds,
        load_external_texture, classify_texture_role,
        extract_igz_blend_state, extract_igz_blend_function,
        extract_igz_alpha_state, extract_igz_color,
        TEX_ROLE_DIFFUSE,
    )
    from .material_builder import build_igz_multitex_material

    mat_obj = mat_state.get('material_obj')
    texbind_list = mat_state.get('texbind_list', [])

    # --- Build cache key from material + all texture binds ---
    mat_off = mat_obj.global_offset if mat_obj else -1
    bind_offsets = tuple(tb.global_offset for tb in texbind_list)
    cache_key = (mat_off, bind_offsets)

    if cache_key in mat_cache:
        return mat_cache[cache_key]

    # --- Extract material properties ---
    parsed_mat = None
    if mat_obj is not None:
        parsed_mat = extract_igz_material(reader, mat_obj)
    else:
        from ..scene_graph.sg_materials import ParsedMaterial
        parsed_mat = ParsedMaterial()

    # --- Resolve all texture binds to role-classified paths ---
    role_map = resolve_all_texture_binds(reader, texbind_list, tex_attr_to_path)

    # --- Load external textures for each role ---
    texture_role_images = {}  # TEX_ROLE_* -> ParsedImage
    for role, (tex_path, texbind_obj) in role_map.items():
        parsed_image = tex_image_cache.get(tex_path)
        if parsed_image is None:
            parsed_image = load_external_texture(
                tex_path,
                game_data_dir=igz_texture_dir or None,
                filepath=filepath,
            )
            if parsed_image is not None:
                tex_image_cache[tex_path] = parsed_image

        if parsed_image is not None:
            texture_role_images[role] = parsed_image

    # --- Build extra_state from IGZ material state objects ---
    extra_state = {}
    blend_state_obj = mat_state.get('blend_state_obj')
    if blend_state_obj is not None:
        extra_state['blend_state'] = extract_igz_blend_state(blend_state_obj)

    blend_func_obj = mat_state.get('blend_func_obj')
    if blend_func_obj is not None:
        extra_state['blend_func'] = extract_igz_blend_function(blend_func_obj)

    alpha_state_obj = mat_state.get('alpha_state_obj')
    if alpha_state_obj is not None:
        extra_state['alpha_state'] = extract_igz_alpha_state(alpha_state_obj)

    color_obj = mat_state.get('color_obj')
    if color_obj is not None:
        color = extract_igz_color(reader, color_obj)
        if color is not None:
            extra_state['color'] = color

    # --- Determine material name ---
    mat_name = mesh_name
    diffuse_img = texture_role_images.get(TEX_ROLE_DIFFUSE)
    if diffuse_img and diffuse_img.name:
        img_base = diffuse_img.name.replace('\\', '/').split('/')[-1]
        if '.' in img_base:
            img_base = img_base.rsplit('.', 1)[0]
        # Strip _d suffix for cleaner name
        if img_base.lower().endswith('_d'):
            img_base = img_base[:-2]
        if img_base:
            mat_name = img_base

    # --- Build the Blender material ---
    if texture_role_images:
        bl_mat = build_igz_multitex_material(
            parsed_mat, texture_role_images,
            extra_state=extra_state, name=mat_name)
    else:
        # No textures loaded — fall back to simple material
        from .material_builder import build_material as build_mat_func
        bl_mat = build_mat_func(parsed_mat, None,
                                extra_state=extra_state, name=mat_name)

    mat_cache[cache_key] = bl_mat
    return bl_mat


def _make_material_name(basename, parsed_mat, parsed_tex, index):
    """Generate a meaningful material name.

    Uses the texture filename if available, otherwise falls back to
    material index or generic naming.
    """
    if parsed_tex is not None and parsed_tex.image is not None:
        img_name = parsed_tex.image.base_name
        if img_name:
            # Remove extension
            if '.' in img_name:
                img_name = img_name.rsplit('.', 1)[0]
            return f"{basename}_{img_name}"

    if parsed_mat is not None and parsed_mat.source_obj is not None:
        return f"{basename}_mat{parsed_mat.source_obj.index}"

    return f"{basename}_mat{index:03d}"


# ===========================================================================
# Light import
# ===========================================================================

def _import_lights(reader, basename, collection, light_sets, profile, operator):
    """Import lights from collected igLightSet nodes.

    For each igLightSet, extracts igLightAttr objects and creates Blender
    lights. The special "SceneAmbient" light set is converted to the
    Blender world ambient color instead of a light object.

    Args:
        reader: IGBReader instance
        basename: file basename for naming
        collection: Blender collection to link light objects into
        light_sets: list of (light_set_obj, transform) tuples
        profile: GameProfile instance
        operator: import operator for reporting

    Returns:
        int: number of lights imported
    """
    from ..scene_graph.sg_lights import extract_lights_from_light_set
    from .light_builder import build_light, set_world_ambient

    light_count = 0

    for light_set_obj, transform in light_sets:
        try:
            parsed_lights = extract_lights_from_light_set(
                reader, light_set_obj, profile)
        except Exception as e:
            _report(operator, 'WARNING',
                    f"Failed to extract lights from igLightSet: {e}")
            continue

        for parsed_light in parsed_lights:
            if parsed_light.is_ambient:
                # SceneAmbient -> set world background color
                try:
                    set_world_ambient(parsed_light)
                    light_count += 1
                    _report(operator, 'INFO',
                            f"  Set world ambient from SceneAmbient light")
                except Exception as e:
                    _report(operator, 'WARNING',
                            f"Failed to set world ambient: {e}")
                continue

            # Create Blender light object
            node_name = parsed_light.node_name or "light"
            light_name = f"{basename}_{node_name}_{light_count:03d}"
            try:
                light_obj = build_light(parsed_light, name=light_name)
            except Exception as e:
                _report(operator, 'WARNING',
                        f"Failed to create light '{light_name}': {e}")
                continue

            if light_obj is not None:
                collection.objects.link(light_obj)
                light_count += 1

    if light_count > 0:
        _report(operator, 'INFO',
                f"  Imported {light_count} lights from "
                f"{len(light_sets)} igLightSet nodes")

    return light_count


# ===========================================================================
# Collision hull import
# ===========================================================================

def _import_collision_hulls(reader, basename, context, operator):
    """Extract igCollideHull objects and create solid mesh in Colliders collection.

    Returns total number of collision triangles imported.
    """
    from ..igb_format.igb_objects import IGBObject, IGBMemoryBlock

    total_tris = 0
    hull_scan_count = 0

    for obj in reader.objects:
        if not isinstance(obj, IGBObject):
            continue
        hull_scan_count += 1
        if obj.type_name != b'igCollideHull':
            continue

        _report(operator, 'INFO',
                f"  Found igCollideHull (scanned {hull_scan_count} objects)")

        num_tris = obj.fields_by_slot.get(7, 0)
        if num_tris == 0:
            continue

        # Resolve triangle float data:
        # slot 5 -> igFloatList -> slot 4 -> MemoryBlock -> data
        tri_list_idx = obj.fields_by_slot.get(5)
        if tri_list_idx is None:
            continue
        tri_list = reader.objects[tri_list_idx]
        if not isinstance(tri_list, IGBObject):
            continue
        mem_ref = tri_list.fields_by_slot.get(4)
        if mem_ref is None:
            continue
        mem_block = reader.objects[mem_ref]
        if not isinstance(mem_block, IGBMemoryBlock):
            continue
        tri_data = mem_block.data

        if len(tri_data) < num_tris * 48:
            _report(operator, 'WARNING',
                    f"igCollideHull triangle data too short "
                    f"({len(tri_data)} < {num_tris * 48})")
            continue

        # Extract triangle vertices and per-triangle metadata (W components)
        # W layout (verified from game files):
        #   v0.w = 0 (always zero)
        #   v1.w = leaf_tag (BVH leaf group tag, 4*bfs_index+3)
        #   v2.w = surface_type (material enum: 0=default, 1=stone, etc.)
        verts = []
        faces = []
        surface_types = []
        for i in range(num_tris):
            base = i * 48  # 12 floats * 4 bytes
            v0 = struct.unpack_from('<fff', tri_data, base)
            v1 = struct.unpack_from('<fff', tri_data, base + 16)
            v2 = struct.unpack_from('<fff', tri_data, base + 32)
            # v1.w = leaf_tag (BVH internal, not stored on mesh)
            w2 = struct.unpack_from('<I', tri_data, base + 44)[0]  # surface type
            vi = len(verts)
            verts.extend([v0, v1, v2])
            faces.append((vi, vi + 1, vi + 2))
            surface_types.append(w2)

        # Create Blender mesh
        mesh_name = f"{basename}_collision"
        mesh = bpy.data.meshes.new(mesh_name)
        mesh.from_pydata(verts, [], faces)
        mesh.update()

        # Store per-face surface_type as custom int attribute.
        # Read back by the exporter to preserve collision metadata
        # on round-trip (different surface types control walkability, sliding, etc.)
        # Note: leaf_tag (V1.w) is NOT stored — it's regenerated by the BVH builder.
        if mesh.polygons:
            try:
                st_attr = mesh.attributes.new(
                    name="surface_type", type='INT', domain='FACE')
                for fi in range(len(mesh.polygons)):
                    st_attr.data[fi].value = surface_types[fi]
            except Exception as e:
                _report(operator, 'WARNING',
                        f"Could not store collision metadata: {e}")

        # Create object
        col_obj = bpy.data.objects.new(mesh_name, mesh)
        col_obj.display_type = 'SOLID'

        # Assign semi-transparent collision material
        col_mat = _get_collision_material()
        mesh.materials.append(col_mat)

        # Ensure "Colliders" collection exists
        if "Colliders" not in bpy.data.collections:
            coll = bpy.data.collections.new("Colliders")
            context.scene.collection.children.link(coll)
        else:
            coll = bpy.data.collections["Colliders"]
            # Ensure linked to scene
            if coll.name not in context.scene.collection.children:
                context.scene.collection.children.link(coll)

        # Link ONLY to Colliders collection
        coll.objects.link(col_obj)

        total_tris += num_tris
        _report(operator, 'INFO',
                f"  Imported collision hull: {num_tris} triangles")

    return total_tris


def _get_collision_material():
    """Get or create the shared semi-transparent collision material.

    Creates a green-tinted transparent material for visualizing
    collision blockout geometry in the viewport.
    """
    mat_name = "IGB_Collision"

    # Reuse existing material if already created
    if mat_name in bpy.data.materials:
        return bpy.data.materials[mat_name]

    mat = bpy.data.materials.new(name=mat_name)
    mat.use_nodes = True

    # Set up Principled BSDF with semi-transparent green
    nodes = mat.node_tree.nodes
    bsdf = nodes.get("Principled BSDF")
    if bsdf is not None:
        bsdf.inputs['Base Color'].default_value = (0.0, 0.8, 0.4, 1.0)
        bsdf.inputs['Alpha'].default_value = 0.3
        bsdf.inputs['Roughness'].default_value = 0.8

    # Enable transparency in material settings
    # Blender 4.2+ (EEVEE Next) replaced blend_method/shadow_method
    if hasattr(mat, 'surface_render_method'):
        mat.surface_render_method = 'BLENDED'
    elif hasattr(mat, 'blend_method'):
        mat.blend_method = 'BLEND'
    if hasattr(mat, 'shadow_method'):
        mat.shadow_method = 'NONE'
    mat.use_backface_culling = False

    # Viewport display color (matches shader)
    mat.diffuse_color = (0.0, 0.8, 0.4, 0.3)

    return mat


def _is_igz_file(filepath):
    """Check if a file is IGZ format by reading the magic bytes.

    IGZ files have magic b'\\x01ZGI' (little-endian) or b'IGZ\\x01' (big-endian)
    instead of IGB's b'\\x01BGI' / b'IGB\\x01'.
    This catches cases where an IGZ file has a .igb extension.
    """
    try:
        with open(filepath, 'rb') as f:
            magic = f.read(4)
            return magic in (b'\x01ZGI', b'IGZ\x01')
    except (OSError, IOError):
        return False


def _report(operator, level, message):
    """Report a message through the operator or print to console."""
    if operator is not None and hasattr(operator, 'report'):
        operator.report({level}, message)
    else:
        print(f"[{level}] {message}")
