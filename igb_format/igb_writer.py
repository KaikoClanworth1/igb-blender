"""Low-level IGB binary file serializer.

Writes an IGB file from in-memory data structures. This is the raw serializer —
it doesn't know about scene graphs or geometry, it just writes the 11 sections
in the correct order with proper alignment and sizing.

This is the exact inverse of igb_reader.py.
"""

import struct
from .igb_constants import (
    HEADER_SIZE, IGB_MAGIC_COOKIE,
    H_ENTRY_BUFFER_SIZE, H_ENTRY_COUNT,
    H_META_OBJ_BUFFER_SIZE, H_META_OBJ_COUNT,
    H_OBJ_BUFFER_SIZE, H_OBJ_COUNT,
    H_MREF_BUFFER_SIZE, H_MREF_COUNT,
    H_MF_BUFFER_SIZE, H_MF_COUNT,
    H_MAGIC_COOKIE, H_VER_FLAGS,
    FLAG_HAS_INFO, FLAG_HAS_EXTERNAL,
    FLAG_SHARED_ENTRIES, FLAG_HAS_MEMORY_POOL_NAMES,
    FIELD_TYPE_FORMATS, VARIABLE_SIZE_FIELDS, REFERENCE_FIELDS,
)


class IGBWriter:
    """Writes a complete IGB binary file from in-memory structures.

    All data must be set before calling write(). The writer computes
    buffer sizes and header fields automatically.

    Usage:
        writer = IGBWriter()
        writer.version = 6
        writer.endian = "<"
        writer.meta_fields = [...]      # list of MetaFieldDef
        writer.meta_objects = [...]      # list of MetaObjectDef
        writer.alignment_data = b'...'  # raw alignment buffer (without size prefix)
        writer.external_dirs = [...]    # list of bytes
        writer.memory_pool_names = [...] # list of bytes
        writer.entries = [...]          # list of EntryDef
        writer.index_map = [...]        # list of int (uint16)
        writer.info_list_index = N      # int or None
        writer.objects = [...]          # list of ObjectDef or MemoryBlockDef
        writer.ref_info = [...]         # list of dict (matches reader format)
        writer.write("output.igb")
    """

    def __init__(self):
        self.version = 6
        self.endian = "<"
        self.has_info = True
        self.has_external = True
        self.shared_entries = True
        self.has_memory_pool_names = True

        self.meta_fields = []       # list of MetaFieldDef
        self.meta_objects = []      # list of MetaObjectDef
        self.alignment_data = b''   # raw alignment buffer content (without 4-byte size prefix)
        self.external_dirs = []     # list of bytes (directory name strings)
        self.external_dirs_unk = 0  # unknown field in external dirs header
        self.memory_pool_names = [] # list of bytes (pool name strings)
        self.entries = []           # list of EntryDef
        self.index_map = []         # list of int (uint16 entry indices)
        self.info_list_index = None # int or None
        self.objects = []           # list of ObjectDef or MemoryBlockDef
        self.ref_info = []          # list of dict (matches reader.ref_info format)

    def write(self, filepath):
        """Serialize and write the complete IGB file to disk.

        Args:
            filepath: output file path
        """
        endian = self.endian

        # Pre-serialize all variable-size buffers
        mf_buf = self._serialize_meta_fields()
        align_buf = self._serialize_alignment()
        mo_buf = self._serialize_meta_objects()
        ext_buf = self._serialize_external_dirs() if self.has_external else b''
        pool_buf = self._serialize_memory_pool_names() if self.has_memory_pool_names else b''
        entry_buf = self._serialize_entries()
        index_buf = self._serialize_index()
        info_buf = self._serialize_info()
        obj_buf = self._serialize_objects()
        mref_buf = self._serialize_memory_refs()

        # Count objects and memory blocks
        obj_count = sum(1 for ri in self.ref_info if ri['is_object'])
        mem_count = sum(1 for ri in self.ref_info if not ri['is_object'])

        # Build header
        ver_flags = self.version
        if self.has_info:
            ver_flags |= FLAG_HAS_INFO
        if self.has_external:
            ver_flags |= FLAG_HAS_EXTERNAL
        if self.shared_entries:
            ver_flags |= FLAG_SHARED_ENTRIES
        if self.has_memory_pool_names:
            ver_flags |= FLAG_HAS_MEMORY_POOL_NAMES

        header_fields = [0] * 12
        header_fields[H_ENTRY_BUFFER_SIZE] = len(entry_buf)
        header_fields[H_ENTRY_COUNT] = len(self.entries)
        header_fields[H_META_OBJ_BUFFER_SIZE] = len(mo_buf)
        header_fields[H_META_OBJ_COUNT] = len(self.meta_objects)
        header_fields[H_OBJ_BUFFER_SIZE] = len(obj_buf)
        header_fields[H_OBJ_COUNT] = obj_count
        header_fields[H_MREF_BUFFER_SIZE] = len(mref_buf)
        header_fields[H_MREF_COUNT] = mem_count
        header_fields[H_MF_BUFFER_SIZE] = len(mf_buf)
        header_fields[H_MF_COUNT] = len(self.meta_fields)
        header_fields[H_MAGIC_COOKIE] = IGB_MAGIC_COOKIE
        header_fields[H_VER_FLAGS] = ver_flags

        header_buf = struct.pack(endian + "I" * 12, *header_fields)

        # Write all sections sequentially
        with open(filepath, "wb") as f:
            f.write(header_buf)    # 1. Header (48 bytes)
            f.write(mf_buf)        # 2. Meta-field buffer
            f.write(align_buf)     # 3. Alignment buffer
            f.write(mo_buf)        # 4. Meta-object buffer
            if self.has_external:
                f.write(ext_buf)   # 5. External directories
            if self.has_memory_pool_names:
                f.write(pool_buf)  # 6. Memory pool names
            f.write(entry_buf)     # 7. Entry buffer
            f.write(index_buf)     # 8. Index buffer
            if self.has_info:
                f.write(info_buf)  # 9. Info V4
            f.write(obj_buf)       # 10. Object buffer
            f.write(mref_buf)      # 11. Memory reference buffer

    def _serialize_meta_fields(self):
        """Serialize the meta-field buffer.

        Format:
            Static part: count * 12 bytes (nameLen:u32, majorVer:u32, minorVer:u32)
            Dynamic part: concatenated null-terminated name strings
        """
        endian = self.endian
        static_parts = []
        dynamic_parts = []

        for mf in self.meta_fields:
            name_bytes = mf.name if isinstance(mf.name, bytes) else mf.name.encode('ascii')
            # Use original name_len if available (preserves padding), else name + null
            if mf.name_len is not None:
                name_len = mf.name_len
            else:
                name_len = len(name_bytes) + 1  # name + null terminator
            # Build padded name data
            name_data = name_bytes + b'\x00' * (name_len - len(name_bytes))
            static_parts.append(struct.pack(
                endian + "III",
                name_len,
                mf.major_version,
                mf.minor_version,
            ))
            dynamic_parts.append(name_data)

        return b''.join(static_parts) + b''.join(dynamic_parts)

    def _serialize_meta_objects(self):
        """Serialize the meta-object buffer.

        Format:
            Static part: count * 24 bytes
                (nameLen:u32, majorVer:u32, minorVer:u32, fieldCount:u32, parentIdx:i32, slotCount:u32)
            Dynamic part: concatenated (name + field descriptors)
                name: null-terminated string (padded to 2-byte alignment)
                fields: fieldCount * 6 bytes (typeIdx:u16, slot:u16, size:u16)
        """
        endian = self.endian
        static_parts = []
        dynamic_parts = []

        for mo in self.meta_objects:
            name_bytes = mo.name if isinstance(mo.name, bytes) else mo.name.encode('ascii')

            # Use original name_len if available, else pad to 2-byte alignment
            if mo.name_len is not None:
                name_len = mo.name_len
            else:
                # Meta-object names are padded to even length
                raw_len = len(name_bytes) + 1  # name + null
                name_len = (raw_len + 1) & ~1  # round up to even

            # Build padded name data
            name_data = name_bytes + b'\x00' * (name_len - len(name_bytes))

            static_parts.append(struct.pack(
                endian + "IIIIiI",
                name_len,
                mo.major_version,
                mo.minor_version,
                len(mo.fields),
                mo.parent_index,
                mo.slot_count,
            ))

            # Dynamic: padded name + field descriptors
            dyn = bytearray(name_data)
            for fd in mo.fields:
                dyn.extend(struct.pack(endian + "HHH", fd.type_index, fd.slot, fd.size))
            dynamic_parts.append(bytes(dyn))

        return b''.join(static_parts) + b''.join(dynamic_parts)

    def _serialize_alignment(self):
        """Serialize the alignment buffer.

        The alignment_data is stored as raw bytes (the full buffer including
        the 4-byte size prefix). We just return it as-is.
        """
        # alignment_data should already include the size prefix
        return self.alignment_data

    def _serialize_external_dirs(self):
        """Serialize external directory references.

        Format:
            bufSize:u32, unk:u32(=0), count:u32
            Static: count * nameLen:u32
            Dynamic: concatenated null-terminated name strings
        """
        endian = self.endian
        count = len(self.external_dirs)

        static_part = bytearray()
        dynamic_part = bytearray()

        for name in self.external_dirs:
            name_bytes = name if isinstance(name, bytes) else name.encode('ascii')
            name_with_null = name_bytes + b'\x00'
            static_part.extend(struct.pack(endian + "I", len(name_with_null)))
            dynamic_part.extend(name_with_null)

        body = struct.pack(endian + "II", self.external_dirs_unk, count) + bytes(static_part) + bytes(dynamic_part)
        buf_size = 4 + len(body)  # include size prefix
        return struct.pack(endian + "I", buf_size) + body

    def _serialize_memory_pool_names(self):
        """Serialize memory pool name strings.

        Format:
            bufSize:u32, count:u32
            Concatenated null-terminated name strings
        """
        endian = self.endian
        count = len(self.memory_pool_names)

        names_data = bytearray()
        for name in self.memory_pool_names:
            name_bytes = name if isinstance(name, bytes) else name.encode('ascii')
            names_data.extend(name_bytes + b'\x00')

        body = struct.pack(endian + "I", count) + bytes(names_data)
        buf_size = 4 + len(body)  # include size prefix
        return struct.pack(endian + "I", buf_size) + body

    def _serialize_entries(self):
        """Serialize the entry buffer.

        Each entry: typeIndex:u32, entrySize:u32, then field data.
        Fields are serialized according to their meta-object field descriptors.
        Field values are aligned to 4 bytes.
        """
        endian = self.endian
        buf = bytearray()

        for entry in self.entries:
            type_index = entry.type_index
            field_data = bytearray()

            # Get field descriptors from meta-object
            mo = self.meta_objects[type_index]
            for i, fd in enumerate(mo.fields):
                val = entry.field_values[i] if i < len(entry.field_values) else 0
                size = fd.size
                fmt = {4: "i", 2: "h", 1: "B"}.get(size, None)
                if fmt is None:
                    raise ValueError(f"Unexpected entry field size: {size}")
                field_data.extend(struct.pack(endian + fmt, val))
                # Align to 4 bytes
                pad = (size + 3) & ~3
                field_data.extend(b'\x00' * (pad - size))

            entry_size = 8 + len(field_data)
            buf.extend(struct.pack(endian + "II", type_index, entry_size))
            buf.extend(field_data)

        return bytes(buf)

    def _serialize_index(self):
        """Serialize the index buffer.

        Format: bufSize:u32, count:u32, uint16[] indices
        """
        endian = self.endian
        count = len(self.index_map)
        indices_data = struct.pack(endian + "H" * count, *self.index_map)
        body = struct.pack(endian + "I", count) + indices_data
        buf_size = 4 + len(body)
        return struct.pack(endian + "I", buf_size) + body

    def _serialize_info(self):
        """Serialize the info V4 section (single uint32)."""
        if self.info_list_index is None:
            return b''
        return struct.pack(self.endian + "I", self.info_list_index)

    def _serialize_objects(self):
        """Serialize the object buffer.

        Only IGBObject entries (not memory blocks) are written here.
        Each object: typeIndex:u32, entrySize:u32, then field data.

        If an ObjectDef has raw_bytes set, those are used directly
        for byte-perfect round-trip fidelity.
        """
        endian = self.endian
        buf = bytearray()

        for i, ri in enumerate(self.ref_info):
            if not ri['is_object']:
                continue

            obj = self.objects[i]
            type_index = obj.type_index

            if obj.raw_bytes is not None:
                # Use exact original bytes for round-trip
                field_data = obj.raw_bytes
            else:
                field_data = self._serialize_object_fields(obj, endian)

            entry_size = 8 + len(field_data)
            buf.extend(struct.pack(endian + "II", type_index, entry_size))
            buf.extend(field_data)

        return bytes(buf)

    def _serialize_object_fields(self, obj, endian):
        """Serialize all fields for a single object.

        Handles all field types including String (v6 inline), ObjectRef,
        MemoryRef, Vec types, arrays, etc.

        Args:
            obj: ObjectDef with raw_fields list
            endian: byte order character

        Returns:
            bytes of serialized field data
        """
        buf = bytearray()

        for slot, val, field_desc in obj.raw_fields:
            short_name = field_desc.short_name
            desc_size = field_desc.size

            data = self._serialize_field(short_name, desc_size, val, endian)
            buf.extend(data)

            # Align to 4 bytes
            actual_size = len(data)
            pad = ((actual_size + 3) & ~3) - actual_size
            buf.extend(b'\x00' * pad)

        return bytes(buf)

    def _serialize_field(self, short_name, desc_size, val, endian):
        """Serialize a single field value.

        This is the inverse of IGBReader._deserialize_field.

        Args:
            short_name: field type short name (bytes, e.g. b"Float")
            desc_size: byte size from field descriptor
            val: the value to serialize
            endian: byte order character

        Returns:
            bytes of serialized field data (before alignment padding)
        """
        # Check fixed-format types first
        fmt = FIELD_TYPE_FORMATS.get(short_name)
        if fmt is not None:
            if isinstance(val, tuple):
                return struct.pack(endian + fmt, *val)
            else:
                return struct.pack(endian + fmt, val)

        # String type (v6): length:i32 + string bytes (inline)
        if short_name == b"String":
            if isinstance(val, str):
                str_bytes = val.encode('utf-8')
            elif isinstance(val, bytes):
                str_bytes = val
            elif isinstance(val, int):
                # Integer value means the string was stored as just a length
                # with no actual string data (empty string with length indicator)
                str_bytes = b''
            else:
                str_bytes = b''

            # Include null terminator if there's actual string content
            if str_bytes:
                str_data = str_bytes + b'\x00'
            else:
                str_data = b''

            str_len = len(str_data)
            return struct.pack(endian + "i", str_len) + str_data

        # ObjectRef and MemoryRef: 4-byte signed index (-1 = null)
        if short_name in (b"ObjectRef", b"MemoryRef"):
            return struct.pack(endian + "i", val if val is not None else -1)

        # Variable-size array types: stored as raw bytes
        if short_name == b"CharArray" or short_name == b"UnsignedCharArray":
            if isinstance(val, bytes):
                return val
            return bytes(desc_size)

        if short_name == b"FloatArray":
            if isinstance(val, (tuple, list)):
                return struct.pack(endian + "f" * len(val), *val)
            if isinstance(val, bytes):
                return val
            return bytes(desc_size)

        if short_name == b"IntArray":
            if isinstance(val, (tuple, list)):
                return struct.pack(endian + "i" * len(val), *val)
            if isinstance(val, bytes):
                return val
            return bytes(desc_size)

        if short_name == b"UnsignedIntArray":
            if isinstance(val, (tuple, list)):
                return struct.pack(endian + "I" * len(val), *val)
            if isinstance(val, bytes):
                return val
            return bytes(desc_size)

        if short_name == b"ShortArray":
            if isinstance(val, (tuple, list)):
                return struct.pack(endian + "h" * len(val), *val)
            if isinstance(val, bytes):
                return val
            return bytes(desc_size)

        if short_name == b"UnsignedShortArray":
            if isinstance(val, (tuple, list)):
                return struct.pack(endian + "H" * len(val), *val)
            if isinstance(val, bytes):
                return val
            return bytes(desc_size)

        if short_name == b"LongArray":
            if isinstance(val, (tuple, list)):
                return struct.pack(endian + "q" * len(val), *val)
            if isinstance(val, bytes):
                return val
            return bytes(desc_size)

        if short_name == b"UnsignedLongArray":
            if isinstance(val, (tuple, list)):
                return struct.pack(endian + "Q" * len(val), *val)
            if isinstance(val, bytes):
                return val
            return bytes(desc_size)

        # Struct: raw bytes of descriptor size
        if short_name == b"Struct":
            if isinstance(val, bytes):
                return val
            # Struct stored as integer (common for small structs like vertex format)
            return struct.pack(endian + "I", val) if desc_size == 4 else bytes(desc_size)

        # Fallback: serialize based on descriptor size
        fmt_by_size = {1: "B", 2: "h", 4: "i", 8: "q"}
        if desc_size in fmt_by_size:
            return struct.pack(endian + fmt_by_size[desc_size], val)

        # Unknown: return raw bytes or zero-filled
        if isinstance(val, bytes):
            return val
        return bytes(desc_size)

    def _serialize_memory_refs(self):
        """Serialize the memory reference buffer.

        Each memory block's raw data, 4-byte aligned.
        Uses raw_data (with original padding) when available for round-trip.
        """
        buf = bytearray()

        for i, ri in enumerate(self.ref_info):
            if ri['is_object']:
                continue

            obj = self.objects[i]

            if hasattr(obj, 'raw_data') and obj.raw_data is not None:
                # Use raw data with original padding for round-trip
                buf.extend(obj.raw_data)
            else:
                data = obj.data if obj.data is not None else b''
                buf.extend(data)
                # Align to 4 bytes
                pad = ((len(data) + 3) & ~3) - len(data)
                buf.extend(b'\x00' * pad)

        return bytes(buf)


