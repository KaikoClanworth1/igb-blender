bl_info = {
    "name": "IGB Format (Alchemy Engine)",
    "author": "Kaiko",
    "version": (0, 2, 0),
    "blender": (4, 4, 0),
    "location": "File > Import/Export, 3D Viewport > Sidebar > IGB",
    "description": "Import/Export Alchemy Engine IGB/IGZ files for X-Men Legends, XML2, MUA. By Kaiko.",
    "category": "Import-Export",
}

import bpy
from bpy.props import StringProperty, BoolProperty, EnumProperty, FloatProperty, IntProperty
from bpy_extras.io_utils import ImportHelper, ExportHelper


def _game_preset_items(self, context):
    """Dynamic enum items for the game preset dropdown (import)."""
    from .game_profiles import get_profile_items
    return get_profile_items()


def _export_game_items(self, context):
    """Enum items for export game preset (no Auto-Detect)."""
    from .game_profiles import get_profile_items
    items = get_profile_items()
    # Filter out Auto-Detect for export
    return [(id, name, desc) for id, name, desc in items if id != 'auto']


def _export_texture_format_items(self, context):
    """Enum items for export texture format."""
    return [
        ('clut', "CLUT (Universal)",
         "256-color palette texture. Works in both XML2 and MUA (recommended)"),
        ('dxt5_xml2', "DXT5 (XML2 Only)",
         "DXT5 compressed for X-Men Legends 2 (standard RGB565)"),
        ('dxt5_mua', "DXT5 (MUA Only)",
         "DXT5 compressed for Marvel Ultimate Alliance (BGR565)"),
    ]


class ImportIGB(bpy.types.Operator, ImportHelper):
    """Import an Alchemy Engine IGB file"""
    bl_idname = "import_scene.igb"
    bl_label = "Import IGB"
    bl_options = {'REGISTER', 'UNDO'}

    filename_ext = ".igb"

    filter_glob: StringProperty(
        default="*.igb;*.igz",
        options={'HIDDEN'},
    )

    game_preset: EnumProperty(
        name="Game Preset",
        description="Select the game this IGB file is from (affects format interpretation)",
        items=_game_preset_items,
    )

    import_normals: BoolProperty(
        name="Import Normals",
        description="Import vertex normals from the IGB file",
        default=True,
    )

    import_uvs: BoolProperty(
        name="Import UVs",
        description="Import texture coordinates",
        default=True,
    )

    import_vertex_colors: BoolProperty(
        name="Import Vertex Colors",
        description="Import vertex color data",
        default=True,
    )

    import_materials: BoolProperty(
        name="Import Materials",
        description="Import materials and textures",
        default=True,
    )

    import_collision: BoolProperty(
        name="Import Collision",
        description="Import collision hull as solid mesh in 'Colliders' collection",
        default=True,
    )

    import_lights: BoolProperty(
        name="Import Lights",
        description="Import scene lights from igLightSet nodes",
        default=True,
    )

    igz_texture_dir: StringProperty(
        name="Texture Directory",
        description="(IGZ only) Path to the folder containing materials/ and models/ subfolders",
        subtype='DIR_PATH',
        default="",
    )

    import_entity_models: BoolProperty(
        name="Import Entity Models",
        description="(IGZ only) Import placed props/objects from companion .mua entity files",
        default=True,
    )

    def execute(self, context):
        from .importer.import_igb import import_igb
        return import_igb(context, self.filepath, self)

    def draw(self, context):
        layout = self.layout
        layout.prop(self, "game_preset")
        layout.separator()
        layout.prop(self, "import_normals")
        layout.prop(self, "import_uvs")
        layout.prop(self, "import_vertex_colors")
        layout.prop(self, "import_materials")
        layout.separator()
        layout.prop(self, "import_collision")
        layout.prop(self, "import_lights")

        # Show IGZ options when importing .igz
        if self.filepath.lower().endswith('.igz'):
            layout.prop(self, "import_entity_models")
            if self.import_materials:
                layout.separator()
                box = layout.box()
                box.label(text="IGZ Data Directory", icon='TEXTURE')
                box.prop(self, "igz_texture_dir")


