"""PySide6 Conversation Editor — external window for game-style dialogue editing.

Launched from Blender via modal operator. Reads/writes directly to Blender
PropertyGroups. Qt event loop pumped by Blender modal timer.
"""

import os
import sys
from pathlib import Path

# Ensure bundled deps directory is on sys.path for PySide6
def _find_deps():
    local = Path(__file__).parent / "deps"
    if local.exists():
        return str(local)
    source = Path(r"F:\Projects\XMenLegends\Alchemy Engine IGB Format"
                  r"\igb_blender\mapmaker\deps")
    if source.exists():
        return str(source)
    return str(local)

_deps_dir = _find_deps()
if _deps_dir not in sys.path:
    sys.path.insert(0, _deps_dir)

try:
    from PySide6.QtWidgets import (
        QApplication, QMainWindow, QWidget, QSplitter, QTreeWidget,
        QTreeWidgetItem, QVBoxLayout, QHBoxLayout, QFormLayout,
        QLabel, QLineEdit, QCheckBox, QFrame, QScrollArea, QMenu,
        QSizePolicy, QPushButton, QComboBox, QAbstractItemView,
    )
    from PySide6.QtCore import Qt, QSize, Signal
    from PySide6.QtGui import QPixmap, QFont, QIcon, QColor, QPalette, QAction
    HAS_PYSIDE6 = True
except ImportError:
    HAS_PYSIDE6 = False


# ---------------------------------------------------------------------------
# Stylesheet — dark theme inspired by X-Men Legends UI
# ---------------------------------------------------------------------------

STYLESHEET = """
QMainWindow {
    background-color: #1a1a2e;
}
QSplitter {
    background-color: #1a1a2e;
}
QSplitter::handle {
    background-color: #0f3460;
    width: 3px;
}
QTreeWidget {
    background-color: #16213e;
    color: #d0d0d0;
    border: 1px solid #0f3460;
    font-size: 13px;
    outline: none;
}
QTreeWidget::item {
    padding: 3px 4px;
    border-bottom: 1px solid #1a1a3e;
}
QTreeWidget::item:selected {
    background-color: #0f3460;
    color: #ffffff;
}
QTreeWidget::item:hover {
    background-color: #1a2a5e;
}
QTreeWidget::branch {
    background-color: #16213e;
}
QTreeWidget QHeaderView::section {
    background-color: #0f3460;
    color: #e0c040;
    font-weight: bold;
    padding: 4px;
    border: none;
}
QScrollArea {
    border: none;
    background-color: #1a1a2e;
}
QLabel {
    color: #d0d0d0;
}
QLineEdit {
    background-color: #16213e;
    color: #e8e8e8;
    border: 1px solid #0f3460;
    border-radius: 3px;
    padding: 4px 6px;
    font-size: 13px;
}
QLineEdit:focus {
    border: 1px solid #e0c040;
}
QCheckBox {
    color: #d0d0d0;
    spacing: 6px;
}
QCheckBox::indicator {
    width: 16px;
    height: 16px;
}
QFrame#npc_box {
    background-color: #16213e;
    border: 2px solid #0f3460;
    border-radius: 8px;
    padding: 8px;
}
QFrame#brotherhood_box {
    background-color: #2a1a0a;
    border: 1px solid #804020;
    border-radius: 6px;
    padding: 6px;
}
QFrame#response_frame {
    background-color: #121228;
    border: 1px solid #0f3460;
    border-radius: 6px;
    padding: 6px;
}
QFrame#follow_up_box {
    background-color: #162e16;
    border: 2px solid #1a5e1a;
    border-radius: 8px;
    padding: 8px;
}
QFrame#props_frame {
    background-color: #16213e;
    border: 1px solid #0f3460;
    border-radius: 6px;
    padding: 8px;
}
QPushButton {
    background-color: #0f3460;
    color: #e0c040;
    border: 1px solid #1a4a80;
    border-radius: 4px;
    padding: 4px 12px;
    font-weight: bold;
}
QPushButton:hover {
    background-color: #1a4a80;
}
QPushButton:pressed {
    background-color: #0a2a50;
}
QComboBox {
    background-color: #16213e;
    color: #e8e8e8;
    border: 1px solid #0f3460;
    border-radius: 3px;
    padding: 4px 6px;
}
"""