# ============================================================================
# Data classes for building IGB structures
# ============================================================================

class MetaFieldDef:
    """Definition of a meta-field type for the writer."""

    __slots__ = ('name', 'major_version', 'minor_version', 'short_name', 'name_len')

    def __init__(self, name, major_version=1, minor_version=0, name_len=None):
        self.name = name if isinstance(name, bytes) else name.encode('ascii')
        self.major_version = major_version
        self.minor_version = minor_version
        # name_len: original serialized name length (including null + any padding)
        # If None, computed as len(name) + 1 (just name + null terminator)
        self.name_len = name_len

        # Derive short name
        short = self.name
        if short.startswith(b"ig"):
            short = short[2:]
        if short.endswith(b"MetaField"):
            short = short[:-9]
        self.short_name = short

    def __repr__(self):
        return f"MetaFieldDef({self.name!r})"


class MetaObjectFieldDef:
    """Definition of a field descriptor within a meta-object."""

    __slots__ = ('type_index', 'slot', 'size', 'short_name')

    def __init__(self, type_index, slot, size, short_name=None):
        self.type_index = type_index
        self.slot = slot
        self.size = size
        self.short_name = short_name  # resolved from meta_fields, set later


class MetaObjectDef:
    """Definition of a meta-object (class) for the writer."""

    __slots__ = ('name', 'major_version', 'minor_version', 'fields',
                 'parent_index', 'slot_count', 'name_len')

    def __init__(self, name, major_version=1, minor_version=0,
                 fields=None, parent_index=-1, slot_count=2, name_len=None):
        self.name = name if isinstance(name, bytes) else name.encode('ascii')
        self.major_version = major_version
        self.minor_version = minor_version
        self.fields = fields or []
        self.parent_index = parent_index
        self.slot_count = slot_count
        # name_len: original serialized name length (including null + padding)
        # If None, computed as name+null padded to 2-byte boundary
        self.name_len = name_len