class ImportIGZ(bpy.types.Operator, ImportHelper):
    """Import an Alchemy Engine IGZ file"""
    bl_idname = "import_scene.igz"
    bl_label = "Import IGZ"
    bl_options = {'REGISTER', 'UNDO'}

    filename_ext = ".igz"

    filter_glob: StringProperty(
        default="*.igz",
        options={'HIDDEN'},
    )

    game_preset: EnumProperty(
        name="Game Preset",
        description="Select the game this IGZ file is from (affects format interpretation)",
        items=_game_preset_items,
    )

    import_normals: BoolProperty(
        name="Import Normals",
        description="Import vertex normals from the IGZ file",
        default=True,
    )

    import_uvs: BoolProperty(
        name="Import UVs",
        description="Import texture coordinates",
        default=True,
    )

    import_vertex_colors: BoolProperty(
        name="Import Vertex Colors",
        description="Import vertex color data",
        default=True,
    )

    import_materials: BoolProperty(
        name="Import Materials",
        description="Import materials and textures",
        default=True,
    )

    import_collision: BoolProperty(
        name="Import Collision",
        description="Import collision hull as solid mesh in 'Colliders' collection",
        default=True,
    )

    import_lights: BoolProperty(
        name="Import Lights",
        description="Import scene lights from igLightSet nodes",
        default=True,
    )

    igz_texture_dir: StringProperty(
        name="Texture Directory",
        description="Path to the folder containing materials/ and models/ subfolders",
        subtype='DIR_PATH',
        default="",
    )

    import_entity_models: BoolProperty(
        name="Import Entity Models",
        description="Import placed props/objects from companion .mua entity files (models/ directory)",
        default=True,
    )

    def execute(self, context):
        from .importer.import_igb import import_igb
        return import_igb(context, self.filepath, self)

    def draw(self, context):
        layout = self.layout
        layout.prop(self, "game_preset")
        layout.separator()
        layout.prop(self, "import_normals")
        layout.prop(self, "import_uvs")
        layout.prop(self, "import_vertex_colors")
        layout.prop(self, "import_materials")
        layout.separator()
        layout.prop(self, "import_collision")
        layout.prop(self, "import_lights")
        layout.prop(self, "import_entity_models")
        layout.separator()
        box = layout.box()
        box.label(text="IGZ Data Directory", icon='TEXTURE')
        box.prop(self, "igz_texture_dir")


class ExportIGB(bpy.types.Operator, ExportHelper):
    """Export to Alchemy Engine IGB format"""
    bl_idname = "export_scene.igb"
    bl_label = "Export IGB"
    bl_options = {'REGISTER', 'UNDO'}

    filename_ext = ".igb"

    filter_glob: StringProperty(
        default="*.igb",
        options={'HIDDEN'},
    )

    texture_format: EnumProperty(
        name="Texture Format",
        description="Texture encoding format for game compatibility",
        items=_export_texture_format_items,
    )

    collision_source: EnumProperty(
        name="Collision Source",
        description="Source geometry for collision hull export",
        items=[
            ('COLLIDERS', "Colliders Collection",
             "Use objects from the 'Colliders' collection"),
            ('VISUAL', "Visual Mesh",
             "Auto-generate collision from the visible mesh geometry"),
            ('NONE', "None",
             "Do not export collision data"),
        ],
        default='COLLIDERS',
    )

    surface_type: IntProperty(
        name="Surface Type",
        description="Surface type for collision (0=default, 1=stone, 12=wood). "
                    "Only used for faces without a custom 'surface_type' attribute",
        default=0,
        min=0,
    )

    export_lights: BoolProperty(
        name="Export Lights",
        description="Export scene lights as igLightSet objects",
        default=True,
    )

    def execute(self, context):
        from .exporter.export_igb import export_igb
        return export_igb(context, self.filepath, self)

    def draw(self, context):
        layout = self.layout
        layout.prop(self, "texture_format")
        layout.separator()
        layout.prop(self, "collision_source")
        if self.collision_source != 'NONE':
            layout.prop(self, "surface_type")
        layout.separator()
        layout.prop(self, "export_lights")
        layout.separator()
        layout.label(text="Exports all scene meshes with materials & textures")


def menu_func_import(self, context):
    self.layout.operator(ImportIGB.bl_idname, text="Alchemy IGB (.igb)")


