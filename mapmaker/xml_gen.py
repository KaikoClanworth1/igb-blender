"""XML generators for all map data file types (ENGB, CHRB, NAVB, BOYB, PKGB).

Produces Raven XMLB text format (curly-brace syntax) that xmlb-compile.exe
compiles to XMLB binary.  Also supports import from ET.Element trees
(produced by the Python xmlb.py reader).
"""

import os

import bpy


def _float_str(v, decimals=6):
    """Format a float, stripping trailing zeros after decimal point."""
    s = f"{v:.{decimals}f}"
    if '.' in s:
        s = s.rstrip('0').rstrip('.')
    return s


def _vec_str(values, decimals=6):
    """Format a sequence of floats as space-separated string."""
    return " ".join(_float_str(v, decimals) for v in values)


# ===========================================================================
# Raven text format writer
# ===========================================================================

class _RavenWriter:
    """Builds Raven XMLB text-format strings.

    Format reference (decompiled sanctuary1.engb.xml / .pkgb.xml):
        XMLB root_tag {
           child_tag {
           key = value ;
           }

           parent_tag {
              nested_tag {
              key = value ;
              }

           }

        }
    """

    def __init__(self, root_tag):
        self._lines = [f"XMLB {root_tag} {{"]
        self._indent = 1  # current indent level (3 spaces per level)

    # -- primitives --

    def _pad(self):
        return "   " * self._indent

    def open_block(self, tag):
        """Open a child element block: 'tag {'"""
        self._lines.append(f"{self._pad()}{tag} {{")
        self._indent += 1

    def close_block(self):
        """Close current block: '}'"""
        self._indent -= 1
        self._lines.append(f"{self._pad()}}}")

    def attr(self, key, value):
        """Write a single attribute: 'key = value ;'"""
        self._lines.append(f"{self._pad()}{key} = {value} ;")

    def blank(self):
        """Write a blank line (separator between sibling blocks)."""
        self._lines.append("")

    def build(self):
        """Return the complete text, closing the root block."""
        # Close root
        self._lines.append("")
        self._lines.append("}")
        self._lines.append("")
        return "\n".join(self._lines)

    # -- convenience --

    def element(self, tag, attrs):
        """Write a complete element with sorted attributes.

        Args:
            tag: element tag name
            attrs: dict of key -> value (written in sorted key order)
        """
        self.open_block(tag)
        for k in sorted(attrs.keys()):
            self.attr(k, attrs[k])
        self.close_block()


# ===========================================================================
# ENGB generation
# ===========================================================================

def generate_engb_text(scene):
    """Generate ENGB in Raven text format from scene data.

    Returns a string in Raven XMLB text format.
    """
    settings = scene.mm_settings
    w = _RavenWriter("world")

    # --- World entity (always first) ---
    w.open_block("entity")
    w.attr("act", str(settings.act))
    if settings.combatlocked:
        w.attr("combatlocked", "true")
    w.attr("level", str(settings.level))
    w.attr("name", "world")
    if settings.nosave:
        w.attr("nosave", "true")
    if any(v > 0 for v in settings.partylight):
        w.attr("partylight", _vec_str(settings.partylight, 2))
    if settings.partylightradius > 0:
        w.attr("partylightradius", _float_str(settings.partylightradius, 0))
    if settings.soundfile:
        w.attr("soundfile", settings.soundfile)
    zone_script = settings.zone_script
    if not zone_script:
        zone_script = f"{settings.map_path}/{settings.map_name}"
    w.attr("zonescript", zone_script)
    w.close_block()

    # --- Precache entries ---
    for entry in scene.mm_precache:
        if entry.filename:
            w.blank()
            w.open_block("precache")
            w.attr("filename", entry.filename)
            w.attr("type", entry.entry_type)
            w.close_block()

    # --- Entity definitions ---
    for edef in scene.mm_entity_defs:
        # Collect all properties into a dict for sorted output
        props = {}
        props["classname"] = edef.classname
        props["name"] = edef.entity_name
        if edef.nocollide:
            props["nocollide"] = "true"

        if edef.character and edef.classname == 'monsterspawnerent':
            props["character"] = edef.character
        if edef.monster_name and edef.classname == 'monsterspawnerent':
            props["monster_name"] = edef.monster_name
        if edef.model:
            props["model"] = edef.model

        for prop in edef.properties:
            if prop.key:
                props[prop.key] = prop.value

        w.blank()
        w.element("entity", props)

    # --- Entity instances (entinst blocks) ---
    instances_by_type = {}
    if "[MapMaker] Entities" in bpy.data.collections:
        for obj in bpy.data.collections["[MapMaker] Entities"].objects:
            if obj.get("mm_preview"):
                continue
            etype = obj.get("mm_entity_type", "")
            if etype:
                if etype not in instances_by_type:
                    instances_by_type[etype] = []
                instances_by_type[etype].append(obj)

    for etype, objects in instances_by_type.items():
        w.blank()
        w.open_block("entinst")
        w.attr("type", etype)

        for obj in objects:
            w.open_block("inst")

            extents = obj.get("mm_extents", "")
            if extents:
                w.attr("extents", extents)

            w.attr("name", obj.name)

            rz = obj.rotation_euler.z
            if abs(rz) > 1e-5:
                w.attr("orient", f"0 0 {_float_str(rz)}")

            loc = obj.location
            w.attr("pos", f"{_float_str(loc.x)} {_float_str(loc.y)} {_float_str(loc.z)}")
            w.close_block()
            w.blank()

        w.close_block()

    return w.build()


