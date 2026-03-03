"""PropertyGroups for the Objective Creation System."""

import bpy
from bpy.props import (
    StringProperty, IntProperty, FloatProperty, BoolProperty,
    EnumProperty, FloatVectorProperty, CollectionProperty,
)
from bpy.types import PropertyGroup


OBJECTIVE_STEP_TYPES = [
    ('GO_TO', "Go to Location", "Player must reach a location", 'TRACKER', 0),
    ('DESTROY', "Destroy Target", "Destroy specific entity/entities", 'TRASH', 1),
    ('COLLECT', "Collect Item", "Pick up N items of a type", 'PACKAGE', 2),
    ('INTERACT', "Interact", "Use/interact with an entity", 'HAND', 3),
    ('DEFEAT_ALL', "Defeat All Enemies", "Clear all spawned enemies", 'ARMATURE_DATA', 4),
    ('CUSTOM', "Custom Script", "Run arbitrary script code", 'SCRIPT', 5),
]


class MM_ObjectiveStep(PropertyGroup):
    """A single step/condition within an objective."""

    step_type: EnumProperty(
        name="Type",
        items=OBJECTIVE_STEP_TYPES,
        default='GO_TO',
    )

    # Reference to scene entity
    target_entity: StringProperty(
        name="Target Entity",
        description="Entity def name this step targets (e.g. zone_exit, boss_magneto)",
    )

    # Location reference
    target_location: FloatVectorProperty(
        name="Location",
        description="World position for GO_TO objectives",
        size=3, subtype='TRANSLATION',
    )
    use_scene_object: BoolProperty(
        name="Use Scene Object",
        description="Pick target location from a Blender object",
        default=True,
    )
    scene_object_name: StringProperty(
        name="Scene Object",
        description="Blender object whose location defines the target",
    )

    # Parameters
    count: IntProperty(
        name="Count",
        description="Number required (items to collect, enemies to defeat)",
        default=1, min=1,
    )
    radius: FloatProperty(
        name="Radius",
        description="Trigger radius for GO_TO objectives",
        default=60.0, min=1.0,
    )

    # Completion
    completion_script: StringProperty(
        name="On Complete",
        description="Script to run when this step completes",
    )

    # Custom script (for CUSTOM type)
    custom_check_script: StringProperty(
        name="Check Script",
        description="Script that returns True when this step is complete",
    )

    # Display
    description: StringProperty(
        name="Description",
        description="Objective text shown in HUD (e.g. 'Reach the extraction point')",
    )


class MM_Objective(PropertyGroup):
    """A complete objective (may have multiple steps)."""

    obj_name: StringProperty(
        name="Name",
        description="Internal name (used in script references)",
        default="objective_01",
    )
    display_text: StringProperty(
        name="Display Text",
        description="Text shown in the objective HUD",
        default="New Objective",
    )
    sort_order: IntProperty(
        name="Order",
        description="Display order (objectives complete in this sequence)",
        default=0, min=0,
    )
    is_optional: BoolProperty(
        name="Optional",
        description="If True, not required to proceed",
        default=False,
    )
    auto_activate: BoolProperty(
        name="Auto-Activate",
        description="Activate on zone load (vs. activated by script/trigger)",
        default=True,
    )
    trigger_entity: StringProperty(
        name="Activation Trigger",
        description="Entity that activates this objective (if not auto-activate)",
    )
    next_objective: StringProperty(
        name="Next Objective",
        description="Objective name to activate on completion (for chaining)",
    )

    # Steps
    steps: CollectionProperty(type=MM_ObjectiveStep)
    steps_index: IntProperty()


# ---------------------------------------------------------------------------
# Registration
# ---------------------------------------------------------------------------

_classes = (
    MM_ObjectiveStep,
    MM_Objective,
)


def register():
    for cls in _classes:
        bpy.utils.register_class(cls)

    bpy.types.Scene.mm_objectives = CollectionProperty(type=MM_Objective)
    bpy.types.Scene.mm_objectives_index = IntProperty(default=0)


def unregister():
    del bpy.types.Scene.mm_objectives_index
    del bpy.types.Scene.mm_objectives

    for cls in reversed(_classes):
        bpy.utils.unregister_class(cls)
