"""Operators for the Objective Creation System."""

import bpy
from bpy.props import StringProperty, IntProperty, EnumProperty
from bpy.types import Operator

from .objective_properties import OBJECTIVE_STEP_TYPES

# Collection name for objective marker empties
OBJECTIVE_COLLECTION_NAME = "[MapMaker] Objectives"


def _get_objective_collection():
    """Get or create the objectives collection."""
    if OBJECTIVE_COLLECTION_NAME in bpy.data.collections:
        col = bpy.data.collections[OBJECTIVE_COLLECTION_NAME]
    else:
        col = bpy.data.collections.new(OBJECTIVE_COLLECTION_NAME)
        bpy.context.scene.collection.children.link(col)
    return col


# ===========================================================================
# Objective CRUD
# ===========================================================================

class MM_OT_add_objective(Operator):
    """Add a new objective"""
    bl_idname = "mm.add_objective"
    bl_label = "Add Objective"
    bl_options = {'REGISTER', 'UNDO'}

    def execute(self, context):
        scene = context.scene
        obj = scene.mm_objectives.add()
        idx = len(scene.mm_objectives)
        obj.obj_name = f"objective_{idx:02d}"
        obj.display_text = f"Objective {idx}"
        obj.sort_order = idx - 1
        scene.mm_objectives_index = idx - 1
        return {'FINISHED'}


class MM_OT_remove_objective(Operator):
    """Remove selected objective"""
    bl_idname = "mm.remove_objective"
    bl_label = "Remove Objective"
    bl_options = {'REGISTER', 'UNDO'}

    @classmethod
    def poll(cls, context):
        scene = context.scene
        return (len(scene.mm_objectives) > 0 and
                0 <= scene.mm_objectives_index < len(scene.mm_objectives))

    def execute(self, context):
        scene = context.scene
        scene.mm_objectives.remove(scene.mm_objectives_index)
        scene.mm_objectives_index = min(
            scene.mm_objectives_index, len(scene.mm_objectives) - 1)
        return {'FINISHED'}


class MM_OT_move_objective(Operator):
    """Move objective up or down in the list"""
    bl_idname = "mm.move_objective"
    bl_label = "Move Objective"
    bl_options = {'REGISTER', 'UNDO'}

    direction: EnumProperty(
        items=[('UP', "Up", ""), ('DOWN', "Down", "")],
    )

    @classmethod
    def poll(cls, context):
        scene = context.scene
        return len(scene.mm_objectives) > 1

    def execute(self, context):
        scene = context.scene
        idx = scene.mm_objectives_index
        if self.direction == 'UP' and idx > 0:
            scene.mm_objectives.move(idx, idx - 1)
            scene.mm_objectives_index = idx - 1
        elif self.direction == 'DOWN' and idx < len(scene.mm_objectives) - 1:
            scene.mm_objectives.move(idx, idx + 1)
            scene.mm_objectives_index = idx + 1
        return {'FINISHED'}


# ===========================================================================
# Objective Step CRUD
# ===========================================================================

class MM_OT_add_objective_step(Operator):
    """Add a step to the selected objective"""
    bl_idname = "mm.add_objective_step"
    bl_label = "Add Step"
    bl_options = {'REGISTER', 'UNDO'}

    step_type: EnumProperty(
        name="Step Type",
        items=OBJECTIVE_STEP_TYPES,
        default='GO_TO',
    )

    @classmethod
    def poll(cls, context):
        scene = context.scene
        return 0 <= scene.mm_objectives_index < len(scene.mm_objectives)

    def execute(self, context):
        scene = context.scene
        objective = scene.mm_objectives[scene.mm_objectives_index]
        step = objective.steps.add()
        step.step_type = self.step_type
        objective.steps_index = len(objective.steps) - 1
        return {'FINISHED'}