# ===========================================================================
# CHRB generation
# ===========================================================================

def generate_chrb_text(scene):
    """Generate CHRB in Raven text format."""
    w = _RavenWriter("characters")
    for entry in scene.mm_characters:
        if entry.char_name:
            w.open_block("character")
            w.attr("name", entry.char_name)
            w.close_block()
            w.blank()
    return w.build()


# ===========================================================================
# NAVB generation
# ===========================================================================

def generate_navb_text(scene):
    """Generate NAVB in Raven text format, or None if no cells."""
    import ast

    cellsize = scene.mm_settings.nav_cellsize
    raw = scene.get("mm_nav_cells", "")
    if not raw:
        return None

    try:
        cells = ast.literal_eval(raw)
    except Exception:
        return None

    w = _RavenWriter("nav")
    # cellsize is a root-level attribute
    w.attr("cellsize", str(cellsize))
    w.blank()

    for gx, gy, wz in cells:
        w.open_block("c")
        w.attr("p", f"{gx} {gy} {int(round(wz))}")
        w.close_block()
        w.blank()

    return w.build()


# ===========================================================================
# BOYB generation
# ===========================================================================

def generate_boyb_text():
    """Generate BOYB in Raven text format (usually empty)."""
    w = _RavenWriter("Buoys")
    return w.build()


# ===========================================================================
# PKGB generation
# ===========================================================================

