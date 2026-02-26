"""Conversation system — flattened tree PropertyGroups, helpers, and XML serialization.

Blender PropertyGroups don't support recursive nesting, so we use a flat list
of nodes with parent_id references to build the conversation tree structure.

Conversation XML format (X-Men Legends II):
<conversation>
  <startCondition conditionscriptfile="..." runonce="true">
    <participant><name>default</name>
      <line text="%CharName%: dialogue" soundtoplay="voice/...">
        <response text="Player choice" chosenscriptfile="..." conversationend="true"/>
        <response text="Another choice">
          <line text="%CharName%: more dialogue">...</line>
        </response>
      </line>
    </participant>
  </startCondition>
</conversation>
"""

import re

import bpy
from bpy.props import (
    StringProperty, IntProperty, BoolProperty,
    EnumProperty, CollectionProperty, PointerProperty,
)
from bpy.types import PropertyGroup
import xml.etree.ElementTree as ET


# ---------------------------------------------------------------------------
# Node types
# ---------------------------------------------------------------------------

CONV_NODE_TYPES = [
    ('START_CONDITION', "Start Condition", "Top-level conversation branch"),
    ('PARTICIPANT', "Participant", "NPC participant in conversation"),
    ('LINE', "NPC Line", "Dialogue line spoken by NPC"),
    ('RESPONSE', "Player Response", "Player dialogue choice"),
]


# ---------------------------------------------------------------------------
# PropertyGroups
# ---------------------------------------------------------------------------

class MM_ConversationNode(PropertyGroup):
    """A single node in the flattened conversation tree."""

    # Tree structure
    node_id: IntProperty(default=-1)
    parent_id: IntProperty(default=-1)  # -1 = root level (under conversation)
    node_type: EnumProperty(
        name="Type",
        items=CONV_NODE_TYPES,
        default='LINE',
    )
    sort_order: IntProperty(default=0)

    # StartCondition fields
    condition_script: StringProperty(
        name="Condition Script",
        description="BehavEd script that gates this conversation branch",
    )
    run_once: BoolProperty(
        name="Run Once",
        description="Only trigger this branch once",
        default=False,
    )

    # Participant fields
    participant_name: StringProperty(
        name="Participant",
        description="NPC participant name (usually 'default')",
        default="default",
    )

    # Line fields
    text: StringProperty(
        name="Text",
        description="Dialogue text (use %CharName% for speaker name)",
    )
    sound_to_play: StringProperty(
        name="Sound",
        description="Voice file path (e.g. 'voice/profx/...')",
    )
    text_b: StringProperty(
        name="Text (Brotherhood)",
        description="Brotherhood faction variant text",
    )
    sound_to_play_b: StringProperty(
        name="Sound (Brotherhood)",
        description="Brotherhood variant voice file",
    )

    # Response fields
    response_text: StringProperty(
        name="Response Text",
        description="Player choice text",
    )
    chosen_script: StringProperty(
        name="Chosen Script",
        description="Script to run when this response is chosen",
    )
    script_file: StringProperty(
        name="Script File",
        description="BehavEd script file path",
    )
    conversation_end: BoolProperty(
        name="End Conversation",
        description="End conversation after this response",
        default=False,
    )
    tag_jump: StringProperty(
        name="Tag Jump",
        description="Jump to tagged node (conversation hub/loop)",
    )
    tag_index: StringProperty(
        name="Tag Index",
        description="This node's tag (for jumps)",
    )
    only_if_xman: BoolProperty(
        name="X-Men Only",
        description="Only show for X-Men faction",
        default=False,
    )
    only_if_brotherhood: BoolProperty(
        name="Brotherhood Only",
        description="Only show for Brotherhood faction",
        default=False,
    )

    # XML1-only attributes (X-Men Legends 1 uses these, XML2 does not)
    time_delay: IntProperty(
        name="Time Delay",
        description="Delay in seconds before proceeding (XML1 only)",
        default=0,
    )
    run_without_user: BoolProperty(
        name="Run Without User",
        description="Line plays automatically without user input (XML1 only)",
        default=False,
    )
    no_return_cam: BoolProperty(
        name="No Return To Game Cam",
        description="Don't return to gameplay camera at end (XML1 only)",
        default=False,
    )
    enable_ai: BoolProperty(
        name="Enable AI",
        description="Enable NPC AI during conversation (XML1 only)",
        default=False,
    )
    no_anim_reset: BoolProperty(
        name="No Anim Reset",
        description="Don't reset activator animations (XML1 only)",
        default=False,
    )
    no_auto_face: BoolProperty(
        name="No Auto Face",
        description="Don't auto-face the activator (XML1 only)",
        default=False,
    )
    line_script_file: StringProperty(
        name="Line Script File",
        description="Camera/effect script for this line (XML1 only)",
    )


