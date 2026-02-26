"""NPC Stat Editor — PySide6 external window for browsing/editing npcstat.engb.

Works directly on an ElementTree parsed from decompiled XMLB.
read_xmlb() stores ALL fields as XML attributes on elements (NOT child text).
    <stats name="Blob" charactername="Blob" skin="3001" team="hero"/>
    <talent name="fightstyle_wrestling" level="1"/>

Provides a 3-pane layout: NPC list | field editor | nested blocks.
"""

import copy
import os
import sys
from pathlib import Path

# PySide6 imports (loaded via bundled deps)
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
        QTabWidget, QHeaderView, QMessageBox, QFileDialog, QSpinBox,
        QDoubleSpinBox, QGroupBox,
    )
    from PySide6.QtCore import Qt, QSize, Signal
    from PySide6.QtGui import QFont, QAction, QColor
    HAS_PYSIDE6 = True
except ImportError:
    HAS_PYSIDE6 = False


# ---------------------------------------------------------------------------
# Dark theme stylesheet (matches convo_editor / menu_editor)
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
QLabel#section_header {
    color: #e0c040;
    font-weight: bold;
    font-size: 14px;
    padding: 6px 0 2px 0;
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
QSpinBox, QDoubleSpinBox {
    background-color: #16213e;
    color: #e8e8e8;
    border: 1px solid #0f3460;
    border-radius: 3px;
    padding: 4px 6px;
    font-size: 13px;
}
QSpinBox:focus, QDoubleSpinBox:focus {
    border: 1px solid #e0c040;
}
QComboBox {
    background-color: #16213e;
    color: #e8e8e8;
    border: 1px solid #0f3460;
    border-radius: 3px;
    padding: 4px 6px;
}
QComboBox QAbstractItemView {
    background-color: #16213e;
    color: #e8e8e8;
    selection-background-color: #0f3460;
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
QPushButton#danger_btn {
    background-color: #601020;
    border: 1px solid #802040;
    color: #ff8080;
}
QPushButton#danger_btn:hover {
    background-color: #802040;
}
QTabWidget::pane {
    background-color: #1a1a2e;
    border: 1px solid #0f3460;
}
QTabBar::tab {
    background-color: #16213e;
    color: #d0d0d0;
    padding: 4px 10px;
    border: 1px solid #0f3460;
    border-bottom: none;
}
QTabBar::tab:selected {
    background-color: #0f3460;
    color: #e0c040;
}
QGroupBox {
    color: #e0c040;
    font-weight: bold;
    border: 1px solid #0f3460;
    border-radius: 4px;
    margin-top: 10px;
    padding-top: 14px;
}
QGroupBox::title {
    subcontrol-origin: margin;
    left: 10px;
    padding: 0 4px;
}
QMenuBar {
    background-color: #16213e;
    color: #d0d0d0;
}
QMenuBar::item:selected {
    background-color: #0f3460;
}
QMenu {
    background-color: #16213e;
    color: #d0d0d0;
    border: 1px solid #0f3460;
}
QMenu::item:selected {
    background-color: #0f3460;
}
"""


# ---------------------------------------------------------------------------
# Field category definitions — determines UI layout order and widget types
# ---------------------------------------------------------------------------

NPC_FIELD_CATEGORIES = {
    'Identity': [
        ('name', 'str', 'Internal ID (e.g. Blob)'),
        ('charactername', 'str', 'Display name (e.g. Blob)'),
        ('team', 'enum', 'Team affiliation', ['hero', 'enemy', 'none', 'boss']),
    ],
    'Appearance': [
        ('skin', 'str', 'Skin number (e.g. 3001)'),
        ('characteranims', 'str', 'Animation set (e.g. 30_blob)'),
        ('textureicon', 'int', 'Texture icon index'),
        ('scale_factor', 'float', 'Model scale factor'),
    ],
    'Attributes': [
        ('level', 'int', 'Character level'),
        ('body', 'int', 'Body stat'),
        ('mind', 'int', 'Mind stat'),
        ('strength', 'int', 'Strength stat'),
        ('speed', 'int', 'Speed stat'),
        ('dangerrating', 'int', 'Danger rating'),
    ],
    'Combat': [
        ('specific_attack', 'int', 'Attack power'),
        ('specific_defense', 'int', 'Defense power'),
        ('specific_health', 'int', 'Health points'),
        ('powerstyle', 'str', 'Power style script (e.g. ps_blob)'),
        ('power1', 'str', 'Power slot 1'),
        ('power2', 'str', 'Power slot 2'),
        ('power3', 'str', 'Power slot 3'),
        ('power4', 'str', 'Power slot 4'),
    ],
    'AI / Flags': [
        ('aiforceranged', 'bool', 'Force ranged AI'),
        ('playable', 'bool', 'Is playable character'),
        ('npchealthscale', 'int', 'NPC health scale'),
        ('autospend', 'str', 'Auto-spend profile'),
        ('combolevel', 'int', 'Combo level'),
        ('mutatechance', 'int', 'Mutation chance'),
    ],
    'Audio': [
        ('sounddir', 'str', 'Sound directory (e.g. blob_m)'),
    ],
}

# Nested block types and their field definitions
NESTED_BLOCK_DEFS = {
    'talent': [('name', 'str', 'Talent name'), ('level', 'int', 'Talent level')],
    'BoltOn': [
        ('anim', 'str', 'Animation'),
        ('bolt', 'str', 'Bone attachment point'),
        ('model', 'str', 'Model number'),
        ('slot', 'str', 'Bolt-on slot'),
        ('menuonly', 'str', 'Menu only flag'),
    ],
    'Race': [('name', 'str', 'Race name')],
    'Multipart': [
        ('health', 'int', 'Health'),
        ('hideskin', 'str', 'Hide skin part'),
        ('showskin', 'str', 'Show skin part'),
        ('nonmenuonly', 'str', 'Non-menu only'),
    ],
    'FlyEffect': [
        ('bolt', 'str', 'Bolt attachment'),
        ('effect', 'str', 'Effect name'),
    ],
}

# Flat set of all known nested block tag names (for filtering)
NESTED_BLOCK_TAGS = set(NESTED_BLOCK_DEFS.keys())

# All known scalar field names from NPC_FIELD_CATEGORIES
_KNOWN_FIELDS = set()
for _fields in NPC_FIELD_CATEGORIES.values():
    for _f in _fields:
        _KNOWN_FIELDS.add(_f[0])


# ---------------------------------------------------------------------------
# ET tree helpers — read_xmlb stores ALL fields as XML ATTRIBUTES
# ---------------------------------------------------------------------------

def _et_get(elem, key, default=''):
    """Get a field from an ET.Element (stored as XML attribute by read_xmlb)."""
    return elem.get(key, default)


def _et_set(elem, key, value):
    """Set a field on an ET.Element (as XML attribute)."""
    elem.set(key, str(value))


def _et_remove_field(elem, key):
    """Remove a field from an ET.Element's attributes."""
    if key in elem.attrib:
        del elem.attrib[key]
        return True
    return False