def generate_pkgb_text(scene):
    """Generate PKGB in Raven text format (package manifest).

    Ordering follows the real game PKGB convention:
        1. combat_is, bigconvmap flags
        2. xml (npcstat)
        3. model (map geometry)
        4. script (zone script)
        5. HUD heads + conversations + scripts (interleaved)
        6. Precache entries (models, effects, textures, sounds)
        7. Entity def models/scripts/effects
        8. Actor skins + anim DBs
        9. Zone-level entries (characters, zonexml, nav, boy)
       10. zam (automap)
    """
    settings = scene.mm_settings
    w = _RavenWriter("packagedef")
    map_path = f"maps/{settings.map_path}/{settings.map_name}"
    zone_script = settings.zone_script
    if not zone_script:
        zone_script = f"{settings.map_path}/{settings.map_name}"

    # --- Combat/bigconvmap flags ---
    w.open_block("combat_is")
    w.attr("filename", "on" if settings.combatlocked else "off")
    w.close_block()
    w.blank()

    w.open_block("bigconvmap")
    w.attr("filename", "on" if settings.bigconvmap else "off")
    w.close_block()
    w.blank()

    # --- Always include npcstat ---
    w.open_block("xml")
    w.attr("filename", "data/npcstat")
    w.close_block()
    w.blank()

    # --- Map model ---
    w.open_block("model")
    w.attr("filename", map_path)
    w.close_block()
    w.blank()

    # --- Zone script ---
    w.open_block("script")
    w.attr("filename", f"scripts/{zone_script}")
    w.close_block()

    # --- Conversation HUD heads + conversation XML entries ---
    # Use per-type dedup sets so a conversation path doesn't block a script
    # with the same name (e.g. "act4/.../beastconvo" as both xml and script).
    added_convs = set()
    added_scripts = set()
    added_models = set()
    added_effects = set()
    added_hud_heads = set()

    if hasattr(scene, 'mm_conversations'):
        for conv in scene.mm_conversations:
            if len(conv.nodes) == 0:
                continue

            # Emit HUD head model before its conversations (if set)
            if conv.hud_head and conv.hud_head not in added_hud_heads:
                added_hud_heads.add(conv.hud_head)
                hud_path = conv.hud_head
                if not hud_path.startswith("HUD/"):
                    hud_path = f"HUD/{hud_path}"
                w.blank()
                w.open_block("model")
                w.attr("filename", hud_path)
                w.close_block()
                added_models.add(hud_path)

            # Emit conversation XML reference
            conv_path = conv.conv_path
            if not conv_path:
                conv_path = f"{settings.map_path}/{settings.map_name}"
            conv_filename = f"{conv_path}/{conv.conv_name}"
            if conv_filename not in added_convs:
                added_convs.add(conv_filename)
                w.blank()
                w.open_block("xml")
                w.attr("filename", f"conversations/{conv_filename}")
                w.close_block()

    # --- Pre-collect full-path script basenames from entity defs ---
    _script_keys = {'actscript', 'spawnscript', 'monster_actscript'}
    edef_script_basenames = set()
    for edef in scene.mm_entity_defs:
        for prop in edef.properties:
            if prop.key in _script_keys and prop.value and '(' not in prop.value:
                basename = prop.value.replace('\\', '/').rsplit('/', 1)[-1]
                if '/' in prop.value:
                    edef_script_basenames.add(basename)

    # --- Add conversation/script/model/fx/sound references from precache ---
    for entry in scene.mm_precache:
        if not entry.filename:
            continue

        if entry.entry_type == 'conversation':
            conv_out = f"conversations/{entry.filename}"
            if entry.filename in added_convs or conv_out in added_convs:
                continue
            basename = entry.filename.replace('\\', '/').rsplit('/', 1)[-1]
            skip = False
            for existing in added_convs:
                if existing.replace('\\', '/').rsplit('/', 1)[-1] == basename:
                    skip = True
                    break
            if skip:
                continue
            added_convs.add(entry.filename)
            added_convs.add(conv_out)
            w.blank()
            w.open_block("xml")
            w.attr("filename", conv_out)
            w.close_block()
        elif entry.entry_type == 'script':
            fn = entry.filename
            if '(' in fn:
                continue
            if fn in added_scripts:
                continue
            # Skip bare-name scripts when a full-path version exists
            if '/' not in fn and fn in edef_script_basenames:
                continue
            added_scripts.add(fn)
            if not fn.startswith("scripts/"):
                fn = f"scripts/{fn}"
            w.blank()
            w.open_block("script")
            w.attr("filename", fn)
            w.close_block()
        elif entry.entry_type == 'model':
            fn = entry.filename
            if fn in added_models:
                continue
            added_models.add(fn)
            if not fn.startswith(("models/", "maps/", "HUD/", "hud/")):
                fn = f"models/{fn}"
            w.blank()
            w.open_block("model")
            w.attr("filename", fn)
            w.close_block()
        elif entry.entry_type == 'fx':
            if entry.filename in added_effects:
                continue
            added_effects.add(entry.filename)
            w.blank()
            w.open_block("effect")
            w.attr("filename", entry.filename)
            w.close_block()

    # --- Models from entity defs ---
    for edef in scene.mm_entity_defs:
        if edef.model and edef.model not in added_models:
            added_models.add(edef.model)
            fn = edef.model
            if not fn.startswith(("models/", "maps/", "HUD/", "hud/")):
                fn = f"models/{fn}"
            w.blank()
            w.open_block("model")
            w.attr("filename", fn)
            w.close_block()

    # --- Scripts and effects from entity def custom properties ---
    # (_script_keys already defined above for pre-collection)
    _fx_keys = {'loopfx', 'deatheffect', 'acteffect'}
    for edef in scene.mm_entity_defs:
        for prop in edef.properties:
            if not prop.value:
                continue
            if prop.key in _script_keys:
                val = prop.value
                if '(' in val:
                    continue
                if val in added_scripts:
                    continue
                added_scripts.add(val)
                if not val.startswith("scripts/"):
                    val = f"scripts/{val}"
                w.blank()
                w.open_block("script")
                w.attr("filename", val)
                w.close_block()
            elif prop.key in _fx_keys:
                if prop.value in added_effects:
                    continue
                added_effects.add(prop.value)
                w.blank()
                w.open_block("effect")
                w.attr("filename", prop.value)
                w.close_block()

    # --- Actor skins and anim DBs from character database ---
    game_data_dir = bpy.path.abspath(settings.game_data_dir) if settings.game_data_dir else ""
    added_skins = set()
    added_animdbs = set()

    if game_data_dir:
        try:
            from .game_database import get_character_db
            char_db = get_character_db(game_data_dir)
        except Exception:
            char_db = {}
    else:
        char_db = {}

    for edef in scene.mm_entity_defs:
        if edef.character and char_db:
            char_info = char_db.get(edef.character)
            if char_info:
                if char_info.skin and char_info.skin not in added_skins:
                    added_skins.add(char_info.skin)
                    w.blank()
                    w.open_block("actorskin")
                    w.attr("filename", char_info.skin)
                    w.close_block()
                if char_info.characteranims and char_info.characteranims not in added_animdbs:
                    added_animdbs.add(char_info.characteranims)
                    w.blank()
                    w.open_block("actoranimdb")
                    w.attr("filename", char_info.characteranims)
                    w.close_block()

    for char_entry in scene.mm_characters:
        if char_entry.char_name and char_db:
            char_info = char_db.get(char_entry.char_name)
            if char_info:
                if char_info.skin and char_info.skin not in added_skins:
                    added_skins.add(char_info.skin)
                    w.blank()
                    w.open_block("actorskin")
                    w.attr("filename", char_info.skin)
                    w.close_block()
                if char_info.characteranims and char_info.characteranims not in added_animdbs:
                    added_animdbs.add(char_info.characteranims)
                    w.blank()
                    w.open_block("actoranimdb")
                    w.attr("filename", char_info.characteranims)
                    w.close_block()

    # --- Zone-level entries ---
    for tag in ("characters", "zonexml", "nav", "boy"):
        w.blank()
        w.open_block(tag)
        w.attr("filename", map_path)
        w.close_block()

    # --- Automap (ZAM) entry ---
    # In real game PKGBs, the zam entry always comes LAST, after zone entries.
    # Path convention: "automaps/{act}/{area}/{mapname}" (NOT "maps/")
    automap_path = settings.automap_path
    if not automap_path:
        automap_path = f"automaps/{settings.map_path}/{settings.map_name}"
    w.blank()
    w.open_block("zam")
    w.attr("filename", automap_path)
    w.close_block()

    return w.build()