class MM_ConversationData(PropertyGroup):
    """A complete conversation with its flat node list."""

    conv_name: StringProperty(
        name="Name",
        description="Conversation filename (without extension)",
        default="new_conversation",
    )
    conv_path: StringProperty(
        name="Path",
        description="Conversation path prefix (e.g. 'act1/sanctuary')",
        default="",
    )
    hud_head: StringProperty(
        name="HUD Head",
        description="HUD head model for this conversation's NPC portrait "
                    "(e.g. 'hud_head_1101' for Professor X). "
                    "Skin number matches the character's actor skin code",
        default="",
    )
    nodes: CollectionProperty(type=MM_ConversationNode)
    nodes_index: IntProperty(default=0)
    next_node_id: IntProperty(default=0)


# ---------------------------------------------------------------------------
# Tree helpers
# ---------------------------------------------------------------------------

def get_children(conv, parent_id):
    """Get child nodes of a parent, sorted by sort_order."""
    children = []
    for node in conv.nodes:
        if node.parent_id == parent_id:
            children.append(node)
    children.sort(key=lambda n: n.sort_order)
    return children


def get_depth(conv, node):
    """Get nesting depth of a node (0 = root level)."""
    depth = 0
    current_parent = node.parent_id
    visited = set()
    while current_parent != -1:
        if current_parent in visited:
            break  # Safety: prevent infinite loops
        visited.add(current_parent)
        found = False
        for n in conv.nodes:
            if n.node_id == current_parent:
                current_parent = n.parent_id
                depth += 1
                found = True
                break
        if not found:
            break
    return depth


def get_flat_display_order(conv):
    """Get nodes in DFS (depth-first) order for UIList display.

    Returns list of (node, depth) tuples.
    """
    result = []

    def _visit(parent_id, depth):
        children = get_children(conv, parent_id)
        for child in children:
            result.append((child, depth))
            _visit(child.node_id, depth + 1)

    _visit(-1, 0)
    return result


def allocate_node_id(conv):
    """Allocate a new unique node ID."""
    nid = conv.next_node_id
    conv.next_node_id = nid + 1
    return nid


def get_next_sort_order(conv, parent_id):
    """Get the next sort_order value for children of parent_id."""
    max_order = -1
    for node in conv.nodes:
        if node.parent_id == parent_id:
            max_order = max(max_order, node.sort_order)
    return max_order + 1


def find_node_by_id(conv, node_id):
    """Find a node by its node_id."""
    for node in conv.nodes:
        if node.node_id == node_id:
            return node
    return None


def find_node_index(conv, node_id):
    """Find the collection index of a node by its node_id."""
    for i, node in enumerate(conv.nodes):
        if node.node_id == node_id:
            return i
    return -1


def remove_node_and_descendants(conv, node_id):
    """Remove a node and all its descendants from the conversation."""
    to_remove = set()

    def _collect(nid):
        to_remove.add(nid)
        for node in conv.nodes:
            if node.parent_id == nid:
                _collect(node.node_id)

    _collect(node_id)

    # Remove in reverse index order to avoid index shifting issues
    indices_to_remove = []
    for i, node in enumerate(conv.nodes):
        if node.node_id in to_remove:
            indices_to_remove.append(i)

    for idx in reversed(indices_to_remove):
        conv.nodes.remove(idx)


# ---------------------------------------------------------------------------
# Preview / display helpers
# ---------------------------------------------------------------------------

def parse_speaker_text(raw_text):
    """Parse '%Speaker%: dialogue' into (speaker, dialogue).

    Examples:
        '%Beast%: Hello friend'  → ('Beast', 'Hello friend')
        '%BLANK%'                → ('', '')
        '%PLAYER%: Thanks'       → ('PLAYER', 'Thanks')
        'Just raw text'          → ('', 'Just raw text')
        ''                       → ('', '')

    Returns:
        (speaker_name, dialogue_text) tuple.
    """
    if not raw_text:
        return ("", "")

    text = raw_text.strip()

    if text == "%BLANK%":
        return ("", "")

    # Match %SpeakerName%: dialogue  OR  %SpeakerName% dialogue
    match = re.match(r'^%([^%]+)%[:\s]*(.*)', text)
    if match:
        speaker = match.group(1)
        dialogue = match.group(2).strip()
        return (speaker, dialogue)

    return ("", text)


