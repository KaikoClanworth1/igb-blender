"""Full IGB binary file reader.

Reads an IGB file and produces a tree of IGBObject and IGBMemoryBlock instances.
Based on the mateon1 gist parser and Alchemy 5.0 SDK headers.
"""

import struct
from .igb_constants import (
    HEADER_SIZE, ENTRY_TYPE_OBJECT, ENTRY_TYPE_MEMORY,
    FIELD_TYPE_FORMATS, VARIABLE_SIZE_FIELDS, REFERENCE_FIELDS,
)
from .igb_header import IGBHeader
from .igb_types import parse_meta_fields, parse_meta_objects
from .igb_objects import IGBObject, IGBMemoryBlock


class IGBReader:
    """Reads and parses a complete IGB binary file.

    Usage:
        reader = IGBReader("path/to/file.igb")
        reader.read()
        # Access parsed data:
        #   reader.header - IGBHeader
        #   reader.meta_fields - list of MetaField
        #   reader.meta_objects - list of MetaObject
        #   reader.objects - list of (IGBObject | IGBMemoryBlock | None)
        #   reader.info_list_index - index of the info list root object
    """

    def __init__(self, filepath):
        self.filepath = filepath
        self.data = None
        self.view = None
        self.header = None
        self.meta_fields = []
        self.meta_objects = []
        self.entries = []       # raw parsed entries
        self.index_map = []     # index buffer: list of entry indices
        self.objects = []       # final: IGBObject or IGBMemoryBlock per index entry
        self.ref_info = []      # entry metadata for each index slot
        self.info_list_index = None  # index of the info list root
        self.external_dirs = []
        self.memory_pool_names = []
        self.name_pool = []     # v8+ name pool (class/string names)
        self.back_refs = {}     # tracks which objects reference which
        self._obj_list_data = set()  # indices that are igObjectList data blocks

    def read(self):
        """Read and parse the entire IGB file."""
        with open(self.filepath, "rb") as f:
            self.data = f.read()
        self.view = memoryview(self.data)

        # Check minimum file size
        file_size = len(self.data)
        if file_size < HEADER_SIZE:
            raise ValueError(f"File too small: {file_size} bytes")

        pos = 0

        # 1. Header (48 bytes)
        self.header = IGBHeader.read(self.data[:HEADER_SIZE])
        pos = HEADER_SIZE
        endian = self.header.endian

        # Compute expected minimum file size from header buffer sizes
        expected_min = (
            HEADER_SIZE + self.header.mf_buffer_size +
            4 +  # alignment buffer minimum (size prefix)
            self.header.meta_obj_buffer_size +
            self.header.entry_buffer_size +
            8 +  # index buffer minimum (size + count)
            self.header.obj_buffer_size +
            self.header.mref_buffer_size
        )
        if file_size < expected_min:
            raise ValueError(
                f"Truncated IGB file: {file_size} bytes, expected at least ~{expected_min} bytes"
            )

        # 2. Name pool (v8+ only)
        # Version 8 adds a name pool buffer between the header and meta-field buffer.
        # It contains all class/string names used in the file, with inline
        # (buf_size:u32, count:u32) header followed by (name_len:u32, name:bytes) entries.
        if self.header.version >= 8:
            pos = self._read_name_pool(pos)

        # 3. Meta-field buffer
        mf_size = self.header.mf_buffer_size
        self.meta_fields = parse_meta_fields(
            self.view[pos:pos + mf_size], self.header.mf_count, endian
        )
        pos += mf_size

        # 4. Alignment buffer (starts with 4-byte size prefix)
        align_size = struct.unpack_from(endian + "I", self.data, pos)[0]
        self.alignment_data = self.view[pos:pos + align_size]
        pos += align_size

        # 5. Meta-object buffer
        mo_size = self.header.meta_obj_buffer_size
        self.meta_objects = parse_meta_objects(
            self.view[pos:pos + mo_size], self.header.meta_obj_count,
            self.meta_fields, endian
        )
        pos += mo_size

        # 6. External directories (optional, v5+)
        if self.header.has_external:
            pos = self._read_external_dirs(pos)

        # 7. Memory pool names (optional)
        if self.header.has_memory_pool_names:
            pos = self._read_memory_pool_names(pos)

        # 8. Entry buffer
        pos = self._read_entries(pos)

        # 9. Index buffer
        pos = self._read_index(pos)

        # 10. Prepare entries (build ref_info and objects list)
        self._prepare_entries()

        # 11. Info V4 (optional)
        if self.header.has_info:
            self.info_list_index = struct.unpack_from(endian + "I", self.data, pos)[0]
            pos += 4
        else:
            self.info_list_index = None

        # 12. Object buffer
        pos = self._read_objects(pos)

        # 13. Memory reference buffer
        pos = self._read_memory_refs(pos)

        return self

    def _read_name_pool(self, pos):
        """Parse the v8+ name pool buffer.

        IGB version 8 adds a name pool buffer immediately after the header,
        before the meta-field buffer. It contains all class and string names
        used in the file.

        Format:
            4 bytes: buffer size (total including this size field)
            4 bytes: name count
            For each name:
                4 bytes: name length (including null terminator)
                N bytes: null-terminated name string

        Args:
            pos: byte offset to start of name pool

        Returns:
            byte offset after the name pool
        """
        endian = self.header.endian
        buf_size, name_count = struct.unpack_from(endian + "II", self.data, pos)
        data_pos = pos + 8

        self.name_pool = []
        for i in range(name_count):
            name_len = struct.unpack_from(endian + "I", self.data, data_pos)[0]
            name = self.data[data_pos + 4:data_pos + 4 + name_len].rstrip(b"\0")
            self.name_pool.append(name)
            data_pos += 4 + name_len

        return pos + buf_size

    def _read_external_dirs(self, pos):
        """Parse external directory references."""
        endian = self.header.endian
        buf_size, unk, ext_count = struct.unpack_from(endian + "III", self.data, pos)
        buf_start = pos

        # Static part: ext_count * 4 bytes of name lengths
        static_pos = pos + 12
        dyn_pos = static_pos + 4 * ext_count

        self.external_dirs = []
        for i in range(ext_count):
            name_len = struct.unpack_from(endian + "I", self.data, static_pos)[0]
            name = self.data[dyn_pos:dyn_pos + name_len].rstrip(b"\0")
            self.external_dirs.append(name)
            static_pos += 4
            dyn_pos += name_len

        return buf_start + buf_size

    def _read_memory_pool_names(self, pos):
        """Parse memory pool name strings."""
        endian = self.header.endian
        buf_size, num_pools = struct.unpack_from(endian + "II", self.data, pos)

        dyn_data = self.data[pos + 8:pos + buf_size]
        self.memory_pool_names = []
        offset = 0
        for i in range(num_pools):
            null_pos = dyn_data.find(b"\0", offset)
            if null_pos == -1:
                null_pos = len(dyn_data)
            self.memory_pool_names.append(dyn_data[offset:null_pos])
            offset = null_pos + 1

        return pos + buf_size

    def _read_entries(self, pos):
        """Parse the entry buffer (directory entries)."""
        endian = self.header.endian
        entry_buf_size = self.header.entry_buffer_size
        entry_count = self.header.entry_count
        buf = self.view[pos:pos + entry_buf_size]
        buf_offset = 0

        self.entries = []
        for i in range(entry_count):
            ent_type, ent_size = struct.unpack_from(endian + "II", buf, buf_offset)
            ent_data = buf[buf_offset + 8:buf_offset + ent_size]

            # Get field descriptors from meta-object
            field_info = self.meta_objects[ent_type].fields
            fields = []
            data_offset = 0

            for fi in field_info:
                size = fi.size
                fmt = {4: "i", 2: "h", 1: "B"}.get(size, None)
                if fmt is None:
                    raise ValueError(f"Unexpected entry field size: {size}")
                val = struct.unpack_from(endian + fmt, ent_data, data_offset)[0]
                fields.append((fi.slot, val, fi))
                # Align to 4-byte boundary
                data_offset += (size + 3) & ~3

            self.entries.append((ent_type, fields))
            buf_offset += ent_size

        return pos + entry_buf_size

    def _read_index(self, pos):
        """Parse the index buffer."""
        endian = self.header.endian
        buf_size, num_idx = struct.unpack_from(endian + "II", self.data, pos)
        self.index_map = list(struct.unpack_from(
            endian + "H" * num_idx, self.data, pos + 8
        ))
        return pos + buf_size

    def _prepare_entries(self):
        """Process entries: build ref_info and allocate object/memory slots."""
        self.ref_info = []
        self.objects = []

        for idx in self.index_map:
            ent_type, fields = self.entries[idx]
            type_name = self.meta_objects[ent_type].name

            if type_name == b"igMemoryDirEntry":
                # Actual slot layout (from meta-object definition):
                # _name=slot2, _memSize=slot7, _memTypeIndex=slot10,
                # _refCounted=slot11, _alignmentTypeIndex=slot12, _memoryPoolHandle=slot13
                field_dict = {f[0]: f[1] for f in fields}
                mem_size = field_dict.get(7, 0)
                mem_type_idx = field_dict.get(10, -1)
                ref_counted = field_dict.get(11, 0)
                align_type_idx = field_dict.get(12, -1)
                mem_pool_handle = field_dict.get(13, -1)

                # Resolve mem type name
                mem_type_name = None
                if 0 <= mem_type_idx < len(self.meta_objects):
                    mem_type_name = self.meta_objects[mem_type_idx].name

                self.ref_info.append({
                    'is_object': False,
                    'type_index': mem_type_idx,
                    'type_name': mem_type_name,
                    'mem_size': mem_size,
                    'ref_counted': ref_counted,
                    'align_type_idx': align_type_idx,
                    'mem_pool_handle': mem_pool_handle,
                })

                mb = IGBMemoryBlock(
                    len(self.objects), mem_size, mem_type_idx, mem_type_name,
                    bool(ref_counted), align_type_idx, mem_pool_handle
                )
                self.objects.append(mb)

            elif type_name == b"igObjectDirEntry":
                # Actual slot layout: _name=slot2, _typeIndex=slot11, _memoryPoolHandle=slot12
                field_dict = {f[0]: f[1] for f in fields}
                type_idx = field_dict.get(11, 0)

                self.ref_info.append({
                    'is_object': True,
                    'type_index': type_idx,
                    'type_name': self.meta_objects[type_idx].name if type_idx < len(self.meta_objects) else None,
                    'mem_pool_handle': field_dict.get(12, -1),
                })

                obj = IGBObject(len(self.objects), self.meta_objects[type_idx])
                self.objects.append(obj)

            else:
                raise ValueError(f"Unexpected directory entry type: {type_name!r}")

    def _read_objects(self, pos):
        """Parse the object buffer: deserialize field data for all object entries."""
        endian = self.header.endian
        obj_buf_size = self.header.obj_buffer_size
        buf = self.view[pos:pos + obj_buf_size]
        buf_offset = 0

        for i, ri in enumerate(self.ref_info):
            if not ri['is_object']:
                continue

            obj = self.objects[i]

            ent_type, ent_size = struct.unpack_from(endian + "II", buf, buf_offset)
            ent_data = buf[buf_offset + 8:buf_offset + ent_size]

            # Update the object's meta-object to the actual type from the object buffer
            # (the entry's _typeIndex often points to igObject base class,
            #  but the object buffer header has the actual concrete type)
            actual_meta = self.meta_objects[ent_type]
            obj.meta_object = actual_meta
            ri['type_index'] = ent_type
            ri['type_name'] = actual_meta.name

            is_obj_list = self._check_is_obj_list(i)

            field_info = actual_meta.fields
            data_offset = 0

            ent_data_len = len(ent_data)

            for fi in field_info:
                short_name = fi.short_name
                size = fi.size
                slot = fi.slot

                # v8 may serialize fewer fields than the meta-object defines.
                # Stop when we've consumed all available data.
                if data_offset + size > ent_data_len:
                    break

                val = self._deserialize_field(
                    short_name, size, ent_data, data_offset, endian
                )

                # Handle String fields
                actual_size = size
                if short_name == b"String":
                    if self.header.version >= 8:
                        # v8+: String fields store a name pool index (4 bytes)
                        # The actual string is looked up from the name pool
                        pool_idx = struct.unpack_from(endian + "i", ent_data, data_offset)[0]
                        actual_size = 4  # just the index, no inline data
                        if 0 <= pool_idx < len(self.name_pool):
                            val = self.name_pool[pool_idx]
                            if isinstance(val, bytes):
                                try:
                                    val = val.decode("utf-8")
                                except UnicodeDecodeError:
                                    try:
                                        val = val.decode("latin-1")
                                    except UnicodeDecodeError:
                                        pass
                        else:
                            val = ""
                    else:
                        # v5-v7: String fields have inline data (4-byte length + string bytes)
                        str_len = struct.unpack_from(endian + "i", ent_data, data_offset)[0]
                        actual_size = size + str_len
                        if data_offset + actual_size > ent_data_len:
                            break  # truncated string
                        val = bytes(ent_data[data_offset + 4:data_offset + actual_size]).rstrip(b"\0")
                        try:
                            val = val.decode("utf-8")
                        except UnicodeDecodeError:
                            try:
                                val = val.decode("latin-1")
                            except UnicodeDecodeError:
                                pass  # keep as bytes

                # Track references for back-reference resolution
                if short_name in (b"ObjectRef", b"MemoryRef") and val != -1:
                    if 0 <= val < len(self.objects):
                        self.back_refs.setdefault(val, set())
                        self.back_refs[val].add(i)
                        if is_obj_list:
                            self._obj_list_data.add(val)

                obj.fields_by_slot[slot] = val
                obj.fields_by_name[short_name] = val
                obj._raw_fields.append((slot, val, fi))

                # Advance with 4-byte alignment
                data_offset += (actual_size + 3) & ~3

            buf_offset += ent_size

        return pos + obj_buf_size

    def _deserialize_field(self, short_name, size, data, offset, endian):
        """Deserialize a single field value based on its type.

        Args:
            short_name: field type short name (e.g. b"Float", b"Vec3f")
            size: byte size from the field descriptor
            data: buffer containing the field data
            offset: byte offset into data
            endian: byte order

        Returns:
            Deserialized value (int, float, tuple, bytes, etc.)
        """
        # Check fixed-format types first
        fmt = FIELD_TYPE_FORMATS.get(short_name)
        if fmt is not None:
            result = struct.unpack_from(endian + fmt, data, offset)
            if len(result) == 1:
                return result[0]
            return result

        # Variable-size array types
        if short_name == b"CharArray":
            return bytes(data[offset:offset + size])

        if short_name == b"UnsignedCharArray":
            return bytes(data[offset:offset + size])

        if short_name == b"FloatArray":
            count = size // 4
            return struct.unpack_from(endian + "f" * count, data, offset)

        if short_name == b"IntArray":
            count = size // 4
            return struct.unpack_from(endian + "i" * count, data, offset)

        if short_name == b"UnsignedIntArray":
            count = size // 4
            return struct.unpack_from(endian + "I" * count, data, offset)

        if short_name == b"ShortArray":
            count = size // 2
            return struct.unpack_from(endian + "h" * count, data, offset)

        if short_name == b"UnsignedShortArray":
            count = size // 2
            return struct.unpack_from(endian + "H" * count, data, offset)

        # String type: first 4 bytes are length, followed by string data
        # This is handled specially in _read_objects, so here just return the length
        if short_name == b"String":
            return struct.unpack_from(endian + "i", data, offset)[0]

        # ObjectRef and MemoryRef: 4-byte index (-1 means null)
        if short_name in (b"ObjectRef", b"MemoryRef"):
            return struct.unpack_from(endian + "i", data, offset)[0]

        # Fallback: interpret based on size
        fmt_by_size = {1: "B", 2: "h", 4: "i"}
        if size in fmt_by_size:
            return struct.unpack_from(endian + fmt_by_size[size], data, offset)[0]

        # Unknown type: return raw bytes
        return bytes(data[offset:offset + size])

    def _read_memory_refs(self, pos):
        """Parse the memory reference buffer: assign raw data to memory entries."""
        endian = self.header.endian
        mref_buf_size = self.header.mref_buffer_size
        buf = self.view[pos:pos + mref_buf_size]
        buf_offset = 0

        for i, ri in enumerate(self.ref_info):
            if ri['is_object']:
                continue

            mb = self.objects[i]
            size = ri['mem_size']
            mb.data = bytes(buf[buf_offset:buf_offset + size])

            # If this memory block is referenced by an igObjectList,
            # parse it as an array of int32 references
            if i in self._obj_list_data:
                for ref_val in struct.iter_unpack(endian + "i", mb.data):
                    ref_val = ref_val[0]
                    if ref_val == -1:
                        continue
                    if 0 <= ref_val < len(self.objects):
                        self.back_refs.setdefault(ref_val, set())
                        self.back_refs[ref_val].add(i)

            buf_offset += (size + 3) & ~3  # align to 4 bytes

        return pos + mref_buf_size

    def _check_is_obj_list(self, obj_index):
        """Check if the object at the given index is an igObjectList."""
        ri = self.ref_info[obj_index]
        if not ri['is_object']:
            return False
        type_idx = ri['type_index']
        if type_idx >= len(self.meta_objects):
            return False
        chain = self.meta_objects[type_idx].get_inheritance_chain()
        return b"igObjectList" in chain

    # ---- High-level access methods ----

    def get_objects_by_type(self, type_name):
        """Get all objects of a specific type (exact match or subclass)."""
        results = []
        for obj in self.objects:
            if isinstance(obj, IGBObject) and obj.is_type(type_name):
                results.append(obj)
        return results

    def get_object(self, index):
        """Get the object or memory block at a given index."""
        if 0 <= index < len(self.objects):
            return self.objects[index]
        return None

    def resolve_ref(self, ref_index):
        """Resolve an ObjectRef or MemoryRef index to the actual object."""
        if ref_index is None or ref_index == -1:
            return None
        return self.get_object(ref_index)

    def get_info_list(self):
        """Get the root info list object."""
        if self.info_list_index is not None:
            return self.get_object(self.info_list_index)
        return None

    def resolve_object_list(self, obj):
        """Resolve an igObjectList to a list of referenced objects.

        An igObjectList has fields: _count, _capacity, _data (MemoryRef).
        The _data memory block contains int32 indices to other objects.
        """
        if not isinstance(obj, IGBObject):
            return []

        count = None
        data_ref = None

        for slot, val, fi in obj._raw_fields:
            name = fi.short_name
            if name == b"Int" and count is None:
                count = val  # _count is first Int field
            elif name == b"MemoryRef":
                data_ref = val

        if count is None or count == 0 or data_ref is None or data_ref == -1:
            return []

        mem_block = self.resolve_ref(data_ref)
        if not isinstance(mem_block, IGBMemoryBlock) or mem_block.data is None:
            return []

        endian = self.header.endian
        refs = []
        for j in range(min(count, len(mem_block.data) // 4)):
            ref_idx = struct.unpack_from(endian + "i", mem_block.data, j * 4)[0]
            if ref_idx == -1:
                refs.append(None)
            else:
                refs.append(self.resolve_ref(ref_idx))

        return refs

    def resolve_data_list(self, obj, elem_format="i"):
        """Resolve an igDataList subclass to a list of typed values.

        Args:
            obj: IGBObject that is an igDataList subclass
            elem_format: struct format for each element (e.g. "f" for float, "i" for int)

        Returns:
            list of deserialized values
        """
        if not isinstance(obj, IGBObject):
            return []

        count = None
        data_ref = None

        for slot, val, fi in obj._raw_fields:
            name = fi.short_name
            if name == b"Int" and count is None:
                count = val
            elif name == b"MemoryRef":
                data_ref = val

        if count is None or count == 0 or data_ref is None or data_ref == -1:
            return []

        mem_block = self.resolve_ref(data_ref)
        if not isinstance(mem_block, IGBMemoryBlock) or mem_block.data is None:
            return []

        endian = self.header.endian
        elem_size = struct.calcsize(elem_format)
        results = []
        for j in range(min(count, len(mem_block.data) // elem_size)):
            val = struct.unpack_from(endian + elem_format, mem_block.data, j * elem_size)
            if len(val) == 1:
                val = val[0]
            results.append(val)

        return results

    def dump_tree(self, max_depth=3):
        """Print a summary tree of the parsed IGB file for debugging."""
        print(f"=== IGB File: {self.filepath} ===")
        print(f"Header: {self.header}")
        print(f"Meta-fields: {len(self.meta_fields)}")
        print(f"Meta-objects: {len(self.meta_objects)}")
        print(f"Total entries: {len(self.objects)}")
        print(f"External dirs: {self.external_dirs}")
        print(f"Memory pool names: {self.memory_pool_names}")
        print(f"Info list index: {self.info_list_index}")
        print()

        # Count objects by type
        type_counts = {}
        for obj in self.objects:
            if isinstance(obj, IGBObject):
                name = obj.type_name
            elif isinstance(obj, IGBMemoryBlock):
                name = b"[MemoryBlock]"
            else:
                name = b"[None]"
            type_counts[name] = type_counts.get(name, 0) + 1

        print("Object type counts:")
        for name, count in sorted(type_counts.items(), key=lambda x: -x[1]):
            print(f"  {name!s:40s} {count:6d}")
        print()

        # Print info list if available
        if self.info_list_index is not None:
            info = self.get_info_list()
            if info is not None:
                print(f"Info list object: {info}")
                self._print_object_tree(info, depth=0, max_depth=max_depth, visited=set())

    def _print_object_tree(self, obj, depth=0, max_depth=3, visited=None):
        """Recursively print an object tree for debugging."""
        if visited is None:
            visited = set()
        if depth > max_depth:
            return

        indent = "  " * (depth + 1)

        if isinstance(obj, IGBObject):
            if obj.index in visited:
                print(f"{indent}[circular ref -> {obj.index}: {obj.type_name!r}]")
                return
            visited.add(obj.index)

            for slot, val, fi in obj._raw_fields:
                name = fi.short_name
                if name in (b"ObjectRef", b"MemoryRef") and val != -1:
                    ref_obj = self.resolve_ref(val)
                    if isinstance(ref_obj, IGBObject):
                        print(f"{indent}slot {slot} ({name!r}): -> [{val}] {ref_obj.type_name!r}")
                        self._print_object_tree(ref_obj, depth + 1, max_depth, visited)
                    elif isinstance(ref_obj, IGBMemoryBlock):
                        print(f"{indent}slot {slot} ({name!r}): -> [{val}] MemBlock({ref_obj.mem_size}b)")
                    else:
                        print(f"{indent}slot {slot} ({name!r}): -> [{val}] ???")
                else:
                    val_str = repr(val)
                    if len(val_str) > 80:
                        val_str = val_str[:80] + "..."
                    print(f"{indent}slot {slot} ({name!r}): {val_str}")