# ===========================================================================
# Conversation text generation
# ===========================================================================

def generate_conversation_text(conv):
    """Generate a conversation in Raven text format.

    Args:
        conv: MM_ConversationData PropertyGroup (or any object with .nodes)

    Returns:
        String in Raven XMLB text format.
    """
    from .conversation import get_children

    w = _RavenWriter("conversation")

    def _build_children(parent_id):
        children = get_children(conv, parent_id)
        for node in children:
            if node.node_type == 'START_CONDITION':
                attrs = {}
                if node.condition_script:
                    attrs["conditionscriptfile"] = node.condition_script
                if node.run_once:
                    attrs["runonce"] = "true"
                w.open_block("startCondition")
                for k in sorted(attrs.keys()):
                    w.attr(k, attrs[k])
                _build_children(node.node_id)
                w.close_block()
                w.blank()

            elif node.node_type == 'PARTICIPANT':
                w.open_block("participant")
                w.attr("name", node.participant_name)
                _build_children(node.node_id)
                w.close_block()
                w.blank()

            elif node.node_type == 'LINE':
                w.open_block("line")
                if node.sound_to_play:
                    w.attr("soundtoplay", node.sound_to_play)
                if node.text:
                    w.attr("text", node.text)
                if node.text_b:
                    w.attr("textb", node.text_b)
                if node.sound_to_play_b:
                    w.attr("soundtoplayb", node.sound_to_play_b)
                if node.tag_index:
                    w.attr("tagindex", node.tag_index)
                _build_children(node.node_id)
                w.close_block()
                w.blank()

            elif node.node_type == 'RESPONSE':
                w.open_block("response")
                if node.chosen_script:
                    w.attr("chosenscriptfile", node.chosen_script)
                if node.conversation_end:
                    w.attr("conversationend", "true")
                if node.only_if_brotherhood:
                    w.attr("onlyif_brotherhood", "true")
                if node.only_if_xman:
                    w.attr("onlyif_xman", "true")
                if node.script_file:
                    w.attr("scriptfile", node.script_file)
                if node.tag_index:
                    w.attr("tagindex", node.tag_index)
                if node.tag_jump:
                    w.attr("tagjump", node.tag_jump)
                if node.response_text:
                    w.attr("text", node.response_text)
                _build_children(node.node_id)
                w.close_block()
                w.blank()

    _build_children(-1)
    return w.build()


