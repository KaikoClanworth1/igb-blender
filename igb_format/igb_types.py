"""IGB meta-field and meta-object type system parser."""

import struct


class MetaField:
    """Represents a type definition in the IGB meta-field registry.

    Each meta-field defines a named type (e.g., Float, Vec3f, ObjectRef)
    that can be used as a field type in meta-objects.
    """

    __slots__ = ('index', 'name', 'major_version', 'minor_version', 'short_name')

    def __init__(self, index, name, major_version, minor_version):
        self.index = index
        self.name = name  # e.g. b"igFloatMetaField"
        self.major_version = major_version
        self.minor_version = minor_version
        # Derive short name by stripping "ig" prefix and "MetaField" suffix
        short = name
        if short.startswith(b"ig"):
            short = short[2:]
        if short.endswith(b"MetaField"):
            short = short[:-9]
        self.short_name = short  # e.g. b"Float"

    def __repr__(self):
        return f"MetaField({self.index}, {self.short_name!r})"


class MetaObjectField:
    """Represents a single field descriptor within a meta-object.

    Each field has a type (referencing a MetaField), a slot number,
    and a byte size for serialization.
    """

    __slots__ = ('type_index', 'slot', 'size', 'meta_field')

    def __init__(self, type_index, slot, size, meta_field=None):
        self.type_index = type_index
        self.slot = slot
        self.size = size
        self.meta_field = meta_field  # resolved MetaField reference

    @property
    def short_name(self):
        if self.meta_field:
            return self.meta_field.short_name
        return b"Unknown"

    def __repr__(self):
        return f"MetaObjectField(type={self.short_name!r}, slot={self.slot}, size={self.size})"


class MetaObject:
    """Represents a class/type definition in the IGB meta-object registry.

    Each meta-object defines a named class with fields and optional parent class.
    This mirrors the Alchemy Engine's runtime type system.
    """

    __slots__ = (
        'index', 'name', 'major_version', 'minor_version',
        'fields', 'parent_index', 'parent', 'slot_count',
    )

    def __init__(self, index, name, major_version, minor_version, fields,
                 parent_index, slot_count):
        self.index = index
        self.name = name  # e.g. b"igSceneInfo"
        self.major_version = major_version
        self.minor_version = minor_version
        self.fields = fields  # list of MetaObjectField
        self.parent_index = parent_index  # -1 if no parent
        self.parent = None  # resolved parent MetaObject reference
        self.slot_count = slot_count

    def get_inheritance_chain(self):
        """Get the full inheritance chain from this class to root."""
        chain = [self.name]
        obj = self
        while obj.parent is not None:
            chain.append(obj.parent.name)
            obj = obj.parent
        return chain

    def is_subclass_of(self, class_name):
        """Check if this class is or inherits from the given class name."""
        return class_name in self.get_inheritance_chain()

    def __repr__(self):
        parent_str = f", parent={self.parent.name!r}" if self.parent else ""
        return (
            f"MetaObject({self.index}, {self.name!r}, "
            f"fields={len(self.fields)}{parent_str})"
        )


def parse_meta_fields(data, count, endian="<"):
    """Parse the meta-field buffer.

    The meta-field buffer has two parts:
    1. Static entries: count * 12 bytes (nameLen:u32, majorVer:u32, minorVer:u32)
    2. Dynamic data: variable-length null-terminated names

    Args:
        data: bytes/memoryview of the meta-field buffer
        count: number of meta-fields
        endian: byte order character ("<" or ">")

    Returns:
        list of MetaField objects
    """
    static_size = count * 12
    static_buf = data[:static_size]
    dyn_buf = data[static_size:]

    meta_fields = []
    dyn_offset = 0

    for i in range(count):
        offset = i * 12
        name_len, major, minor = struct.unpack_from(endian + "III", static_buf, offset)

        # Extract null-terminated name from dynamic buffer
        # Truncate at first null byte (v8 names may have padding bytes after null)
        raw_name = bytes(dyn_buf[dyn_offset:dyn_offset + name_len])
        null_pos = raw_name.find(b"\0")
        name = raw_name[:null_pos] if null_pos >= 0 else raw_name
        dyn_offset += name_len

        meta_fields.append(MetaField(i, name, major, minor))

    return meta_fields


def parse_meta_objects(data, count, meta_fields, endian="<"):
    """Parse the meta-object buffer.

    The meta-object buffer has two parts:
    1. Static entries: count * 24 bytes each
       (nameLen:u32, majorVer:u32, minorVer:u32, fieldCount:u32, parentIdx:i32, slotCount:u32)
    2. Dynamic data: variable-length names + field descriptors (6 bytes each)

    Args:
        data: bytes/memoryview of the meta-object buffer
        count: number of meta-objects
        meta_fields: list of MetaField objects (from parse_meta_fields)
        endian: byte order character ("<" or ">")

    Returns:
        list of MetaObject objects with resolved parent references
    """
    static_size = count * 24
    static_buf = data[:static_size]
    dyn_buf = data[static_size:]
    dyn_buf_len = len(dyn_buf)

    meta_objects = []
    dyn_offset = 0

    for i in range(count):
        offset = i * 24
        name_len, major, minor, n_fields, parent_idx, slot_count = struct.unpack_from(
            endian + "IIIIiI", static_buf, offset
        )

        # Version fix: 0 means 1
        if major == 0:
            major = 1

        # Check if we have enough data remaining (handle truncated files)
        needed = name_len + n_fields * 6
        if dyn_offset + needed > dyn_buf_len:
            # Truncated file - stop parsing meta-objects here
            break

        # Extract name from dynamic buffer
        # Truncate at first null byte (v8 names may have padding bytes after null)
        raw_name = bytes(dyn_buf[dyn_offset:dyn_offset + name_len])
        null_pos = raw_name.find(b"\0")
        name = raw_name[:null_pos] if null_pos >= 0 else raw_name
        dyn_offset += name_len

        # Parse field descriptors (6 bytes each: typeIdx:u16, slot:u16, size:u16)
        fields = []
        for j in range(n_fields):
            type_idx, slot, size = struct.unpack_from(
                endian + "HHH", dyn_buf, dyn_offset
            )
            dyn_offset += 6

            # Resolve meta-field reference
            mf = meta_fields[type_idx] if type_idx < len(meta_fields) else None
            fields.append(MetaObjectField(type_idx, slot, size, mf))

        meta_objects.append(MetaObject(
            i, name, major, minor, fields, parent_idx, slot_count
        ))

    # Resolve parent references
    for mo in meta_objects:
        if mo.parent_index >= 0 and mo.parent_index < len(meta_objects):
            mo.parent = meta_objects[mo.parent_index]

    return meta_objects