def get_node_indicators(node):
    """Get visual indicator flags for a conversation node.

    Returns dict for the enhanced UIList trailing icons.
    """
    return {
        'has_sound': bool(node.sound_to_play),
        'has_end': (node.conversation_end
                    if node.node_type == 'RESPONSE' else False),
        'has_tag_jump': (bool(node.tag_jump)
                         if node.node_type == 'RESPONSE' else False),
        'has_tag_index': bool(node.tag_index),
        'has_faction': ('xmen' if node.only_if_xman else
                        'brotherhood' if node.only_if_brotherhood else None),
        'has_brotherhood_variant': bool(node.text_b),
        'is_blank': (node.response_text.strip() == '%BLANK%'
                     if node.node_type == 'RESPONSE' else False),
    }


def get_conversation_context(conv, node):
    """Build display context for the game-style dialogue preview.

    Walks the tree around the selected node to gather the speaker,
    dialogue text, child responses, parent line, and follow-up line
    — everything needed to render a game-like dialogue box.

    Args:
        conv: MM_ConversationData
        node: MM_ConversationNode (currently selected)

    Returns:
        dict with preview data (see keys below).
    """
    result = {
        'mode': node.node_type.lower(),
        'speaker': '',
        'dialogue': '',
        'dialogue_b': '',
        'responses': [],
        'parent_speaker': '',
        'parent_dialogue': '',
        'follow_up_speaker': '',
        'follow_up_dialogue': '',
        'has_sound': False,
        'sound_path': '',
    }

    if node.node_type == 'LINE':
        speaker, dialogue = parse_speaker_text(node.text)
        result['speaker'] = speaker
        result['dialogue'] = dialogue
        result['has_sound'] = bool(node.sound_to_play)
        result['sound_path'] = node.sound_to_play

        if node.text_b:
            _, dialogue_b = parse_speaker_text(node.text_b)
            result['dialogue_b'] = dialogue_b

        # Child responses
        for child in get_children(conv, node.node_id):
            if child.node_type == 'RESPONSE':
                _, resp_text = parse_speaker_text(child.response_text)
                result['responses'].append({
                    'text': resp_text if resp_text else child.response_text,
                    'is_blank': child.response_text.strip() == '%BLANK%',
                    'is_selected': False,
                    'is_end': child.conversation_end,
                    'tag_jump': child.tag_jump,
                    'faction': ('xmen' if child.only_if_xman else
                                'brotherhood' if child.only_if_brotherhood
                                else None),
                    'node_id': child.node_id,
                })
            elif child.node_type == 'LINE':
                child_speaker, _ = parse_speaker_text(child.text)
                result['responses'].append({
                    'text': f"[continues: {child_speaker or 'NPC'}]",
                    'is_blank': False,
                    'is_selected': False,
                    'is_end': False,
                    'tag_jump': '',
                    'faction': None,
                    'node_id': child.node_id,
                })

    elif node.node_type == 'RESPONSE':
        result['mode'] = 'response'

        # Parent LINE — what the NPC said
        parent = find_node_by_id(conv, node.parent_id)
        if parent and parent.node_type == 'LINE':
            p_speaker, p_dialogue = parse_speaker_text(parent.text)
            result['parent_speaker'] = p_speaker
            result['parent_dialogue'] = p_dialogue

            # Sibling responses (including this one, marked selected)
            for sib in get_children(conv, parent.node_id):
                if sib.node_type == 'RESPONSE':
                    _, sib_text = parse_speaker_text(sib.response_text)
                    result['responses'].append({
                        'text': sib_text if sib_text else sib.response_text,
                        'is_blank': sib.response_text.strip() == '%BLANK%',
                        'is_selected': (sib.node_id == node.node_id),
                        'is_end': sib.conversation_end,
                        'tag_jump': sib.tag_jump,
                        'faction': ('xmen' if sib.only_if_xman else
                                    'brotherhood' if sib.only_if_brotherhood
                                    else None),
                        'node_id': sib.node_id,
                    })

        # Follow-up: first child LINE of this response
        for child in get_children(conv, node.node_id):
            if child.node_type == 'LINE':
                fu_speaker, fu_dialogue = parse_speaker_text(child.text)
                result['follow_up_speaker'] = fu_speaker
                result['follow_up_dialogue'] = fu_dialogue
                result['has_sound'] = bool(child.sound_to_play)
                result['sound_path'] = child.sound_to_play
                break

    elif node.node_type in ('START_CONDITION', 'PARTICIPANT'):
        result['mode'] = node.node_type.lower()

    return result


