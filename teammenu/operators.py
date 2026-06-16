"""Blender operators + properties for the MUA Team Menu editor.

Import builds a movable Empty per pad/actor/camera slot at its in-game
position (labelled with the character assigned via herostat menulocation).
Export reads the Empties back, patches the transform matrices in
mlm_team_back.igb in place, and writes menulocation changes to herostat.xmlb.
"""

import os
import math
import bpy
from bpy.props import (StringProperty, IntProperty, CollectionProperty,
                       PointerProperty)
from bpy.types import Operator, PropertyGroup
from bpy_extras.io_utils import ImportHelper
from mathutils import Matrix

from . import team_menu


_COLLECTION = "IGB Team Menu"
_BACKDROP_COLLECTION = "IGB Team Menu Backdrop"


# ── Alchemy row-major Matrix44f <-> Blender matrix ──────────────────────

def _alchemy_to_blender(m16):
    """16-float row-major (translation in last row) -> Blender Matrix."""
    rows = [m16[0:4], m16[4:8], m16[8:12], m16[12:16]]
    return Matrix(rows).transposed()


def _blender_to_alchemy(mat):
    """Blender Matrix -> 16-float row-major (translation in last row)."""
    t = mat.transposed()
    out = []
    for r in range(4):
        out.extend(t[r][:])
    return out


# ── Properties ──────────────────────────────────────────────────────────

class TeamMenuCharItem(PropertyGroup):
    name: StringProperty(name="Name")
    charactername: StringProperty(name="Internal")
    skin: StringProperty(name="Skin")
    menulocation: IntProperty(name="Pad", min=0, max=63,
                              description="Which pad (standing position) this "
                                          "character uses. 0 = not shown")


class IGBTeamMenuProps(PropertyGroup):
    igb_path: StringProperty(name="Team Menu IGB", subtype='FILE_PATH')
    herostat_path: StringProperty(name="Herostat", subtype='FILE_PATH')
    chars: CollectionProperty(type=TeamMenuCharItem)
    chars_index: IntProperty(default=0)


# ── Import ────────────────────────────────────────────────────────────────

class TEAMMENU_OT_import(Operator, ImportHelper):
    """Import the MUA team-menu lineup (mlm_team_back.igb) for editing"""
    bl_idname = "igb.teammenu_import"
    bl_label = "Import Team Menu"
    bl_options = {'REGISTER', 'UNDO'}

    filename_ext = ".igb"
    filter_glob: StringProperty(default="*.igb", options={'HIDDEN'})

    import_backdrop: bpy.props.BoolProperty(
        name="Load Backdrop Model",
        description="Also import the team-menu stage geometry "
                    "(ui/models/m_team_stage.igb) as a reference so you can see "
                    "where the pads/cameras sit in the environment. It's "
                    "parented to a 'Backdrop Control' empty you can scale/move "
                    "if the in-game composition is at a different scale",
        default=True,
    )

    def draw(self, context):
        self.layout.prop(self, "import_backdrop")

    def _load_backdrop(self, context, igb_path):
        """Import the stage geometry as a locked, reference-only sub-collection."""
        from ..importer.import_igb import import_igb
        backdrop = team_menu.default_backdrop_for(igb_path)
        if not backdrop or not os.path.exists(backdrop):
            return 0
        # Remove a previous backdrop import
        old = bpy.data.collections.get(_BACKDROP_COLLECTION)
        if old is not None:
            for o in list(old.objects):
                bpy.data.objects.remove(o, do_unlink=True)
            bpy.data.collections.remove(old)
        before = set(bpy.data.collections.keys())
        try:
            import_igb(context, backdrop, operator=None)
        except Exception as e:
            self.report({'WARNING'}, f"Backdrop import failed: {e}")
            return 0
        new = [bpy.data.collections[k] for k in bpy.data.collections.keys()
               if k not in before]
        n = 0
        bd_coll = None
        for coll in new:
            coll.name = _BACKDROP_COLLECTION
            bd_coll = coll
            for obj in coll.objects:
                n += 1
        if bd_coll is None:
            return 0
        # Parent the whole backdrop to one selectable control empty at the
        # origin. The meshes stay hide_select (so editing pads never grabs the
        # set), but the user can select "Backdrop Control" and scale/move it
        # as a unit — the team-menu stage and the pad layout import at the same
        # raw IGB scale, but the engine may composite the stage at a different
        # scale, so this lets the user match what they see in-game.
        ctrl = bpy.data.objects.new("Backdrop Control", None)
        ctrl.empty_display_type = 'SPHERE'
        ctrl.empty_display_size = 30.0
        ctrl.show_name = True
        ctrl["igb_tm_backdrop_control"] = True
        bd_coll.objects.link(ctrl)
        for obj in list(bd_coll.objects):
            if obj is ctrl:
                continue
            obj.parent = ctrl
            obj.matrix_parent_inverse = ctrl.matrix_world.inverted()
            obj.hide_select = True   # only the control is grabbable
        return n

    def execute(self, context):
        props = context.scene.igb_teammenu
        igb_path = self.filepath
        if not os.path.exists(igb_path):
            self.report({'ERROR'}, "IGB not found")
            return {'CANCELLED'}

        herostat = team_menu.default_herostat_for(igb_path)
        props.igb_path = igb_path
        props.herostat_path = herostat

        # Read transforms + (optional) herostat menulocation map
        transforms = team_menu.read_team_transforms(igb_path)
        if not transforms:
            self.report({'ERROR'},
                        "No pad/actor/camera transforms found — is this "
                        "mlm_team_back.igb?")
            return {'CANCELLED'}

        pad_to_char = {}
        props.chars.clear()
        if herostat and os.path.exists(herostat):
            try:
                chars = team_menu.read_menulocations(herostat)
            except Exception as e:
                chars = []
                self.report({'WARNING'}, f"Could not read herostat: {e}")
            for c in chars:
                item = props.chars.add()
                item.name = c['name']
                item.charactername = c['charactername']
                item.skin = c['skin']
                item.menulocation = c['menulocation']
                if c['menulocation'] > 0:
                    pad_to_char.setdefault(c['menulocation'], c['name'])

        # Fresh collection
        coll = bpy.data.collections.get(_COLLECTION)
        if coll is None:
            coll = bpy.data.collections.new(_COLLECTION)
            context.scene.collection.children.link(coll)
        else:
            for obj in list(coll.objects):
                bpy.data.objects.remove(obj, do_unlink=True)

        created = 0
        for name, info in transforms.items():
            cat = info['category']
            if cat == 'camera':
                # Real Camera object — the game camera convention matches
                # Blender's (looks down local -Z, +Y up), so the frustum points
                # the right way and the user can look through it to aim. (A CONE
                # empty only drew an axis, which looked like it faced up.)
                cam_data = bpy.data.cameras.new(name)
                cam_data.lens_unit = 'FOV'
                cam_data.angle = math.radians(60.0)  # igCamera FOV
                cam_data.display_size = 10.0
                obj = bpy.data.objects.new(name, cam_data)
            else:
                obj = bpy.data.objects.new(name, None)
                obj.empty_display_type = 'ARROWS'
                obj.empty_display_size = 8.0 if cat == 'actor' else 6.0
            obj.matrix_world = _alchemy_to_blender(info['matrix'])
            obj["igb_tm_node"] = name
            obj["igb_tm_category"] = cat
            if info['index'] is not None:
                obj["igb_tm_index"] = info['index']
            # Label pads with the assigned character
            if cat == 'pad' and info['index'] in pad_to_char:
                obj["igb_tm_character"] = pad_to_char[info['index']]
                obj.show_name = True
                obj.name = f"{name}  [{pad_to_char[info['index']]}]"
            else:
                obj.show_name = (cat in ('pad', 'actor', 'camera'))
            coll.objects.link(obj)
            created += 1

        backdrop_n = 0
        if self.import_backdrop:
            backdrop_n = self._load_backdrop(context, igb_path)

        bmsg = f", backdrop {backdrop_n} meshes" if backdrop_n else ""
        self.report({'INFO'},
                    f"Team menu: {created} slots imported "
                    f"({sum(1 for i in transforms.values() if i['category']=='pad')} pads){bmsg}. "
                    f"Move the empties; edit menulocation; then Build IGB.")
        return {'FINISHED'}


