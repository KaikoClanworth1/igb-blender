"""Menu validation rules and auto-fix navigation.

Checks for common issues that would break menus in-game:
duplicate names, broken navigation links, missing transforms, etc.
"""

import bpy
from pathlib import Path


# ---------------------------------------------------------------------------
# Validation result
# ---------------------------------------------------------------------------

class ValidationResult:
    """Single validation check result."""
    __slots__ = ('rule', 'level', 'message', 'item_names')

    PASS = 'pass'
    WARN = 'warn'
    ERROR = 'error'

    def __init__(self, rule, level, message, item_names=None):
        self.rule = rule
        self.level = level
        self.message = message
        self.item_names = item_names or []


# ---------------------------------------------------------------------------
# Individual validation rules
# ---------------------------------------------------------------------------

def _check_duplicate_names(scene):
    """Check for duplicate item names."""
    seen = {}
    for pg in scene.menu_items:
        name = pg.item_name
        if name in seen:
            seen[name].append(pg)
        else:
            seen[name] = [pg]

    dupes = {n: items for n, items in seen.items() if len(items) > 1}
    if not dupes:
        return ValidationResult(
            'duplicate_names', ValidationResult.PASS,
            "No duplicate item names")

    names = list(dupes.keys())
    return ValidationResult(
        'duplicate_names', ValidationResult.ERROR,
        f"{len(names)} duplicate name(s): {', '.join(names[:5])}"
        + ("..." if len(names) > 5 else ""),
        names)


def _check_empty_names(scene):
    """Check for items without names."""
    empty = [pg for pg in scene.menu_items if not pg.item_name.strip()]
    if not empty:
        return ValidationResult(
            'empty_names', ValidationResult.PASS,
            "All items have names")
    return ValidationResult(
        'empty_names', ValidationResult.ERROR,
        f"{len(empty)} item(s) without a name")


def _check_object_links(scene):
    """Check that all items have valid Blender object links."""
    broken = []
    for pg in scene.menu_items:
        if not pg.object_name or pg.object_name not in bpy.data.objects:
            broken.append(pg.item_name)
    if not broken:
        return ValidationResult(
            'object_links', ValidationResult.PASS,
            "All items linked to objects")
    return ValidationResult(
        'object_links', ValidationResult.ERROR,
        f"{len(broken)} unlinked item(s): {', '.join(broken[:5])}"
        + ("..." if len(broken) > 5 else ""),
        broken)


def _check_nav_targets(scene):
    """Check that all navigation targets reference existing items."""
    name_set = {pg.item_name for pg in scene.menu_items}
    broken = []
    for pg in scene.menu_items:
        for direction, target in [('up', pg.nav_up), ('down', pg.nav_down),
                                  ('left', pg.nav_left), ('right', pg.nav_right)]:
            target = target.strip()
            if target and target != ' ' and target not in name_set:
                broken.append(f"{pg.item_name}.{direction}→{target}")
    if not broken:
        return ValidationResult(
            'nav_targets', ValidationResult.PASS,
            "All navigation links valid")
    return ValidationResult(
        'nav_targets', ValidationResult.ERROR,
        f"{len(broken)} broken nav link(s): {', '.join(broken[:5])}"
        + ("..." if len(broken) > 5 else ""),
        [b.split('.')[0] for b in broken])


def _check_startactive(scene):
    """Check that exactly one selectable item has startactive=true."""
    active = [pg for pg in scene.menu_items if pg.startactive]
    selectable = [pg for pg in scene.menu_items
                  if not pg.neverfocus and (pg.usecmd or pg.nav_up or pg.nav_down)]
    if not selectable:
        return ValidationResult(
            'startactive', ValidationResult.PASS,
            "No selectable items (non-interactive menu)")
    if len(active) == 1:
        return ValidationResult(
            'startactive', ValidationResult.PASS,
            f"Start active: {active[0].item_name}")
    if len(active) == 0:
        return ValidationResult(
            'startactive', ValidationResult.WARN,
            "No item has startactive=true")
    return ValidationResult(
        'startactive', ValidationResult.WARN,
        f"{len(active)} items have startactive (expected 1): "
        + ', '.join(a.item_name for a in active[:3]),
        [a.item_name for a in active])


def _check_transforms(scene):
    """Check that all items have corresponding igTransform in the IGB."""
    missing = [pg for pg in scene.menu_items if not pg.has_transform]
    if not missing:
        return ValidationResult(
            'transforms', ValidationResult.PASS,
            "All items have IGB transforms")
    return ValidationResult(
        'transforms', ValidationResult.WARN,
        f"{len(missing)} item(s) without IGB transform: "
        + ', '.join(m.item_name for m in missing[:5])
        + ("..." if len(missing) > 5 else ""),
        [m.item_name for m in missing])