# ---------------------------------------------------------------------------
# XML generation (nodes → ET.Element)
# ---------------------------------------------------------------------------

def nodes_to_xml(conv):
    """Convert conversation nodes to an ET.Element <conversation> tree.

    Args:
        conv: MM_ConversationData PropertyGroup

    Returns:
        ET.Element root (<conversation>)
    """
    root = ET.Element("conversation")

    def _build_children(parent_id, parent_elem):
        children = get_children(conv, parent_id)
        for node in children:
            if node.node_type == 'START_CONDITION':
                elem = ET.SubElement(parent_elem, "startCondition")
                if node.condition_script:
                    elem.set("conditionscriptfile", node.condition_script)
                if node.run_once:
                    elem.set("runonce", "true")
                # XML1-only startCondition attrs
                if node.run_without_user:
                    elem.set("runWithoutUser", "true")
                if node.no_return_cam:
                    elem.set("noReturnToGameCamAtEnd", "true")
                if node.enable_ai:
                    elem.set("enableAI", "true")
                if node.no_anim_reset:
                    elem.set("noAnimResetActivator", "true")
                if node.no_auto_face:
                    elem.set("noAutoFaceActivator", "true")
                _build_children(node.node_id, elem)

            elif node.node_type == 'PARTICIPANT':
                elem = ET.SubElement(parent_elem, "participant")
                # name is an attribute on participant, not a child element
                elem.set("name", node.participant_name)
                _build_children(node.node_id, elem)

            elif node.node_type == 'LINE':
                elem = ET.SubElement(parent_elem, "line")
                if node.text:
                    elem.set("text", node.text)
                if node.sound_to_play:
                    elem.set("soundtoplay", node.sound_to_play)
                if node.text_b:
                    elem.set("textb", node.text_b)
                if node.sound_to_play_b:
                    elem.set("soundtoplayb", node.sound_to_play_b)
                if node.tag_index:
                    elem.set("tagindex", node.tag_index)
                # XML1-only line attrs
                if node.line_script_file:
                    elem.set("scriptFile", node.line_script_file)
                if node.run_without_user:
                    elem.set("runWithoutUser", "true")
                _build_children(node.node_id, elem)

            elif node.node_type == 'RESPONSE':
                elem = ET.SubElement(parent_elem, "response")
                if node.response_text:
                    elem.set("text", node.response_text)
                if node.chosen_script:
                    elem.set("chosenscriptfile", node.chosen_script)
                if node.script_file:
                    elem.set("scriptfile", node.script_file)
                if node.conversation_end:
                    elem.set("conversationend", "true")
                if node.tag_jump:
                    elem.set("tagjump", node.tag_jump)
                if node.tag_index:
                    elem.set("tagindex", node.tag_index)
                if node.only_if_xman:
                    elem.set("onlyif_xman", "true")
                if node.only_if_brotherhood:
                    elem.set("onlyif_brotherhood", "true")
                # XML1-only response attrs
                if node.time_delay > 0:
                    elem.set("timeDelay", str(node.time_delay))
                if node.run_without_user:
                    elem.set("runWithoutUser", "true")
                _build_children(node.node_id, elem)

    _build_children(-1, root)
    return root


# ---------------------------------------------------------------------------
# XML import (ET.Element → nodes)
# ---------------------------------------------------------------------------

def _get_ci(elem, key):
    """Case-insensitive ET.Element attribute get.

    XML2 uses lowercase attrs (soundtoplay, conversationend) while
    XML1 uses camelCase (soundToPlay, conversationEnd).  This helper
    finds the attribute regardless of case.

    Args:
        elem: ET.Element
        key:  attribute name to look up (lowercase preferred)

    Returns:
        The attribute value, or '' if not found.
    """
    # Fast path: exact match (covers XML2 lowercase)
    val = elem.get(key, '')
    if val:
        return val
    # Slow path: case-insensitive scan (covers XML1 camelCase)
    key_lower = key.lower()
    for k, v in elem.attrib.items():
        if k.lower() == key_lower:
            return v
    return ''