# ── Export ────────────────────────────────────────────────────────────────

class TEAMMENU_OT_export(Operator):
    """Write edited pad positions back to the IGB and menulocation to herostat"""
    bl_idname = "igb.teammenu_export"
    bl_label = "Export Team Menu"
    bl_options = {'REGISTER'}

    def execute(self, context):
        props = context.scene.igb_teammenu
        igb_path = props.igb_path
        if not igb_path or not os.path.exists(igb_path):
            self.report({'ERROR'}, "No imported team-menu IGB — Import first")
            return {'CANCELLED'}

        coll = bpy.data.collections.get(_COLLECTION)
        if coll is None:
            self.report({'ERROR'}, "Team Menu collection not found")
            return {'CANCELLED'}

        # Collect updated matrices from the empties
        updates = {}
        for obj in coll.objects:
            node = obj.get("igb_tm_node")
            if not node:
                continue
            updates[node] = _blender_to_alchemy(obj.matrix_world)

        # Backup + patch positions in place
        if not os.path.exists(igb_path + ".bak"):
            try:
                import shutil
                shutil.copy2(igb_path, igb_path + ".bak")
            except Exception:
                pass
        patched, skipped = team_menu.patch_team_transforms(
            igb_path, igb_path, updates)

        # Write menulocation changes to herostat
        hs_msg = ""
        herostat = props.herostat_path
        if herostat and os.path.exists(herostat) and len(props.chars):
            ml_updates = {c.name: c.menulocation for c in props.chars if c.name}
            if not os.path.exists(herostat + ".bak"):
                try:
                    import shutil
                    shutil.copy2(herostat, herostat + ".bak")
                except Exception:
                    pass
            try:
                n = team_menu.write_menulocations(herostat, herostat, ml_updates)
                hs_msg = f", {n} menulocations -> herostat"
            except Exception as e:
                self.report({'WARNING'}, f"herostat write failed: {e}")

        self.report({'INFO'},
                    f"Exported {patched} slot positions to "
                    f"{os.path.basename(igb_path)}{hs_msg} (.bak saved)")
        return {'FINISHED'}


_CLASSES = (TeamMenuCharItem, IGBTeamMenuProps,
            TEAMMENU_OT_import, TEAMMENU_OT_export)


def register():
    for c in _CLASSES:
        bpy.utils.register_class(c)
    bpy.types.Scene.igb_teammenu = PointerProperty(type=IGBTeamMenuProps)


def unregister():
    del bpy.types.Scene.igb_teammenu
    for c in reversed(_CLASSES):
        try:
            bpy.utils.unregister_class(c)
        except RuntimeError:
            pass