class MM_OT_remove_objective_step(Operator):
    """Remove selected step from the objective"""
    bl_idname = "mm.remove_objective_step"
    bl_label = "Remove Step"
    bl_options = {'REGISTER', 'UNDO'}

    @classmethod
    def poll(cls, context):
        scene = context.scene
        if not (0 <= scene.mm_objectives_index < len(scene.mm_objectives)):
            return False
        obj = scene.mm_objectives[scene.mm_objectives_index]
        return len(obj.steps) > 0 and 0 <= obj.steps_index < len(obj.steps)

    def execute(self, context):
        scene = context.scene
        objective = scene.mm_objectives[scene.mm_objectives_index]
        objective.steps.remove(objective.steps_index)
        objective.steps_index = min(
            objective.steps_index, len(objective.steps) - 1)
        return {'FINISHED'}


# ===========================================================================
# Objective Marker Placement
# ===========================================================================

class MM_OT_place_objective_marker(Operator):
    """Place an objective target marker at the 3D cursor"""
    bl_idname = "mm.place_objective_marker"
    bl_label = "Place Marker"
    bl_options = {'REGISTER', 'UNDO'}

    @classmethod
    def poll(cls, context):
        scene = context.scene
        if not (0 <= scene.mm_objectives_index < len(scene.mm_objectives)):
            return False
        obj = scene.mm_objectives[scene.mm_objectives_index]
        return len(obj.steps) > 0 and 0 <= obj.steps_index < len(obj.steps)

    def execute(self, context):
        scene = context.scene
        objective = scene.mm_objectives[scene.mm_objectives_index]
        step = objective.steps[objective.steps_index]

        col = _get_objective_collection()

        marker_name = f"objm_{objective.obj_name}_{objective.steps_index:02d}"

        empty = bpy.data.objects.new(marker_name, None)
        empty.empty_display_type = 'SPHERE'
        empty.empty_display_size = 50.0
        empty.location = context.scene.cursor.location.copy()
        empty.color = (1.0, 0.8, 0.0, 1.0)  # Gold
        empty["mm_type"] = "objective_marker"
        empty["mm_objective"] = objective.obj_name
        col.objects.link(empty)

        # Link the step to this marker
        step.use_scene_object = True
        step.scene_object_name = marker_name

        # Select the marker
        bpy.ops.object.select_all(action='DESELECT')
        empty.select_set(True)
        context.view_layer.objects.active = empty

        self.report({'INFO'}, f"Placed objective marker '{marker_name}'")
        return {'FINISHED'}


class MM_OT_select_step_target(Operator):
    """Select the scene object referenced by the current step"""
    bl_idname = "mm.select_step_target"
    bl_label = "Select Target"

    @classmethod
    def poll(cls, context):
        scene = context.scene
        if not (0 <= scene.mm_objectives_index < len(scene.mm_objectives)):
            return False
        obj = scene.mm_objectives[scene.mm_objectives_index]
        if not (0 <= obj.steps_index < len(obj.steps)):
            return False
        step = obj.steps[obj.steps_index]
        return bool(step.scene_object_name)

    def execute(self, context):
        scene = context.scene
        objective = scene.mm_objectives[scene.mm_objectives_index]
        step = objective.steps[objective.steps_index]

        target = bpy.data.objects.get(step.scene_object_name)
        if not target:
            self.report({'WARNING'}, f"Object '{step.scene_object_name}' not found")
            return {'CANCELLED'}

        bpy.ops.object.select_all(action='DESELECT')
        target.select_set(True)
        context.view_layer.objects.active = target
        return {'FINISHED'}


# ===========================================================================
# Registration
# ===========================================================================

_classes = (
    MM_OT_add_objective,
    MM_OT_remove_objective,
    MM_OT_move_objective,
    MM_OT_add_objective_step,
    MM_OT_remove_objective_step,
    MM_OT_place_objective_marker,
    MM_OT_select_step_target,
)


def register():
    for cls in _classes:
        bpy.utils.register_class(cls)


def unregister():
    for cls in reversed(_classes):
        bpy.utils.unregister_class(cls)