def _npcstat_tree_to_raven_text(root):
    """Convert an npcstat ET tree to Raven text format for compilation.

    read_xmlb stores fields as XML attributes on elements:
        <stats name="Blob" charactername="Blob" skin="3001" team="hero">
          <talent name="fightstyle_wrestling" level="1"/>
        </stats>

    Output format:
        XMLB characters {
           stats {
           charactername = Blob ;
           name = Blob ;
           skin = 3001 ;
           team = hero ;
              talent {
              level = 1 ;
              name = fightstyle_wrestling ;
              }
           }
        }
    """
    lines = [f"XMLB {root.tag} {{"]

    for stats_elem in root:
        if stats_elem.tag != 'stats':
            continue
        lines.append("   stats {")

        # Scalar fields from element attributes (alphabetical, matching game)
        scalar_keys = sorted(k for k in stats_elem.attrib.keys())
        for key in scalar_keys:
            lines.append(f"   {key} = {stats_elem.attrib[key]} ;")

        # Nested blocks (child elements like talent, BoltOn, Race, etc.)
        for child in stats_elem:
            if child.tag in NESTED_BLOCK_TAGS:
                lines.append(f"      {child.tag} {{")
                for key in sorted(child.attrib.keys()):
                    lines.append(f"      {key} = {child.attrib[key]} ;")
                lines.append("      }")
                lines.append("")

        lines.append("   }")
        lines.append("")

    lines.append("")
    lines.append("}")
    lines.append("")
    return "\n".join(lines)