def xml_to_nodes(conv, root_element):
    """Import an XML conversation tree into the node list.

    Args:
        conv: MM_ConversationData PropertyGroup
        root_element: ET.Element (<conversation> root)
    """
    conv.nodes.clear()
    conv.next_node_id = 0

    def _import_children(xml_parent, parent_id):
        sort_idx = 0
        for xml_child in xml_parent:
            tag = xml_child.tag

            if tag == "startCondition":
                node = conv.nodes.add()
                node.node_id = allocate_node_id(conv)
                node.parent_id = parent_id
                node.node_type = 'START_CONDITION'
                node.sort_order = sort_idx
                node.condition_script = _get_ci(xml_child, "conditionscriptfile")
                node.run_once = _get_ci(xml_child, "runonce") == "true"
                # XML1-only startCondition attrs
                node.run_without_user = _get_ci(xml_child, "runwithoutuser") == "true"
                node.no_return_cam = _get_ci(xml_child, "noreturntogamecamatend") == "true"
                node.enable_ai = _get_ci(xml_child, "enableai") == "true"
                node.no_anim_reset = _get_ci(xml_child, "noanimresetactivator") == "true"
                node.no_auto_face = _get_ci(xml_child, "noautofaceactivator") == "true"
                _import_children(xml_child, node.node_id)

            elif tag == "participant":
                node = conv.nodes.add()
                node.node_id = allocate_node_id(conv)
                node.parent_id = parent_id
                node.node_type = 'PARTICIPANT'
                node.sort_order = sort_idx
                # name is an attribute on participant element
                node.participant_name = xml_child.get("name", "default")
                _import_children(xml_child, node.node_id)

            elif tag == "line":
                node = conv.nodes.add()
                node.node_id = allocate_node_id(conv)
                node.parent_id = parent_id
                node.node_type = 'LINE'
                node.sort_order = sort_idx
                node.text = xml_child.get("text", "")
                node.sound_to_play = _get_ci(xml_child, "soundtoplay")
                node.text_b = xml_child.get("textb", "")
                node.sound_to_play_b = xml_child.get("soundtoplayb", "")
                node.tag_index = _get_ci(xml_child, "tagindex")
                # XML1-only line attrs
                node.line_script_file = _get_ci(xml_child, "scriptfile")
                node.run_without_user = _get_ci(xml_child, "runwithoutuser") == "true"
                _import_children(xml_child, node.node_id)

            elif tag == "response":
                node = conv.nodes.add()
                node.node_id = allocate_node_id(conv)
                node.parent_id = parent_id
                node.node_type = 'RESPONSE'
                node.sort_order = sort_idx
                node.response_text = xml_child.get("text", "")
                node.chosen_script = _get_ci(xml_child, "chosenscriptfile")
                node.script_file = _get_ci(xml_child, "scriptfile")
                node.conversation_end = _get_ci(xml_child, "conversationend") == "true"
                node.tag_jump = _get_ci(xml_child, "tagjump")
                node.tag_index = _get_ci(xml_child, "tagindex")
                node.only_if_xman = _get_ci(xml_child, "onlyif_xman") == "true"
                node.only_if_brotherhood = _get_ci(xml_child, "onlyif_brotherhood") == "true"
                # XML1-only response attrs
                td = _get_ci(xml_child, "timedelay")
                node.time_delay = int(td) if td else 0
                node.run_without_user = _get_ci(xml_child, "runwithoutuser") == "true"
                _import_children(xml_child, node.node_id)

            elif tag == "name":
                # Skip <name> elements (handled by participant)
                continue
            else:
                # Unknown tag, skip
                continue

            sort_idx += 1

    # Kick off the recursive import from the root element
    _import_children(root_element, -1)


# ---------------------------------------------------------------------------
# Registration
# ---------------------------------------------------------------------------

_classes = (
    MM_ConversationNode,
    MM_ConversationData,
)


def register():
    for cls in _classes:
        bpy.utils.register_class(cls)

    bpy.types.Scene.mm_conversations = CollectionProperty(type=MM_ConversationData)
    bpy.types.Scene.mm_conversations_index = IntProperty(default=0)


def unregister():
    del bpy.types.Scene.mm_conversations_index
    del bpy.types.Scene.mm_conversations

    for cls in reversed(_classes):
        bpy.utils.unregister_class(cls)
