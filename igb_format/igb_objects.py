"""Python representations of deserialized IGB objects."""


class IGBObject:
    """Represents a deserialized IGB object (from igObjectDirEntry).

    Stores the meta-object type and a dictionary of field values
    indexed by slot number and by field short name.
    """

    __slots__ = ('index', 'meta_object', 'fields_by_slot', 'fields_by_name', '_raw_fields')

    def __init__(self, index, meta_object):
        self.index = index
        self.meta_object = meta_object
        self.fields_by_slot = {}  # slot -> value
        self.fields_by_name = {}  # short_name (bytes) -> value
        self._raw_fields = []     # list of (slot, value, field_descriptor)

    @property
    def type_name(self):
        return self.meta_object.name if self.meta_object else b"Unknown"

    def get(self, name, default=None):
        """Get a field value by short name (bytes)."""
        return self.fields_by_name.get(name, default)

    def get_slot(self, slot, default=None):
        """Get a field value by slot number."""
        return self.fields_by_slot.get(slot, default)

    def is_type(self, class_name):
        """Check if this object is of or inherits from the given type."""
        if self.meta_object is None:
            return False
        return self.meta_object.is_subclass_of(class_name)

    def get_inheritance_chain(self):
        """Get the full class hierarchy for this object."""
        if self.meta_object is None:
            return []
        return self.meta_object.get_inheritance_chain()

    def __repr__(self):
        return (
            f"IGBObject({self.index}, {self.type_name!r}, "
            f"fields={len(self.fields_by_slot)})"
        )


class IGBMemoryBlock:
    """Represents raw memory data (from igMemoryDirEntry).

    These are typically vertex buffers, index buffers, image data, etc.
    """

    __slots__ = (
        'index', 'data', 'mem_size', 'mem_type_index', 'mem_type_name',
        'ref_counted', 'alignment_type_index', 'memory_pool_handle',
    )

    def __init__(self, index, mem_size, mem_type_index=None, mem_type_name=None,
                 ref_counted=False, alignment_type_index=-1, memory_pool_handle=-1):
        self.index = index
        self.data = None  # raw bytes, set during readMemoryRefs
        self.mem_size = mem_size
        self.mem_type_index = mem_type_index
        self.mem_type_name = mem_type_name
        self.ref_counted = ref_counted
        self.alignment_type_index = alignment_type_index
        self.memory_pool_handle = memory_pool_handle

    def __repr__(self):
        type_str = self.mem_type_name or f"typeIdx={self.mem_type_index}"
        return f"IGBMemoryBlock({self.index}, size={self.mem_size}, type={type_str})"


class DirEntry:
    """Parsed directory entry (either object or memory type)."""

    __slots__ = ('index', 'is_object', 'type_index', 'fields')

    def __init__(self, index, is_object, type_index, fields):
        self.index = index
        self.is_object = is_object
        self.type_index = type_index
        self.fields = fields  # list of (slot, value, field_info) tuples