# ===========================================================================
# NPCStatEditorWindow — main PySide6 window
# ===========================================================================

class NPCStatEditorWindow(QMainWindow):
    """External NPC stat editor with 3-pane layout."""

    def __init__(self, npcstat_path, on_save_callback=None):
        """Initialize the NPC Stat Editor.

        Args:
            npcstat_path: Path to the npcstat.engb or herostat.engb file
            on_save_callback: Optional callback(output_path) after successful save
        """
        super().__init__()
        self._npcstat_path = npcstat_path
        self._on_save_callback = on_save_callback
        self._dirty = False
        self._updating = False  # recursion guard
        self._undo_stack = []
        self._redo_stack = []
        self._current_stats_elem = None  # currently selected stats ET.Element
        self._field_widgets = {}  # field_name -> widget

        # Load the npcstat data
        self._load_npcstat(npcstat_path)

        filename = Path(npcstat_path).name
        self.setWindowTitle(f"NPC Stat Editor \u2014 {filename}")
        self.setMinimumSize(1200, 700)
        self.resize(1400, 800)
        self.setStyleSheet(STYLESHEET)
        self.setWindowFlags(self.windowFlags() | Qt.WindowStaysOnTopHint)

        self._build_ui()
        self._populate_npc_list()

    # ------------------------------------------------------------------
    # Data loading
    # ------------------------------------------------------------------

    def _load_npcstat(self, path):
        """Load and decompile npcstat.engb to ET.Element tree."""
        from .xmlb_compile import decompile_xmlb
        self._root = decompile_xmlb(path)
        self._push_undo()

    # ------------------------------------------------------------------
    # Undo / Redo (deep copy of ET tree)
    # ------------------------------------------------------------------

    def _push_undo(self):
        """Snapshot current tree state for undo."""
        self._undo_stack.append(copy.deepcopy(self._root))
        if len(self._undo_stack) > 50:
            self._undo_stack.pop(0)
        self._redo_stack.clear()

    def _undo(self):
        if len(self._undo_stack) <= 1:
            return
        self._redo_stack.append(self._undo_stack.pop())
        self._root = copy.deepcopy(self._undo_stack[-1])
        self._refresh_after_tree_change()

    def _redo(self):
        if not self._redo_stack:
            return
        state = self._redo_stack.pop()
        self._undo_stack.append(state)
        self._root = copy.deepcopy(state)
        self._refresh_after_tree_change()

    def _refresh_after_tree_change(self):
        """Re-populate NPC list and clear field editor after undo/redo."""
        self._current_stats_elem = None
        self._populate_npc_list()
        self._clear_field_editor()
        self._clear_nested_blocks()

    # ------------------------------------------------------------------
    # UI construction
    # ------------------------------------------------------------------

    def _build_ui(self):
        central = QWidget()
        self.setCentralWidget(central)
        main_layout = QVBoxLayout(central)
        main_layout.setContentsMargins(4, 4, 4, 4)

        # Menu bar
        self._build_menu_bar()

        # Main 3-pane splitter
        splitter = QSplitter(Qt.Horizontal)

        # Left pane: NPC list
        left_panel = self._build_npc_list_panel()
        splitter.addWidget(left_panel)

        # Center pane: Field editor (scrollable)
        center_panel = self._build_field_editor_panel()
        splitter.addWidget(center_panel)

        # Right pane: Nested blocks (tabs)
        right_panel = self._build_nested_blocks_panel()
        splitter.addWidget(right_panel)

        splitter.setSizes([280, 500, 350])
        main_layout.addWidget(splitter)

    def _build_menu_bar(self):
        mb = self.menuBar()

        file_menu = mb.addMenu("&File")
        save_action = QAction("&Save", self)
        save_action.setShortcut("Ctrl+S")
        save_action.triggered.connect(self._save)
        file_menu.addAction(save_action)

        save_as_action = QAction("Save &As...", self)
        save_as_action.triggered.connect(self._save_as)
        file_menu.addAction(save_as_action)

        file_menu.addSeparator()
        close_action = QAction("&Close", self)
        close_action.triggered.connect(self.close)
        file_menu.addAction(close_action)

        edit_menu = mb.addMenu("&Edit")
        undo_action = QAction("&Undo", self)
        undo_action.setShortcut("Ctrl+Z")
        undo_action.triggered.connect(self._undo)
        edit_menu.addAction(undo_action)

        redo_action = QAction("&Redo", self)
        redo_action.setShortcut("Ctrl+Shift+Z")
        redo_action.triggered.connect(self._redo)
        edit_menu.addAction(redo_action)

    # -- Left pane: NPC list --

    def _build_npc_list_panel(self):
        panel = QWidget()
        layout = QVBoxLayout(panel)
        layout.setContentsMargins(2, 2, 2, 2)

        # Search
        search_layout = QHBoxLayout()
        search_label = QLabel("Search:")
        self._search_edit = QLineEdit()
        self._search_edit.setPlaceholderText("Filter by name...")
        self._search_edit.textChanged.connect(self._filter_npc_list)
        search_layout.addWidget(search_label)
        search_layout.addWidget(self._search_edit)
        layout.addLayout(search_layout)

        # Team filter
        team_layout = QHBoxLayout()
        team_label = QLabel("Team:")
        self._team_combo = QComboBox()
        self._team_combo.addItems(["All Teams", "hero", "enemy", "none", "boss"])
        self._team_combo.currentTextChanged.connect(self._filter_npc_list)
        team_layout.addWidget(team_label)
        team_layout.addWidget(self._team_combo)
        layout.addLayout(team_layout)

        # NPC tree
        self._npc_tree = QTreeWidget()
        self._npc_tree.setHeaderLabels(["Name", "Team", "Skin"])
        self._npc_tree.setColumnCount(3)
        self._npc_tree.header().setStretchLastSection(False)
        self._npc_tree.header().setSectionResizeMode(0, QHeaderView.Stretch)
        self._npc_tree.header().setSectionResizeMode(1, QHeaderView.ResizeToContents)
        self._npc_tree.header().setSectionResizeMode(2, QHeaderView.ResizeToContents)
        self._npc_tree.setSelectionMode(QAbstractItemView.SingleSelection)
        self._npc_tree.currentItemChanged.connect(self._on_npc_selected)
        layout.addWidget(self._npc_tree)

        # Action buttons
        btn_layout = QHBoxLayout()
        add_btn = QPushButton("+ Add NPC")
        add_btn.clicked.connect(self._add_npc)
        dup_btn = QPushButton("Duplicate")
        dup_btn.clicked.connect(self._duplicate_npc)
        del_btn = QPushButton("- Remove")
        del_btn.setObjectName("danger_btn")
        del_btn.clicked.connect(self._remove_npc)
        btn_layout.addWidget(add_btn)
        btn_layout.addWidget(dup_btn)
        btn_layout.addWidget(del_btn)
        layout.addLayout(btn_layout)

        # Count label
        self._count_label = QLabel("0 NPCs")
        layout.addWidget(self._count_label)

        return panel

    # -- Center pane: Field editor --

    def _build_field_editor_panel(self):
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)

        self._field_container = QWidget()
        self._field_layout = QVBoxLayout(self._field_container)
        self._field_layout.setContentsMargins(8, 8, 8, 8)
        self._field_layout.addStretch()

        scroll.setWidget(self._field_container)
        return scroll

    # -- Right pane: Nested blocks --

    def _build_nested_blocks_panel(self):
        self._blocks_tab = QTabWidget()

        for block_type, fields in NESTED_BLOCK_DEFS.items():
            page = QWidget()
            page_layout = QVBoxLayout(page)
            page_layout.setContentsMargins(4, 4, 4, 4)

            # Block list (tree widget showing entries)
            tree = QTreeWidget()
            headers = [f[0] for f in fields]
            tree.setHeaderLabels(headers)
            tree.setColumnCount(len(headers))
            tree.setSelectionMode(QAbstractItemView.SingleSelection)
            page_layout.addWidget(tree)

            # Store reference
            tree.setProperty("block_type", block_type)

            # Add/Remove buttons
            btn_layout = QHBoxLayout()
            add_btn = QPushButton(f"+ Add {block_type}")
            add_btn.setProperty("block_type", block_type)
            add_btn.clicked.connect(
                lambda checked, bt=block_type: self._add_block_entry(bt))
            del_btn = QPushButton("- Remove")
            del_btn.setObjectName("danger_btn")
            del_btn.setProperty("block_type", block_type)
            del_btn.clicked.connect(
                lambda checked, bt=block_type, t=tree:
                    self._remove_block_entry(bt, t))
            btn_layout.addWidget(add_btn)
            btn_layout.addWidget(del_btn)
            page_layout.addLayout(btn_layout)

            self._blocks_tab.addTab(page, block_type)

        return self._blocks_tab

    # ------------------------------------------------------------------
    # NPC list population and filtering
    # ------------------------------------------------------------------

    def _populate_npc_list(self):
        """Fill the NPC list tree from the ET root."""
        self._npc_tree.clear()
        count = 0
        for stats_elem in self._root:
            if stats_elem.tag != 'stats':
                continue
            name = _et_get(stats_elem, 'name', '(unnamed)')
            char_name = _et_get(stats_elem, 'charactername', '')
            team = _et_get(stats_elem, 'team', '')
            skin = _et_get(stats_elem, 'skin', '')

            display_name = char_name if char_name else name
            item = QTreeWidgetItem([display_name, team, skin])
            # Store reference to ET element via its Python object id
            item.setData(0, Qt.UserRole, id(stats_elem))
            self._npc_tree.addTopLevelItem(item)
            count += 1

        self._count_label.setText(f"{count} NPCs")
        self._filter_npc_list()

    def _filter_npc_list(self):
        """Apply search and team filters to the NPC list."""
        search = self._search_edit.text().lower()
        team_filter = self._team_combo.currentText()
        if team_filter == "All Teams":
            team_filter = ""

        for i in range(self._npc_tree.topLevelItemCount()):
            item = self._npc_tree.topLevelItem(i)
            name = item.text(0).lower()
            team = item.text(1).lower()

            visible = True
            if search and search not in name:
                visible = False
            if team_filter and team != team_filter.lower():
                visible = False

            item.setHidden(not visible)

    def _find_stats_elem_by_id(self, elem_id):
        """Find a stats ET.Element by its Python object id."""
        for stats_elem in self._root:
            if stats_elem.tag == 'stats' and id(stats_elem) == elem_id:
                return stats_elem
        return None

    # ------------------------------------------------------------------
    # NPC selection -> populate field editor + nested blocks
    # ------------------------------------------------------------------

    def _on_npc_selected(self, current, previous):
        if current is None:
            self._current_stats_elem = None
            self._clear_field_editor()
            self._clear_nested_blocks()
            return

        elem_id = current.data(0, Qt.UserRole)
        stats_elem = self._find_stats_elem_by_id(elem_id)
        if stats_elem is None:
            return

        self._current_stats_elem = stats_elem
        self._refresh_field_editor(stats_elem)
        self._refresh_nested_blocks(stats_elem)

    # ------------------------------------------------------------------
    # Field editor
    # ------------------------------------------------------------------

    def _clear_field_editor(self):
        """Remove all widgets from the field editor."""
        self._field_widgets.clear()
        while self._field_layout.count():
            item = self._field_layout.takeAt(0)
            widget = item.widget()
            if widget:
                widget.deleteLater()

    def _refresh_field_editor(self, stats_elem):
        """Rebuild the field editor for the selected NPC."""
        self._updating = True
        self._clear_field_editor()
        self._field_widgets.clear()

        for category, fields in NPC_FIELD_CATEGORIES.items():
            # Section header
            header = QLabel(category)
            header.setObjectName("section_header")
            self._field_layout.addWidget(header)

            form = QFormLayout()
            form.setLabelAlignment(Qt.AlignRight | Qt.AlignVCenter)

            for field_def in fields:
                field_name = field_def[0]
                field_type = field_def[1]
                tooltip = field_def[2] if len(field_def) > 2 else ""
                current_value = _et_get(stats_elem, field_name, '')

                widget = self._create_field_widget(
                    field_name, field_type, field_def, current_value)
                widget.setToolTip(tooltip)
                label = QLabel(f"{field_name}:")
                label.setToolTip(tooltip)
                form.addRow(label, widget)
                self._field_widgets[field_name] = widget

            form_widget = QWidget()
            form_widget.setLayout(form)
            self._field_layout.addWidget(form_widget)

        # Extra fields section — any attributes not in known categories
        extra_fields = {}
        for key, val in stats_elem.attrib.items():
            if key not in _KNOWN_FIELDS:
                extra_fields[key] = val

        if extra_fields:
            header = QLabel("Other Fields")
            header.setObjectName("section_header")
            self._field_layout.addWidget(header)

            form = QFormLayout()
            for fname, fval in sorted(extra_fields.items()):
                widget = QLineEdit(fval)
                widget.editingFinished.connect(
                    lambda fn=fname, w=widget:
                        self._on_field_changed(fn, w.text()))
                form.addRow(QLabel(f"{fname}:"), widget)
                self._field_widgets[fname] = widget

            form_widget = QWidget()
            form_widget.setLayout(form)
            self._field_layout.addWidget(form_widget)

        self._field_layout.addStretch()
        self._updating = False

    def _create_field_widget(self, field_name, field_type, field_def, value):
        """Create an appropriate widget for a field type."""
        if field_type == 'str':
            widget = QLineEdit(value)
            widget.editingFinished.connect(
                lambda fn=field_name, w=widget:
                    self._on_field_changed(fn, w.text()))
            return widget

        elif field_type == 'int':
            widget = QSpinBox()
            widget.setRange(-999999, 999999)
            try:
                widget.setValue(int(value) if value else 0)
            except (ValueError, TypeError):
                widget.setValue(0)
            widget.valueChanged.connect(
                lambda v, fn=field_name:
                    self._on_field_changed(fn, str(v)))
            return widget

        elif field_type == 'float':
            widget = QDoubleSpinBox()
            widget.setRange(-9999.0, 9999.0)
            widget.setDecimals(3)
            try:
                widget.setValue(float(value) if value else 0.0)
            except (ValueError, TypeError):
                widget.setValue(0.0)
            widget.valueChanged.connect(
                lambda v, fn=field_name:
                    self._on_field_changed(fn, str(v)))
            return widget

        elif field_type == 'bool':
            widget = QCheckBox()
            widget.setChecked(
                value.lower() == 'true' if value else False)
            widget.stateChanged.connect(
                lambda state, fn=field_name:
                    self._on_field_changed(
                        fn, 'true' if state else 'false'))
            return widget

        elif field_type == 'enum':
            widget = QComboBox()
            options = field_def[3] if len(field_def) > 3 else []
            widget.addItems(options)
            if value in options:
                widget.setCurrentText(value)
            elif value:
                widget.addItem(value)
                widget.setCurrentText(value)
            widget.currentTextChanged.connect(
                lambda text, fn=field_name:
                    self._on_field_changed(fn, text))
            return widget

        # Fallback: string
        widget = QLineEdit(value)
        widget.editingFinished.connect(
            lambda fn=field_name, w=widget:
                self._on_field_changed(fn, w.text()))
        return widget

    def _on_field_changed(self, field_name, value):
        """Handle a field value change — write to ET element attributes."""
        if self._updating or self._current_stats_elem is None:
            return

        self._push_undo()

        # Remove field if value is empty/default
        if not value or value == '0' or value == '0.0' or value == 'false':
            existing = _et_get(self._current_stats_elem, field_name, '')
            if existing:
                _et_remove_field(self._current_stats_elem, field_name)
        else:
            _et_set(self._current_stats_elem, field_name, value)

        self._dirty = True
        self._update_npc_list_item()

    def _update_npc_list_item(self):
        """Update the currently selected NPC list item text."""
        current = self._npc_tree.currentItem()
        if current is None or self._current_stats_elem is None:
            return
        name = _et_get(self._current_stats_elem, 'name', '(unnamed)')
        char_name = _et_get(self._current_stats_elem, 'charactername', '')
        team = _et_get(self._current_stats_elem, 'team', '')
        skin = _et_get(self._current_stats_elem, 'skin', '')
        current.setText(0, char_name if char_name else name)
        current.setText(1, team)
        current.setText(2, skin)

    # ------------------------------------------------------------------
    # Nested blocks (talents, bolt-ons, races, multipart, fly effect)
    # ------------------------------------------------------------------

    def _clear_nested_blocks(self):
        """Clear all nested block tab trees."""
        for i in range(self._blocks_tab.count()):
            page = self._blocks_tab.widget(i)
            for child in page.findChildren(QTreeWidget):
                child.clear()

    def _refresh_nested_blocks(self, stats_elem):
        """Populate nested block tabs for the selected NPC."""
        self._clear_nested_blocks()

        for i in range(self._blocks_tab.count()):
            page = self._blocks_tab.widget(i)
            block_type = list(NESTED_BLOCK_DEFS.keys())[i]
            fields = NESTED_BLOCK_DEFS[block_type]

            tree = None
            for child in page.findChildren(QTreeWidget):
                tree = child
                break
            if tree is None:
                continue

            # Find all child elements matching this block type
            for block_elem in stats_elem:
                if block_elem.tag != block_type:
                    continue
                # Fields are XML attributes on the block element
                values = []
                for field_def in fields:
                    values.append(block_elem.get(field_def[0], ''))
                item = QTreeWidgetItem(values)
                item.setData(0, Qt.UserRole, id(block_elem))
                # Make editable
                item.setFlags(item.flags() | Qt.ItemIsEditable)
                tree.addTopLevelItem(item)

            # Connect item changed signal for editing
            try:
                tree.itemChanged.disconnect()
            except RuntimeError:
                pass
            tree.itemChanged.connect(
                lambda item, col, bt=block_type, fs=fields:
                    self._on_block_item_changed(item, col, bt, fs))

    def _on_block_item_changed(self, item, column, block_type, fields):
        """Handle inline editing of a nested block entry."""
        if self._updating or self._current_stats_elem is None:
            return

        elem_id = item.data(0, Qt.UserRole)
        for block_elem in self._current_stats_elem:
            if block_elem.tag == block_type and id(block_elem) == elem_id:
                self._push_undo()
                field_name = fields[column][0]
                new_value = item.text(column)
                # Store as XML attribute
                block_elem.set(field_name, new_value)
                self._dirty = True
                return

    def _add_block_entry(self, block_type):
        """Add a new nested block entry to the current NPC."""
        if self._current_stats_elem is None:
            return
        import xml.etree.ElementTree as ET

        self._push_undo()

        # Create new block element with default empty attributes
        block_elem = ET.SubElement(self._current_stats_elem, block_type)
        for field_def in NESTED_BLOCK_DEFS[block_type]:
            block_elem.set(field_def[0], '')

        self._dirty = True
        self._refresh_nested_blocks(self._current_stats_elem)

    def _remove_block_entry(self, block_type, tree):
        """Remove the selected nested block entry."""
        if self._current_stats_elem is None:
            return

        current_item = tree.currentItem()
        if current_item is None:
            return

        elem_id = current_item.data(0, Qt.UserRole)
        for block_elem in list(self._current_stats_elem):
            if block_elem.tag == block_type and id(block_elem) == elem_id:
                self._push_undo()
                self._current_stats_elem.remove(block_elem)
                self._dirty = True
                self._refresh_nested_blocks(self._current_stats_elem)
                return

    # ------------------------------------------------------------------
    # NPC CRUD (add / remove / duplicate)
    # ------------------------------------------------------------------

    def _add_npc(self):
        """Add a new empty NPC stats entry."""
        import xml.etree.ElementTree as ET

        self._push_undo()

        stats_elem = ET.SubElement(self._root, 'stats')
        stats_elem.set('name', 'NewNPC')
        stats_elem.set('charactername', 'New NPC')
        stats_elem.set('team', 'none')
        stats_elem.set('skin', '0001')
        stats_elem.set('characteranims', '128_civilian_male')

        self._dirty = True
        self._populate_npc_list()

        # Select the new entry (last item)
        count = self._npc_tree.topLevelItemCount()
        if count > 0:
            self._npc_tree.setCurrentItem(
                self._npc_tree.topLevelItem(count - 1))

    def _remove_npc(self):
        """Remove the currently selected NPC."""
        if self._current_stats_elem is None:
            return

        name = _et_get(self._current_stats_elem, 'charactername',
                       _et_get(self._current_stats_elem, 'name', '(unnamed)'))

        reply = QMessageBox.question(
            self, "Remove NPC",
            f"Remove '{name}'?\n\nYou can Ctrl+Z to undo.",
            QMessageBox.Yes | QMessageBox.No, QMessageBox.No)

        if reply != QMessageBox.Yes:
            return

        self._push_undo()
        self._root.remove(self._current_stats_elem)
        self._current_stats_elem = None
        self._dirty = True
        self._populate_npc_list()
        self._clear_field_editor()
        self._clear_nested_blocks()

    def _duplicate_npc(self):
        """Duplicate the currently selected NPC."""
        if self._current_stats_elem is None:
            return

        self._push_undo()

        new_elem = copy.deepcopy(self._current_stats_elem)
        # Rename to avoid confusion
        old_name = _et_get(new_elem, 'name', 'NPC')
        _et_set(new_elem, 'name', old_name + '_copy')
        old_charname = _et_get(new_elem, 'charactername', '')
        if old_charname:
            _et_set(new_elem, 'charactername', old_charname + ' (Copy)')

        self._root.append(new_elem)
        self._dirty = True
        self._populate_npc_list()

        # Select the new entry
        count = self._npc_tree.topLevelItemCount()
        if count > 0:
            self._npc_tree.setCurrentItem(
                self._npc_tree.topLevelItem(count - 1))

    # ------------------------------------------------------------------
    # Save
    # ------------------------------------------------------------------

    def _save(self):
        """Save to the original path."""
        self._save_to_path(self._npcstat_path)

    def _save_as(self):
        """Save to a user-chosen path."""
        path, _ = QFileDialog.getSaveFileName(
            self, "Save As",
            str(self._npcstat_path),
            "ENGB Files (*.engb);;All Files (*)")
        if path:
            self._save_to_path(path)

    def _save_to_path(self, output_path):
        """Convert ET tree to Raven text, compile to XMLB, write to disk."""
        try:
            # Generate Raven text format
            raven_text = _npcstat_tree_to_raven_text(self._root)

            # Write text file
            text_path = str(output_path) + '.xml'
            with open(text_path, 'w', encoding='utf-8') as f:
                f.write(raven_text)

            # Compile to XMLB binary
            from .xmlb_compile import compile_xml_to_xmlb
            compile_xml_to_xmlb(text_path, output_path)

            # Clean up text file
            try:
                os.remove(text_path)
            except OSError:
                pass

            self._dirty = False
            self.setWindowTitle(
                f"NPC Stat Editor \u2014 {Path(output_path).name}")

            if self._on_save_callback:
                self._on_save_callback(output_path)

            QMessageBox.information(self, "Saved",
                                   f"Saved to:\n{output_path}")

        except Exception as e:
            QMessageBox.critical(self, "Save Failed",
                                f"Failed to save:\n{e}")

    # ------------------------------------------------------------------
    # Close event
    # ------------------------------------------------------------------

    def closeEvent(self, event):
        if self._dirty:
            reply = QMessageBox.question(
                self, "Unsaved Changes",
                "You have unsaved changes. Close without saving?",
                QMessageBox.Yes | QMessageBox.No, QMessageBox.No)
            if reply != QMessageBox.Yes:
                event.ignore()
                return
        event.accept()