def _check_onfocus_targets(scene):
    """Check that onfocus entries reference existing items."""
    name_set = {pg.item_name for pg in scene.menu_items}
    broken = []
    for pg in scene.menu_items:
        for of_entry in pg.onfocus_entries:
            target = of_entry.target_item.strip()
            if target and target not in name_set:
                broken.append(f"{pg.item_name}→{target}")
    if not broken:
        return ValidationResult(
            'onfocus_targets', ValidationResult.PASS,
            "All onfocus targets valid")
    return ValidationResult(
        'onfocus_targets', ValidationResult.WARN,
        f"{len(broken)} onfocus target(s) not found: {', '.join(broken[:5])}"
        + ("..." if len(broken) > 5 else ""),
        [b.split('→')[0] for b in broken])


def _check_model_refs(scene):
    """Check that model paths could resolve (if game_dir is set)."""
    game_dir = scene.menu_settings.game_dir
    if not game_dir:
        return ValidationResult(
            'model_refs', ValidationResult.PASS,
            "Model paths not checked (no game dir)")

    from .menu_igb_loader import resolve_igb_path
    broken = []
    seen = set()
    for pg in scene.menu_items:
        ref = pg.model_ref.strip()
        if not ref or ref in seen:
            continue
        seen.add(ref)
        resolved = resolve_igb_path(ref, game_dir)
        if resolved is None:
            broken.append(ref)

    if not broken:
        return ValidationResult(
            'model_refs', ValidationResult.PASS,
            f"All {len(seen)} model paths resolve")
    return ValidationResult(
        'model_refs', ValidationResult.WARN,
        f"{len(broken)} model path(s) not found: {', '.join(broken[:3])}"
        + ("..." if len(broken) > 3 else ""),
        broken)


# ---------------------------------------------------------------------------
# Run all validations
# ---------------------------------------------------------------------------

_ALL_CHECKS = [
    _check_duplicate_names,
    _check_empty_names,
    _check_object_links,
    _check_nav_targets,
    _check_startactive,
    _check_transforms,
    _check_onfocus_targets,
    _check_model_refs,
]


def validate_menu(scene):
    """Run all validation checks.

    Returns:
        list of ValidationResult
    """
    return [check(scene) for check in _ALL_CHECKS]


# ---------------------------------------------------------------------------
# Auto-fix: generate navigation from spatial positions
# ---------------------------------------------------------------------------

def auto_fix_navigation(context):
    """Generate up/down/left/right navigation from spatial positions.

    Algorithm:
    1. Collect selectable items (not neverfocus, have usecmd or other interactive attrs)
    2. Sort by Z position (vertical), cluster into rows
    3. Within each row, sort by X position (horizontal)
    4. Assign up/down between adjacent rows, left/right within rows

    Returns:
        (item_count: int, message: str)
    """
    scene = context.scene

    # Collect selectable items with their positions
    selectables = []
    for pg in scene.menu_items:
        if pg.neverfocus or pg.debug_only:
            continue
        # Include if it has a command, nav links, or is not a bare model
        if pg.usecmd or pg.item_type in ('', 'MENU_ITEM_TEXT', 'MENU_ITEM_TEXTBOX',
                                          'MENU_ITEM_BAR', 'MENU_ITEM_BINARY',
                                          'MENU_ITEM_LISTBOX', 'MENU_ITEM_LIST_CYCLE'):
            obj = bpy.data.objects.get(pg.object_name)
            if obj is None:
                continue
            selectables.append((pg, obj.location.x, obj.location.z))

    if not selectables:
        return 0, "No selectable items found"

    # Sort by Z descending (top of screen = highest Z)
    selectables.sort(key=lambda s: -s[2])

    # Cluster into rows by Z position
    Z_THRESHOLD = 15.0
    rows = []
    current_row = [selectables[0]]

    for i in range(1, len(selectables)):
        _, _, prev_z = current_row[-1]
        _, _, curr_z = selectables[i]
        if abs(prev_z - curr_z) <= Z_THRESHOLD:
            current_row.append(selectables[i])
        else:
            rows.append(current_row)
            current_row = [selectables[i]]
    rows.append(current_row)

    # Sort each row by X (left to right)
    for row in rows:
        row.sort(key=lambda s: s[1])

    # Assign navigation
    for row_idx, row in enumerate(rows):
        for col_idx, (pg, x, z) in enumerate(row):
            # Up: closest X in row above
            above_row = rows[row_idx - 1] if row_idx > 0 else rows[-1]
            pg.nav_up = _find_closest_x(above_row, x)

            # Down: closest X in row below
            below_row = rows[row_idx + 1] if row_idx < len(rows) - 1 else rows[0]
            pg.nav_down = _find_closest_x(below_row, x)

            # Left/Right within row
            if len(row) > 1:
                left_idx = (col_idx - 1) % len(row)
                right_idx = (col_idx + 1) % len(row)
                pg.nav_left = row[left_idx][0].item_name
                pg.nav_right = row[right_idx][0].item_name
            else:
                pg.nav_left = " "
                pg.nav_right = " "

    count = sum(len(r) for r in rows)
    return count, f"Navigation set for {count} items in {len(rows)} row(s)"


def _find_closest_x(row, target_x):
    """Find the item in a row whose X is closest to target_x."""
    best = row[0]
    best_dist = abs(row[0][1] - target_x)
    for item in row[1:]:
        dist = abs(item[1] - target_x)
        if dist < best_dist:
            best = item
            best_dist = dist
    return best[0].item_name