def menu_func_import_igz(self, context):
    self.layout.operator(ImportIGZ.bl_idname, text="Alchemy IGZ (.igz)")


def menu_func_export(self, context):
    self.layout.operator(ExportIGB.bl_idname, text="Alchemy IGB (.igb)")


class ImportZAM(bpy.types.Operator, ImportHelper):
    """Import a ZAM minimap file"""
    bl_idname = "import_mesh.zam"
    bl_label = "Import ZAM"
    bl_options = {'PRESET', 'UNDO'}

    filename_ext = ".zam"

    filter_glob: StringProperty(
        default="*.zam",
        options={'HIDDEN'},
    )

    scale: FloatProperty(
        name="Scale",
        description="Scale factor for coordinates",
        default=0.01, min=0.001, max=10.0,
    )

    def execute(self, context):
        import os
        from .mapmaker.zam_io import parse_zam, create_mesh_from_zam

        zam_data = parse_zam(self.filepath)
        name = os.path.splitext(os.path.basename(self.filepath))[0]
        obj, tri_count = create_mesh_from_zam(name, zam_data, self.scale)
        context.collection.objects.link(obj)

        bpy.ops.object.select_all(action='DESELECT')
        obj.select_set(True)
        context.view_layer.objects.active = obj

        self.report({'INFO'},
                    f"Imported {tri_count} triangles from "
                    f"{len(zam_data['polygons'])} polygons ({name})")
        return {'FINISHED'}


class ExportZAM(bpy.types.Operator, ExportHelper):
    """Export to ZAM minimap format"""
    bl_idname = "export_mesh.zam"
    bl_label = "Export ZAM"
    bl_options = {'PRESET'}

    filename_ext = ".zam"

    filter_glob: StringProperty(
        default="*.zam",
        options={'HIDDEN'},
    )

    scale: FloatProperty(
        name="Scale",
        description="Scale factor for coordinates",
        default=100.0, min=0.1, max=1000.0,
    )

    @classmethod
    def poll(cls, context):
        return context.active_object is not None and context.active_object.type == 'MESH'

    def execute(self, context):
        from .mapmaker.zam_io import write_zam

        obj = context.active_object
        poly_count = write_zam(self.filepath, obj, self.scale)
        self.report({'INFO'},
                    f"Exported {poly_count} polygons to {self.filepath}")
        return {'FINISHED'}


def menu_func_import_zam(self, context):
    self.layout.operator(ImportZAM.bl_idname, text="ZAM Minimap (.zam)")


def menu_func_export_zam(self, context):
    self.layout.operator(ExportZAM.bl_idname, text="ZAM Minimap (.zam)")


def register():
    bpy.utils.register_class(ImportIGB)
    bpy.utils.register_class(ImportIGZ)
    bpy.utils.register_class(ExportIGB)
    bpy.utils.register_class(ImportZAM)
    bpy.utils.register_class(ExportZAM)
    bpy.types.TOPBAR_MT_file_import.append(menu_func_import)
    bpy.types.TOPBAR_MT_file_import.append(menu_func_import_igz)
    bpy.types.TOPBAR_MT_file_import.append(menu_func_import_zam)
    bpy.types.TOPBAR_MT_file_export.append(menu_func_export)
    bpy.types.TOPBAR_MT_file_export.append(menu_func_export_zam)
    from . import panels
    panels.register()
    from . import mapmaker
    mapmaker.register()
    from . import actor
    actor.register()


def unregister():
    from . import actor
    actor.unregister()
    from . import mapmaker
    mapmaker.unregister()
    from . import panels
    panels.unregister()
    bpy.types.TOPBAR_MT_file_export.remove(menu_func_export_zam)
    bpy.types.TOPBAR_MT_file_export.remove(menu_func_export)
    bpy.types.TOPBAR_MT_file_import.remove(menu_func_import_zam)
    bpy.types.TOPBAR_MT_file_import.remove(menu_func_import_igz)
    bpy.types.TOPBAR_MT_file_import.remove(menu_func_import)
    bpy.utils.unregister_class(ExportZAM)
    bpy.utils.unregister_class(ImportZAM)
    bpy.utils.unregister_class(ExportIGB)
    bpy.utils.unregister_class(ImportIGZ)
    bpy.utils.unregister_class(ImportIGB)


if __name__ == "__main__":
    register()