class EntryDef:
    """Definition of a directory entry for the writer."""

    __slots__ = ('type_index', 'field_values')

    def __init__(self, type_index, field_values=None):
        self.type_index = type_index
        self.field_values = field_values or []


class ObjectFieldDef:
    """A single field value with its descriptor info, for serialization."""

    __slots__ = ('slot', 'short_name', 'size')

    def __init__(self, slot, short_name, size):
        self.slot = slot
        self.short_name = short_name if isinstance(short_name, bytes) else short_name.encode('ascii')
        self.size = size


class ObjectDef:
    """Definition of an object for the writer's object buffer.

    For round-trip fidelity, raw_bytes can be set to the exact original
    field data bytes (without the 8-byte type+size header). When set,
    _serialize_objects uses raw_bytes instead of re-serializing from fields.
    """

    __slots__ = ('type_index', 'raw_fields', 'raw_bytes')

    def __init__(self, type_index, raw_fields=None, raw_bytes=None):
        self.type_index = type_index
        # raw_fields: list of (slot, value, ObjectFieldDef)
        self.raw_fields = raw_fields or []
        # raw_bytes: exact original field data bytes for round-trip
        self.raw_bytes = raw_bytes


class MemoryBlockDef:
    """Definition of a memory block for the writer.

    For round-trip fidelity, raw_data can include original alignment
    padding bytes. When set, _serialize_memory_refs uses raw_data
    instead of data + zero-padding.
    """

    __slots__ = ('data', 'raw_data')

    def __init__(self, data=None, raw_data=None):
        self.data = data or b''
        # raw_data: data + original alignment padding bytes (for round-trip)
        self.raw_data = raw_data