# ---------------------------------------------------------------------------
# Node type icons (unicode symbols for tree display)
# ---------------------------------------------------------------------------

NODE_ICONS = {
    'START_CONDITION': '\u25B6',   # ▶
    'PARTICIPANT':     '\U0001F464',  # 👤
    'LINE':            '\U0001F4AC',  # 💬
    'RESPONSE':        '\u25CB',   # ○
}

NODE_COLORS = {
    'START_CONDITION': '#80c040',
    'PARTICIPANT':     '#40a0e0',
    'LINE':            '#e0c040',
    'RESPONSE':        '#b0b0b0',
}


# ---------------------------------------------------------------------------
# Helper: get bpy conversation data
# ---------------------------------------------------------------------------

def _get_conv_data(scene, conv_index):
    """Safely get conversation data from Blender scene."""
    if conv_index < 0 or conv_index >= len(scene.mm_conversations):
        return None
    return scene.mm_conversations[conv_index]


# ---------------------------------------------------------------------------
# Main editor window
# ---------------------------------------------------------------------------

class ConversationEditorWindow(QMainWindow):
    """External conversation editor with game-style dialogue preview."""

    def __init__(self, scene, conv_index, hud_cache_dir=None):
        super().__init__()
        self._scene = scene
        self._conv_index = conv_index
        self._hud_cache_dir = hud_cache_dir or str(Path(__file__).parent / "hud_cache")
        self._hud_pixmaps = {}  # cache loaded HUD images
        self._updating = False  # prevent recursion during sync
        self._prop_widgets = {}  # current property widgets

        conv = _get_conv_data(scene, conv_index)
        title = f"Conversation Editor"
        if conv:
            title = f"Conversation: {conv.conv_name}"

        self.setWindowTitle(title)
        self.setMinimumSize(1100, 650)
        self.resize(1280, 720)
        self.setStyleSheet(STYLESHEET)

        # Keep on top of Blender
        self.setWindowFlags(self.windowFlags() | Qt.WindowStaysOnTopHint)

        self._build_ui()
        self._populate_tree()

    # ----- UI Construction -----

    def _build_ui(self):
        """Build the three-pane layout."""
        central = QWidget()
        self.setCentralWidget(central)
        main_layout = QVBoxLayout(central)
        main_layout.setContentsMargins(4, 4, 4, 4)

        # Toolbar
        toolbar = QHBoxLayout()
        self._btn_add_line = QPushButton("+ Line")
        self._btn_add_response = QPushButton("+ Response")
        self._btn_delete = QPushButton("Delete")
        self._btn_add_line.clicked.connect(self._on_add_line)
        self._btn_add_response.clicked.connect(self._on_add_response)
        self._btn_delete.clicked.connect(self._on_delete_node)
        toolbar.addWidget(self._btn_add_line)
        toolbar.addWidget(self._btn_add_response)
        toolbar.addWidget(self._btn_delete)
        toolbar.addStretch()

        # Close button
        btn_close = QPushButton("Close")
        btn_close.clicked.connect(self.close)
        toolbar.addWidget(btn_close)

        main_layout.addLayout(toolbar)

        # Splitter
        splitter = QSplitter(Qt.Horizontal)

        # Left: Tree
        self._tree = QTreeWidget()
        self._tree.setHeaderLabels(["Node", "Info"])
        self._tree.setColumnWidth(0, 280)
        self._tree.setColumnWidth(1, 120)
        self._tree.setSelectionMode(QAbstractItemView.SingleSelection)
        self._tree.setContextMenuPolicy(Qt.CustomContextMenu)
        self._tree.customContextMenuRequested.connect(self._on_tree_context_menu)
        self._tree.currentItemChanged.connect(self._on_tree_selection_changed)
        self._tree.setMinimumWidth(300)

        # Center: Preview
        preview_scroll = QScrollArea()
        preview_scroll.setWidgetResizable(True)
        self._preview_container = QWidget()
        self._preview_layout = QVBoxLayout(self._preview_container)
        self._preview_layout.setAlignment(Qt.AlignTop)
        preview_scroll.setWidget(self._preview_container)
        preview_scroll.setMinimumWidth(400)

        # Right: Properties
        props_scroll = QScrollArea()
        props_scroll.setWidgetResizable(True)
        self._props_container = QWidget()
        self._props_layout = QVBoxLayout(self._props_container)
        self._props_layout.setAlignment(Qt.AlignTop)
        props_scroll.setWidget(self._props_container)
        props_scroll.setMinimumWidth(280)

        splitter.addWidget(self._tree)
        splitter.addWidget(preview_scroll)
        splitter.addWidget(props_scroll)
        splitter.setSizes([320, 480, 320])

        main_layout.addWidget(splitter, 1)

    # ----- Tree Population -----

    def _populate_tree(self):
        """Build QTreeWidget from conversation PropertyGroup data."""
        self._tree.clear()
        conv = _get_conv_data(self._scene, self._conv_index)
        if not conv:
            return

        # Import here to avoid circular at module level
        from .conversation import get_children, parse_speaker_text, get_node_indicators

        # Build tree recursively
        node_items = {}  # node_id -> QTreeWidgetItem

        def _build(parent_item, parent_id):
            children = get_children(conv, parent_id)
            for node in children:
                label = self._get_node_label(node)
                info = self._get_node_info(node)

                item = QTreeWidgetItem()
                item.setText(0, label)
                item.setText(1, info)
                item.setData(0, Qt.UserRole, node.node_id)

                # Color based on type
                color = NODE_COLORS.get(node.node_type, '#d0d0d0')
                item.setForeground(0, QColor(color))
                item.setForeground(1, QColor('#808080'))

                if parent_item is None:
                    self._tree.addTopLevelItem(item)
                else:
                    parent_item.addChild(item)

                node_items[node.node_id] = item
                _build(item, node.node_id)

        _build(None, -1)

        # Expand all
        self._tree.expandAll()

        # Select the node matching Blender's current selection
        if conv.nodes_index >= 0 and conv.nodes_index < len(conv.nodes):
            selected_node = conv.nodes[conv.nodes_index]
            if selected_node.node_id in node_items:
                self._tree.setCurrentItem(node_items[selected_node.node_id])

    def _get_node_label(self, node):
        """Build display label for tree item."""
        from .conversation import parse_speaker_text

        icon = NODE_ICONS.get(node.node_type, '')

        if node.node_type == 'START_CONDITION':
            cond = node.condition_script
            if cond:
                # Show just the filename
                cond = os.path.basename(cond)
            label = f"{icon} Start Condition"
            if cond:
                label += f": {cond}"
            return label

        elif node.node_type == 'PARTICIPANT':
            return f"{icon} {node.participant_name}"

        elif node.node_type == 'LINE':
            speaker, dialogue = parse_speaker_text(node.text)
            if not speaker and not dialogue:
                return f"{icon} (empty line)"
            name = speaker or "NPC"
            text = dialogue[:45] + "..." if len(dialogue) > 45 else dialogue
            return f"{icon} {name}: {text}"

        elif node.node_type == 'RESPONSE':
            text = node.response_text.strip()
            if text == '%BLANK%':
                return f"\u25B7 (auto-advance)"  # ▷
            _, resp = parse_speaker_text(text)
            if not resp:
                resp = text
            resp = resp[:45] + "..." if len(resp) > 45 else resp
            return f"{icon} {resp}"

        return f"{icon} {node.node_type}"

    def _get_node_info(self, node):
        """Build info column text with indicator symbols."""
        from .conversation import get_node_indicators
        ind = get_node_indicators(node)
        parts = []
        if ind['has_sound']:
            parts.append('\U0001F50A')  # 🔊
        if ind['has_end']:
            parts.append('\u2716')  # ✖
        if ind['has_tag_jump']:
            parts.append('\u2192')  # →
        if ind['has_tag_index']:
            parts.append('\U0001F516')  # 🔖
        if ind['has_faction'] == 'xmen':
            parts.append('[X]')
        elif ind['has_faction'] == 'brotherhood':
            parts.append('[B]')
        if ind['has_brotherhood_variant']:
            parts.append('\u0042\u0332')  # B̲
        return ' '.join(parts)

    # ----- Selection Changed -----

    def _on_tree_selection_changed(self, current, previous):
        """Handle tree selection — update preview and properties."""
        if self._updating or current is None:
            return

        node_id = current.data(0, Qt.UserRole)
        if node_id is None:
            return

        conv = _get_conv_data(self._scene, self._conv_index)
        if not conv:
            return

        # Sync Blender selection
        from .conversation import find_node_index
        blender_idx = find_node_index(conv, node_id)
        if blender_idx >= 0:
            # Find the flat display index for this node_id
            from .conversation import get_flat_display_order
            display_order = get_flat_display_order(conv)
            for display_idx, (display_node, _depth) in enumerate(display_order):
                if display_node.node_id == node_id:
                    conv.nodes_index = display_idx
                    break

        # Update preview and properties
        self._update_preview(node_id)
        self._update_properties(node_id)

    # ----- Dialogue Preview -----

    def _update_preview(self, node_id):
        """Update the center dialogue preview pane."""
        # Clear existing
        self._clear_layout(self._preview_layout)

        conv = _get_conv_data(self._scene, self._conv_index)
        if not conv:
            return

        from .conversation import find_node_by_id, get_conversation_context

        node = find_node_by_id(conv, node_id)
        if not node:
            return

        ctx = get_conversation_context(conv, node)
        mode = ctx['mode']

        if mode == 'line':
            self._draw_line_preview(ctx, conv)
        elif mode == 'response':
            self._draw_response_preview(ctx, conv)
        elif mode == 'start_condition':
            self._draw_start_condition_preview(node)
        elif mode == 'participant':
            self._draw_participant_preview(node)

    def _draw_line_preview(self, ctx, conv):
        """Draw NPC dialogue line preview."""
        # NPC Speech Box
        npc_box = QFrame()
        npc_box.setObjectName("npc_box")
        npc_layout = QVBoxLayout(npc_box)

        # Header: HUD portrait + speaker name + sound icon
        header = QHBoxLayout()

        # HUD portrait
        hud_pixmap = self._get_hud_pixmap(conv.hud_head)
        if hud_pixmap:
            portrait = QLabel()
            portrait.setPixmap(hud_pixmap.scaled(64, 64, Qt.KeepAspectRatio, Qt.SmoothTransformation))
            portrait.setFixedSize(68, 68)
            header.addWidget(portrait)

        # Speaker name
        speaker = ctx.get('speaker', 'NPC')
        name_label = QLabel(speaker or "NPC")
        name_font = QFont()
        name_font.setPointSize(16)
        name_font.setBold(True)
        name_label.setFont(name_font)
        name_label.setStyleSheet("color: #e0c040;")
        header.addWidget(name_label)
        header.addStretch()

        # Sound icon
        if ctx.get('has_sound'):
            sound_label = QLabel('\U0001F50A')  # 🔊
            sound_label.setFont(QFont("Segoe UI Emoji", 14))
            sound_label.setToolTip(ctx.get('sound_path', ''))
            header.addWidget(sound_label)

        npc_layout.addLayout(header)

        # Dialogue text
        dialogue = ctx.get('dialogue', '')
        if dialogue:
            text_label = QLabel(dialogue)
            text_label.setWordWrap(True)
            text_label.setStyleSheet("color: #e8e8e8; font-size: 14px; padding: 8px 4px;")
            npc_layout.addWidget(text_label)

        # Brotherhood variant
        dialogue_b = ctx.get('dialogue_b', '')
        if dialogue_b:
            bro_box = QFrame()
            bro_box.setObjectName("brotherhood_box")
            bro_layout = QVBoxLayout(bro_box)

            bro_header = QLabel("\u2694 Brotherhood Variant")  # ⚔
            bro_header.setStyleSheet("color: #c08040; font-weight: bold; font-size: 12px;")
            bro_layout.addWidget(bro_header)

            bro_text = QLabel(dialogue_b)
            bro_text.setWordWrap(True)
            bro_text.setStyleSheet("color: #d0b070; font-size: 13px;")
            bro_layout.addWidget(bro_text)

            npc_layout.addWidget(bro_box)

        self._preview_layout.addWidget(npc_box)

        # Player Choices
        responses = ctx.get('responses', [])
        if responses:
            self._draw_responses_section(responses)

    def _draw_response_preview(self, ctx, conv):
        """Draw response preview: parent NPC speech + response list + follow-up."""
        # Parent NPC speech
        if ctx.get('parent_speaker') or ctx.get('parent_dialogue'):
            parent_box = QFrame()
            parent_box.setObjectName("npc_box")
            parent_layout = QVBoxLayout(parent_box)

            # Header with portrait
            header = QHBoxLayout()
            hud_pixmap = self._get_hud_pixmap(conv.hud_head)
            if hud_pixmap:
                portrait = QLabel()
                portrait.setPixmap(hud_pixmap.scaled(48, 48, Qt.KeepAspectRatio, Qt.SmoothTransformation))
                portrait.setFixedSize(52, 52)
                header.addWidget(portrait)

            name_label = QLabel(ctx.get('parent_speaker', 'NPC'))
            name_label.setStyleSheet("color: #e0c040; font-weight: bold; font-size: 14px;")
            header.addWidget(name_label)
            header.addStretch()
            parent_layout.addLayout(header)

            dialogue = ctx.get('parent_dialogue', '')
            if dialogue:
                text_label = QLabel(dialogue)
                text_label.setWordWrap(True)
                text_label.setStyleSheet("color: #c0c0c0; font-size: 13px; padding: 4px;")
                parent_layout.addWidget(text_label)

            self._preview_layout.addWidget(parent_box)

        # Response list
        responses = ctx.get('responses', [])
        if responses:
            self._draw_responses_section(responses)

        # Follow-up NPC speech
        if ctx.get('follow_up_speaker') or ctx.get('follow_up_dialogue'):
            fu_box = QFrame()
            fu_box.setObjectName("follow_up_box")
            fu_layout = QVBoxLayout(fu_box)

            header = QHBoxLayout()
            hud_pixmap = self._get_hud_pixmap(conv.hud_head)
            if hud_pixmap:
                portrait = QLabel()
                portrait.setPixmap(hud_pixmap.scaled(48, 48, Qt.KeepAspectRatio, Qt.SmoothTransformation))
                portrait.setFixedSize(52, 52)
                header.addWidget(portrait)

            name_label = QLabel(ctx.get('follow_up_speaker', 'NPC'))
            name_label.setStyleSheet("color: #40c040; font-weight: bold; font-size: 14px;")
            header.addWidget(name_label)
            header.addStretch()

            if ctx.get('has_sound'):
                sound_label = QLabel('\U0001F50A')
                sound_label.setFont(QFont("Segoe UI Emoji", 14))
                sound_label.setToolTip(ctx.get('sound_path', ''))
                header.addWidget(sound_label)

            fu_layout.addLayout(header)

            dialogue = ctx.get('follow_up_dialogue', '')
            if dialogue:
                text_label = QLabel(dialogue)
                text_label.setWordWrap(True)
                text_label.setStyleSheet("color: #c0e0c0; font-size: 13px; padding: 4px;")
                fu_layout.addWidget(text_label)

            self._preview_layout.addWidget(fu_box)

    def _draw_responses_section(self, responses):
        """Draw the player choices section."""
        resp_frame = QFrame()
        resp_frame.setObjectName("response_frame")
        resp_layout = QVBoxLayout(resp_frame)

        header = QLabel("Player Choices:")
        header.setStyleSheet("color: #a0a0c0; font-weight: bold; font-size: 12px; padding-bottom: 4px;")
        resp_layout.addWidget(header)

        for i, resp in enumerate(responses, 1):
            row = QHBoxLayout()

            # Selection indicator
            if resp.get('is_selected'):
                bullet = '\u25CF'  # ●
                color = '#ffffff'
                bg = 'background-color: #0f3460; border-radius: 4px; padding: 3px 6px;'
            elif resp.get('is_blank'):
                bullet = '\u25B7'  # ▷
                color = '#606060'
                bg = 'padding: 3px 6px;'
            else:
                bullet = '\u25CB'  # ○
                color = '#b8b8b8'
                bg = 'padding: 3px 6px;'

            text = resp.get('text', '')
            resp_label = QLabel(f"{bullet}  {i}. {text}")
            resp_label.setStyleSheet(f"color: {color}; font-size: 13px; {bg}")
            resp_label.setWordWrap(True)
            row.addWidget(resp_label, 1)

            # Indicator icons
            indicators = []
            if resp.get('faction') == 'xmen':
                indicators.append(('[X]', '#4080e0'))
            elif resp.get('faction') == 'brotherhood':
                indicators.append(('[B]', '#c06030'))
            if resp.get('tag_jump'):
                indicators.append(('\u2192', '#40c0c0'))  # →
            if resp.get('is_end'):
                indicators.append(('\u2716', '#e04040'))  # ✖

            for symbol, ind_color in indicators:
                ind_label = QLabel(symbol)
                ind_label.setStyleSheet(f"color: {ind_color}; font-weight: bold; font-size: 13px;")
                ind_label.setFixedWidth(28)
                ind_label.setAlignment(Qt.AlignCenter)
                row.addWidget(ind_label)

            resp_layout.addLayout(row)

        self._preview_layout.addWidget(resp_frame)

    def _draw_start_condition_preview(self, node):
        """Draw start condition info box."""
        box = QFrame()
        box.setObjectName("npc_box")
        layout = QVBoxLayout(box)

        header = QLabel("\u25B6 Start Condition")
        header.setStyleSheet("color: #80c040; font-weight: bold; font-size: 15px;")
        layout.addWidget(header)

        if node.condition_script:
            layout.addWidget(self._info_row("Script:", node.condition_script))
        layout.addWidget(self._info_row("Run Once:", "Yes" if node.run_once else "No"))

        self._preview_layout.addWidget(box)

    def _draw_participant_preview(self, node):
        """Draw participant info box."""
        box = QFrame()
        box.setObjectName("npc_box")
        layout = QVBoxLayout(box)

        header = QLabel("\U0001F464 Participant")
        header.setStyleSheet("color: #40a0e0; font-weight: bold; font-size: 15px;")
        layout.addWidget(header)

        layout.addWidget(self._info_row("Name:", node.participant_name or "default"))

        self._preview_layout.addWidget(box)

    def _info_row(self, label, value):
        """Create a label: value info row."""
        w = QWidget()
        h = QHBoxLayout(w)
        h.setContentsMargins(0, 2, 0, 2)
        lbl = QLabel(label)
        lbl.setStyleSheet("color: #808090; font-size: 13px;")
        lbl.setFixedWidth(80)
        val = QLabel(str(value))
        val.setStyleSheet("color: #d0d0d0; font-size: 13px;")
        val.setWordWrap(True)
        h.addWidget(lbl)
        h.addWidget(val, 1)
        return w

    # ----- HUD Portrait Loading -----

    def _get_hud_pixmap(self, hud_name):
        """Load and cache HUD head portrait as QPixmap."""
        if not hud_name:
            return None

        if hud_name in self._hud_pixmaps:
            return self._hud_pixmaps[hud_name]

        png_path = os.path.join(self._hud_cache_dir, f"{hud_name}.png")
        if not os.path.exists(png_path):
            self._hud_pixmaps[hud_name] = None
            return None

        pixmap = QPixmap(png_path)
        if pixmap.isNull():
            self._hud_pixmaps[hud_name] = None
            return None

        self._hud_pixmaps[hud_name] = pixmap
        return pixmap

    # ----- Properties Panel -----

    def _update_properties(self, node_id):
        """Update the right-side properties panel for selected node."""
        self._clear_layout(self._props_layout)
        self._prop_widgets = {}

        conv = _get_conv_data(self._scene, self._conv_index)
        if not conv:
            return

        from .conversation import find_node_by_id
        node = find_node_by_id(conv, node_id)
        if not node:
            return

        # Title
        props_frame = QFrame()
        props_frame.setObjectName("props_frame")
        form = QVBoxLayout(props_frame)

        type_label = QLabel(f"Node: {node.node_type}")
        type_label.setStyleSheet("color: #e0c040; font-weight: bold; font-size: 14px; padding-bottom: 6px;")
        form.addWidget(type_label)

        # Build fields based on type
        if node.node_type == 'START_CONDITION':
            self._add_text_field(form, node, 'condition_script', "Condition Script")
            self._add_bool_field(form, node, 'run_once', "Run Once")

        elif node.node_type == 'PARTICIPANT':
            self._add_text_field(form, node, 'participant_name', "Name")

        elif node.node_type == 'LINE':
            self._add_text_field(form, node, 'text', "Text")
            self._add_text_field(form, node, 'sound_to_play', "Sound File")
            self._add_text_field(form, node, 'text_b', "Text (Brotherhood)")
            self._add_text_field(form, node, 'sound_to_play_b', "Sound (Brotherhood)")
            self._add_text_field(form, node, 'tag_index', "Tag Index")

        elif node.node_type == 'RESPONSE':
            self._add_text_field(form, node, 'response_text', "Response Text")
            self._add_text_field(form, node, 'chosen_script', "Chosen Script")
            self._add_text_field(form, node, 'script_file', "Script File")
            self._add_bool_field(form, node, 'conversation_end', "End Conversation")
            self._add_text_field(form, node, 'tag_jump', "Tag Jump")
            self._add_text_field(form, node, 'tag_index', "Tag Index")
            self._add_bool_field(form, node, 'only_if_xman', "X-Men Only")
            self._add_bool_field(form, node, 'only_if_brotherhood', "Brotherhood Only")

        self._props_layout.addWidget(props_frame)

        # Add stretch at bottom
        self._props_layout.addStretch()

    def _add_text_field(self, layout, node, prop_name, label):
        """Add a text input field that syncs to PropertyGroup."""
        lbl = QLabel(label)
        lbl.setStyleSheet("color: #808090; font-size: 12px; padding-top: 4px;")
        layout.addWidget(lbl)

        edit = QLineEdit()
        edit.setText(getattr(node, prop_name, ''))
        edit.textChanged.connect(
            lambda text, n=node, p=prop_name: self._on_prop_changed(n, p, text)
        )
        layout.addWidget(edit)
        self._prop_widgets[prop_name] = edit

    def _add_bool_field(self, layout, node, prop_name, label):
        """Add a checkbox field that syncs to PropertyGroup."""
        cb = QCheckBox(label)
        cb.setChecked(getattr(node, prop_name, False))
        cb.stateChanged.connect(
            lambda state, n=node, p=prop_name: self._on_prop_bool_changed(n, p, state)
        )
        layout.addWidget(cb)
        self._prop_widgets[prop_name] = cb

    def _on_prop_changed(self, node, prop_name, value):
        """Handle text property change — write to Blender PropertyGroup."""
        if self._updating:
            return
        self._updating = True
        try:
            setattr(node, prop_name, value)
            # Refresh tree label for this node
            self._refresh_current_tree_item(node)
            # Refresh preview
            self._update_preview(node.node_id)
        finally:
            self._updating = False

    def _on_prop_bool_changed(self, node, prop_name, state):
        """Handle bool property change."""
        if self._updating:
            return
        self._updating = True
        try:
            setattr(node, prop_name, bool(state))
            self._refresh_current_tree_item(node)
            self._update_preview(node.node_id)
        finally:
            self._updating = False

    def _refresh_current_tree_item(self, node):
        """Refresh the label/info of the currently selected tree item."""
        item = self._tree.currentItem()
        if item and item.data(0, Qt.UserRole) == node.node_id:
            item.setText(0, self._get_node_label(node))
            item.setText(1, self._get_node_info(node))

    # ----- Node Operations -----

    def _on_add_line(self):
        """Add a new LINE node under the selected node."""
        self._add_node('LINE')

    def _on_add_response(self):
        """Add a new RESPONSE node under the selected node."""
        self._add_node('RESPONSE')

    def _add_node(self, node_type):
        """Add a new child node under the selected node."""
        conv = _get_conv_data(self._scene, self._conv_index)
        if not conv:
            return

        item = self._tree.currentItem()
        if not item:
            return

        parent_id = item.data(0, Qt.UserRole)
        if parent_id is None:
            return

        from .conversation import allocate_node_id, get_next_sort_order

        new_node = conv.nodes.add()
        new_node.node_id = allocate_node_id(conv)
        new_node.parent_id = parent_id
        new_node.node_type = node_type
        new_node.sort_order = get_next_sort_order(conv, parent_id)

        if node_type == 'LINE':
            new_node.text = "%NPC%: "
        elif node_type == 'RESPONSE':
            new_node.response_text = "New response"

        # Rebuild tree
        self._populate_tree()

    def _on_delete_node(self):
        """Delete the selected node and its descendants."""
        conv = _get_conv_data(self._scene, self._conv_index)
        if not conv:
            return

        item = self._tree.currentItem()
        if not item:
            return

        node_id = item.data(0, Qt.UserRole)
        if node_id is None:
            return

        from .conversation import remove_node_and_descendants
        remove_node_and_descendants(conv, node_id)

        # Rebuild tree
        self._populate_tree()

    # ----- Context Menu -----

    def _on_tree_context_menu(self, pos):
        """Show right-click context menu on tree."""
        item = self._tree.itemAt(pos)
        if not item:
            return

        menu = QMenu(self)
        menu.setStyleSheet("""
            QMenu {
                background-color: #16213e;
                color: #d0d0d0;
                border: 1px solid #0f3460;
            }
            QMenu::item:selected {
                background-color: #0f3460;
            }
        """)

        add_line_action = menu.addAction("\U0001F4AC Add Line")
        add_resp_action = menu.addAction("\u25CB Add Response")
        menu.addSeparator()
        delete_action = menu.addAction("\u2716 Delete Node")

        action = menu.exec_(self._tree.viewport().mapToGlobal(pos))
        if action == add_line_action:
            self._on_add_line()
        elif action == add_resp_action:
            self._on_add_response()
        elif action == delete_action:
            self._on_delete_node()

    # ----- Layout Helpers -----

    def _clear_layout(self, layout):
        """Remove all widgets from a layout."""
        while layout.count():
            item = layout.takeAt(0)
            widget = item.widget()
            if widget:
                widget.deleteLater()
            elif item.layout():
                self._clear_layout(item.layout())

    # ----- Keyboard Shortcuts -----

    def keyPressEvent(self, event):
        """Handle keyboard shortcuts."""
        if event.modifiers() == Qt.ControlModifier:
            if event.key() == Qt.Key_N:
                self._on_add_line()
                return
            elif event.key() == Qt.Key_R:
                self._on_add_response()
                return
            elif event.key() == Qt.Key_W:
                self.close()
                return
        elif event.key() == Qt.Key_Delete:
            self._on_delete_node()
            return
        super().keyPressEvent(event)