# ===========================================================================
# Write helpers
# ===========================================================================

def _write_raven_text(text, filepath):
    """Write Raven text format string to a file."""
    with open(filepath, 'w', encoding='utf-8') as f:
        f.write(text)


def generate_all(scene, output_dir):
    """Generate all text files for the map. Returns list of written file paths."""
    settings = scene.mm_settings
    base_name = settings.map_name
    written = []

    # ENGB
    engb_text = generate_engb_text(scene)
    engb_path = os.path.join(output_dir, base_name + ".engb.xml")
    _write_raven_text(engb_text, engb_path)
    written.append(engb_path)

    # CHRB
    chrb_text = generate_chrb_text(scene)
    chrb_path = os.path.join(output_dir, base_name + ".chrb.xml")
    _write_raven_text(chrb_text, chrb_path)
    written.append(chrb_path)

    # NAVB
    navb_text = generate_navb_text(scene)
    if navb_text is not None:
        navb_path = os.path.join(output_dir, base_name + ".navb.xml")
        _write_raven_text(navb_text, navb_path)
        written.append(navb_path)

    # BOYB
    boyb_text = generate_boyb_text()
    boyb_path = os.path.join(output_dir, base_name + ".boyb.xml")
    _write_raven_text(boyb_text, boyb_path)
    written.append(boyb_path)

    # Conversation files
    if hasattr(scene, 'mm_conversations'):
        for conv in scene.mm_conversations:
            if len(conv.nodes) == 0:
                continue
            conv_text = generate_conversation_text(conv)
            conv_xml_path = os.path.join(output_dir, conv.conv_name + ".engb.xml")
            _write_raven_text(conv_text, conv_xml_path)
            written.append(conv_xml_path)

    # PKGB (generated last so it can reference all conversation/script paths)
    pkgb_text = generate_pkgb_text(scene)
    pkgb_path = os.path.join(output_dir, base_name + ".pkgb.xml")
    _write_raven_text(pkgb_text, pkgb_path)
    written.append(pkgb_path)

    return written


# Keep old name as alias for callers
generate_all_xml = generate_all


# ===========================================================================
# Import (ENGB XML → scene data)
# ===========================================================================