def from_reader(reader):
    """Create an IGBWriter populated from an IGBReader's parsed data.

    This enables round-trip testing: read an IGB file, convert to writer
    structures, and write back. The output should be byte-identical.

    Args:
        reader: a fully-parsed IGBReader instance

    Returns:
        IGBWriter ready to write
    """
    writer = IGBWriter()

    # Copy header flags
    writer.version = reader.header.version
    writer.endian = reader.header.endian
    writer.has_info = reader.header.has_info
    writer.has_external = reader.header.has_external
    writer.shared_entries = reader.header.shared_entries
    writer.has_memory_pool_names = reader.header.has_memory_pool_names

    # Extract original name lengths from raw file data
    # Meta-field buffer: static part has nameLen for each entry
    mf_name_lens = []
    mf_static_start = 48  # right after header
    if reader.header.version >= 8:
        # v8 has name pool before meta-fields — need to skip it
        np_size = struct.unpack_from(reader.header.endian + "I", reader.data, 48)[0]
        mf_static_start = 48 + np_size
    for i in range(len(reader.meta_fields)):
        name_len = struct.unpack_from(
            reader.header.endian + "I", reader.data, mf_static_start + i * 12
        )[0]
        mf_name_lens.append(name_len)

    # Meta-object buffer: static part has nameLen for each entry
    mo_name_lens = []
    # Compute meta-object buffer start position
    mo_static_start = mf_static_start + reader.header.mf_buffer_size
    align_size = struct.unpack_from(reader.header.endian + "I", reader.data, mo_static_start)[0]
    mo_static_start += align_size  # skip alignment buffer
    for i in range(len(reader.meta_objects)):
        name_len = struct.unpack_from(
            reader.header.endian + "I", reader.data, mo_static_start + i * 24
        )[0]
        mo_name_lens.append(name_len)

    # Copy meta-fields with original name lengths
    for i, mf in enumerate(reader.meta_fields):
        writer.meta_fields.append(MetaFieldDef(
            mf.name, mf.major_version, mf.minor_version,
            name_len=mf_name_lens[i]
        ))

    # Copy meta-objects with original name lengths
    for i, mo in enumerate(reader.meta_objects):
        fields = []
        for fd in mo.fields:
            fdef = MetaObjectFieldDef(fd.type_index, fd.slot, fd.size)
            # Resolve short_name from meta_fields
            if fd.meta_field:
                fdef.short_name = fd.meta_field.short_name
            elif fd.type_index < len(reader.meta_fields):
                fdef.short_name = reader.meta_fields[fd.type_index].short_name
            fields.append(fdef)

        writer.meta_objects.append(MetaObjectDef(
            mo.name, mo.major_version, mo.minor_version,
            fields, mo.parent_index, mo.slot_count,
            name_len=mo_name_lens[i]
        ))

    # Copy alignment data (raw bytes including size prefix)
    writer.alignment_data = bytes(reader.alignment_data)

    # Copy external dirs (and preserve the unknown field)
    writer.external_dirs = list(reader.external_dirs)
    if reader.header.has_external:
        # Find the external dirs position and extract the unk field
        ext_pos = mf_static_start + reader.header.mf_buffer_size
        ext_pos += struct.unpack_from(reader.header.endian + "I", reader.data, ext_pos)[0]
        ext_pos += reader.header.meta_obj_buffer_size
        _ext_size, ext_unk, _ext_count = struct.unpack_from(
            reader.header.endian + "III", reader.data, ext_pos
        )
        writer.external_dirs_unk = ext_unk

    # Copy memory pool names
    writer.memory_pool_names = list(reader.memory_pool_names)

    # Copy entries
    for ent_type, fields in reader.entries:
        field_values = [f[1] for f in fields]
        writer.entries.append(EntryDef(ent_type, field_values))

    # Copy index map
    writer.index_map = list(reader.index_map)

    # Copy info list index
    writer.info_list_index = reader.info_list_index

    # Copy ref_info
    writer.ref_info = [dict(ri) for ri in reader.ref_info]

    # Extract raw object bytes from the original file for round-trip fidelity
    # Find the object buffer start position
    obj_buf_pos = mf_static_start + reader.header.mf_buffer_size
    obj_buf_pos += struct.unpack_from(reader.header.endian + "I", reader.data, obj_buf_pos)[0]
    obj_buf_pos += reader.header.meta_obj_buffer_size
    if reader.header.has_external:
        ext_s = struct.unpack_from(reader.header.endian + "I", reader.data, obj_buf_pos)[0]
        obj_buf_pos += ext_s
    if reader.header.has_memory_pool_names:
        pool_s = struct.unpack_from(reader.header.endian + "I", reader.data, obj_buf_pos)[0]
        obj_buf_pos += pool_s
    obj_buf_pos += reader.header.entry_buffer_size
    idx_s = struct.unpack_from(reader.header.endian + "I", reader.data, obj_buf_pos)[0]
    obj_buf_pos += idx_s
    if reader.header.has_info:
        obj_buf_pos += 4

    # Parse per-object raw bytes from original object buffer
    raw_obj_bytes = {}  # obj_index -> bytes (field data only, no header)
    scan_pos = obj_buf_pos
    for i, ri in enumerate(reader.ref_info):
        if not ri['is_object']:
            continue
        _etype, esize = struct.unpack_from(
            reader.header.endian + "II", reader.data, scan_pos
        )
        raw_obj_bytes[i] = reader.data[scan_pos + 8:scan_pos + esize]
        scan_pos += esize

    # Extract raw memory block bytes (with alignment padding) from original file
    mref_buf_pos = obj_buf_pos + reader.header.obj_buffer_size
    raw_mem_bytes = {}  # obj_index -> bytes (data + padding)
    scan_pos = mref_buf_pos
    for i, ri in enumerate(reader.ref_info):
        if ri['is_object']:
            continue
        size = ri['mem_size']
        aligned_size = (size + 3) & ~3
        raw_mem_bytes[i] = reader.data[scan_pos:scan_pos + aligned_size]
        scan_pos += aligned_size

    # Copy objects and memory blocks
    from .igb_objects import IGBObject, IGBMemoryBlock

    writer.objects = [None] * len(reader.objects)
    for i, obj in enumerate(reader.objects):
        if isinstance(obj, IGBObject):
            # Build ObjectDef from the reader's parsed object
            raw_fields = []
            for slot, val, fi in obj._raw_fields:
                fd = ObjectFieldDef(slot, fi.short_name, fi.size)
                raw_fields.append((slot, val, fd))

            odef = ObjectDef(
                obj.meta_object.index, raw_fields,
                raw_bytes=raw_obj_bytes.get(i)
            )
            writer.objects[i] = odef

        elif isinstance(obj, IGBMemoryBlock):
            mdef = MemoryBlockDef(
                obj.data,
                raw_data=raw_mem_bytes.get(i)
            )
            writer.objects[i] = mdef

    return writer
