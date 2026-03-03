"""Entity and objective validation for pre-build checks.

Runs automatically before Build All and displays warnings in the UI.
"""

import bpy


def _find_property_value(edef, key):
    """Find a custom property value on an entity def by key."""
    for prop in edef.properties:
        if prop.key == key:
            return prop.value
    return ""


def validate_all(scene):
    """Run all validation checks. Returns list of (level, message).

    Levels: 'ERROR', 'WARNING', 'INFO'
    """
    issues = []
    issues.extend(_validate_settings(scene))
    issues.extend(_validate_entity_defs(scene))
    issues.extend(_validate_instances(scene))
    issues.extend(_validate_objectives(scene))
    return issues


def _validate_settings(scene):
    """Validate map settings."""
    issues = []
    settings = scene.mm_settings

    if not settings.map_name:
        issues.append(('ERROR', "Map name is empty"))
    if not settings.map_path:
        issues.append(('ERROR', "Map path is empty"))
    if not settings.output_dir:
        issues.append(('ERROR', "Output directory not set"))

    return issues


def _validate_entity_defs(scene):
    """Validate entity definitions."""
    issues = []

    # Check for duplicate entity names
    names = {}
    for edef in scene.mm_entity_defs:
        if edef.entity_name in names:
            issues.append(('ERROR',
                f"Duplicate entity name: '{edef.entity_name}'"))
        names[edef.entity_name] = edef

    # Per-classname validation
    for edef in scene.mm_entity_defs:
        if edef.classname == 'monsterspawnerent':
            if not edef.character:
                issues.append(('WARNING',
                    f"'{edef.entity_name}': Monster spawner has no character assigned"))
        elif edef.classname == 'zonelinkent':
            destzone = _find_property_value(edef, 'destzone')
            if not destzone:
                issues.append(('WARNING',
                    f"'{edef.entity_name}': Zone link has no destination zone"))
        elif edef.classname in ('physent', 'doorent', 'gameent', 'actionent'):
            # These typically need models but it's only a warning
            if not edef.model:
                has_model_prop = any(p.key == 'model' for p in edef.properties)
                if not has_model_prop:
                    issues.append(('INFO',
                        f"'{edef.entity_name}': No model assigned (may be intentional for triggers)"))

    return issues


def _validate_instances(scene):
    """Validate entity instances in the scene."""
    issues = []

    # Check that there's at least one player start
    has_player_start = False
    entity_col = bpy.data.collections.get("[MapMaker] Entities")

    if entity_col:
        for obj in entity_col.objects:
            if obj.get("mm_preview"):
                continue
            etype = obj.get("mm_entity_type", "")
            if not etype:
                continue
            # Find the entity def
            for edef in scene.mm_entity_defs:
                if edef.entity_name == etype and edef.classname == 'playerstartent':
                    has_player_start = True
                    break

        # Check for orphaned instances (entity type not in defs)
        known_names = {edef.entity_name for edef in scene.mm_entity_defs}
        for obj in entity_col.objects:
            if obj.get("mm_preview"):
                continue
            etype = obj.get("mm_entity_type", "")
            if etype and etype not in known_names:
                issues.append(('WARNING',
                    f"Instance '{obj.name}' references unknown entity def '{etype}'"))
    else:
        issues.append(('INFO', "No entity instances placed yet"))

    if not has_player_start:
        issues.append(('WARNING', "No player start entity placed"))

    return issues


def _validate_objectives(scene):
    """Validate objectives."""
    issues = []

    if not hasattr(scene, 'mm_objectives'):
        return issues

    # Check for duplicate objective names
    names = set()
    for obj in scene.mm_objectives:
        if obj.obj_name in names:
            issues.append(('ERROR',
                f"Duplicate objective name: '{obj.obj_name}'"))
        names.add(obj.obj_name)

    # Check objective chaining
    for obj in scene.mm_objectives:
        if obj.next_objective and obj.next_objective not in names:
            issues.append(('WARNING',
                f"Objective '{obj.obj_name}': next_objective '{obj.next_objective}' not found"))

        # Check steps have required data
        for i, step in enumerate(obj.steps):
            if step.step_type == 'GO_TO':
                if step.use_scene_object and step.scene_object_name:
                    target = bpy.data.objects.get(step.scene_object_name)
                    if not target:
                        issues.append(('WARNING',
                            f"Objective '{obj.obj_name}' step {i+1}: "
                            f"scene object '{step.scene_object_name}' not found"))
            elif step.step_type in ('DESTROY', 'INTERACT', 'DEFEAT_ALL'):
                if not step.target_entity:
                    issues.append(('WARNING',
                        f"Objective '{obj.obj_name}' step {i+1}: no target entity set"))
            elif step.step_type == 'CUSTOM':
                if not step.custom_check_script:
                    issues.append(('WARNING',
                        f"Objective '{obj.obj_name}' step {i+1}: no check script set"))

        if len(obj.steps) == 0:
            issues.append(('INFO',
                f"Objective '{obj.obj_name}' has no steps"))

    return issues