def import_engb_xml(scene, root, skip_classes=None):
    """Import entities from a parsed ENGB XML tree into the scene.

    The root is an ET.Element produced by the Python xmlb.py reader (which
    reads binary XMLB into standard ET trees).  This importer works with
    that representation.

    Args:
        scene: bpy.types.Scene
        root: ET.Element (root <world> element)
        skip_classes: optional set of classnames to skip (e.g. {'lightent'})

    Returns:
        dict with counts: {'entity_defs', 'instances', 'precache', 'skipped_defs'}
    """
    from .operators import _get_entity_collection, ENTITY_COLLECTION_NAME
    from .entity_defs import get_visual_for_classname, ENTITY_EMPTY_SIZE

    if skip_classes is None:
        skip_classes = set()

    stats = {'entity_defs': 0, 'instances': 0, 'precache': 0, 'skipped_defs': 0}

    # Track which entity names are skipped so we can also skip their instances
    skipped_entity_names = set()

    # First pass: collect entity defs and determine which are skipped
    # We need two passes because entinst references entity defs by name
    entity_elements = []
    precache_elements = []
    entinst_elements = []

    for child in root:
        tag = child.tag
        if tag == "entity":
            entity_elements.append(child)
        elif tag == "precache":
            precache_elements.append(child)
        elif tag == "entinst":
            entinst_elements.append(child)

    # Process entity defs
    for child in entity_elements:
        name = child.get("name", "")
        classname = child.get("classname", "")

        if name == "world":
            settings = scene.mm_settings
            settings.map_name = name
            if child.get("act"):
                settings.act = int(child.get("act"))
            if child.get("level"):
                settings.level = int(child.get("level"))
            if child.get("zonescript"):
                settings.zone_script = child.get("zonescript")
            if child.get("soundfile"):
                settings.soundfile = child.get("soundfile")
            if child.get("combatlocked"):
                settings.combatlocked = child.get("combatlocked") == "true"
            if child.get("nosave"):
                settings.nosave = child.get("nosave") == "true"
            if child.get("partylight"):
                vals = [float(v) for v in child.get("partylight").split()]
                if len(vals) >= 3:
                    settings.partylight = tuple(vals[:3])
            if child.get("partylightradius"):
                settings.partylightradius = float(child.get("partylightradius"))
            continue

        # Check if this classname should be skipped
        if classname in skip_classes:
            skipped_entity_names.add(name)
            stats['skipped_defs'] += 1
            continue

        edef = scene.mm_entity_defs.add()
        edef.entity_name = name
        if classname:
            try:
                edef.classname = classname
            except TypeError:
                edef.classname = 'ent'
                prop = edef.properties.add()
                prop.key = "classname"
                prop.value = classname

        if child.get("character"):
            edef.character = child.get("character")
        if child.get("monster_name"):
            edef.monster_name = child.get("monster_name")
        if child.get("model"):
            edef.model = child.get("model")
        if child.get("nocollide"):
            edef.nocollide = child.get("nocollide") == "true"

        skip_keys = {"name", "classname", "character", "monster_name", "model", "nocollide"}
        for key, val in child.attrib.items():
            if key not in skip_keys:
                prop = edef.properties.add()
                prop.key = key
                prop.value = val

        stats['entity_defs'] += 1

    # Process precache entries
    for child in precache_elements:
        entry = scene.mm_precache.add()
        entry.filename = child.get("filename", "")
        ptype = child.get("type", "script")
        try:
            entry.entry_type = ptype
        except TypeError:
            entry.entry_type = 'script'
        stats['precache'] += 1

    # Process entity instances
    for child in entinst_elements:
        etype = child.get("type", "")

        # Skip instances of skipped entity types
        if etype in skipped_entity_names:
            continue

        col = _get_entity_collection()

        for inst in child:
            if inst.tag != "inst":
                continue

            pos_str = inst.get("pos", "0 0 0")
            pos = [float(v) for v in pos_str.split()]

            orient_str = inst.get("orient", "")
            rz = 0.0
            if orient_str:
                orient = [float(v) for v in orient_str.split()]
                rz = orient[2] if len(orient) >= 3 else 0.0

            inst_name = inst.get("name", etype)
            extents = inst.get("extents", "")

            classname = "ent"
            for edef in scene.mm_entity_defs:
                if edef.entity_name == etype:
                    classname = edef.classname
                    break

            display_type, color = get_visual_for_classname(classname)

            empty = bpy.data.objects.new(inst_name, None)
            empty.empty_display_type = display_type
            empty.empty_display_size = ENTITY_EMPTY_SIZE
            empty.location = (pos[0] if len(pos) > 0 else 0,
                              pos[1] if len(pos) > 1 else 0,
                              pos[2] if len(pos) > 2 else 0)
            empty.rotation_euler.z = rz
            empty.color = color

            empty["mm_entity_type"] = etype
            empty["mm_classname"] = classname
            if extents:
                empty["mm_extents"] = extents

            col.objects.link(empty)
            stats['instances'] += 1

    return stats
