"""IGZ file reader - parses header, sections, fixup tables, and objects.

IGZ is the Alchemy 5.0+ binary format used by MUA2 PC, Crash NST, etc.
It is NOT compressed IGB - it's a completely different binary format.

File layout:
  Bytes 0-15:   Header (magic, version, platform, unused)
  Bytes 16+:    ChunkInfo table (16 bytes each: offset, size, alignment, identifier)
  Padding:      Memory pool name strings (null-terminated)
  Section 0:    Fixup section (type registry + reference tables)
  Section 1+:   Data sections (Default=objects, Vertex, System, Image)

Version differences:
  v6 (MUA2 PC): Fixup blocks in implicit order, no ASCII tags, nibble decode uses +1
  v10 (Crash NST): Fixup blocks with 4-char ASCII tags, nibble decode without +1
"""

import struct
import os


# --- Constants ---

IGZ_MAGIC = 0x49475A01  # "\x01ZGI" in little-endian

# Fixed order of fixup blocks in v6 format (no ASCII tags - implied by position)
V6_FIXUP_ORDER = [
    'TSTR', 'TMET', 'MTSZ', 'EXNM', 'EXID',
    'RVTB', 'RSTT', 'ROFS', 'RPID', 'REXT',
    'RHND', 'ROOT', 'ONAM', 'NSPC',
]

# Inner header size for the fixup section (v6)
V6_FIXUP_INNER_HEADER_SIZE = 0x28  # 40 bytes


# --- Data classes ---

class ChunkInfo:
    """Describes a section/chunk in the IGZ file."""
    __slots__ = ('identifier', 'offset', 'size', 'alignment', 'name')

    def __init__(self, offset, size, alignment, identifier):
        self.offset = offset
        self.size = size
        self.alignment = alignment
        self.identifier = identifier
        self.name = ''


class IGZObject:
    """Represents a parsed object from the IGZ Default section."""
    __slots__ = (
        'type_name', 'type_index', 'type_size',
        'global_offset', 'raw_data',
        'references', 'object_name',
    )

    def __init__(self, type_name, type_index, type_size, global_offset):
        self.type_name = type_name
        self.type_index = type_index
        self.type_size = type_size
        self.global_offset = global_offset
        self.raw_data = None
        self.references = {}   # field_offset_within_object -> target IGZObject or int (global offset)
        self.object_name = None

    def read_u8(self, offset):
        return self.raw_data[offset]

    def read_u16(self, offset):
        return struct.unpack_from('<H', self.raw_data, offset)[0]

    def read_i32(self, offset):
        return struct.unpack_from('<i', self.raw_data, offset)[0]

    def read_u32(self, offset):
        return struct.unpack_from('<I', self.raw_data, offset)[0]

    def read_u64(self, offset):
        return struct.unpack_from('<Q', self.raw_data, offset)[0]

    def read_f32(self, offset):
        return struct.unpack_from('<f', self.raw_data, offset)[0]

    def read_vec3f(self, offset):
        return struct.unpack_from('<3f', self.raw_data, offset)

    def read_vec4f(self, offset):
        return struct.unpack_from('<4f', self.raw_data, offset)

    def get_ref(self, field_offset):
        """Get the IGZObject referenced at a field offset, or None."""
        ref = self.references.get(field_offset)
        if isinstance(ref, IGZObject):
            return ref
        return None

    def get_raw_ref(self, field_offset):
        """Get raw reference value (IGZObject or global offset int), or None."""
        return self.references.get(field_offset)

    def __repr__(self):
        name = f' "{self.object_name}"' if self.object_name else ''
        return f'<IGZObject {self.type_name}{name} @0x{self.global_offset:X} ({self.type_size}B)>'


class FixupData:
    """Container for all parsed fixup tables."""
    __slots__ = (
        'tstr', 'tmet', 'mtsz',
        'rvtb', 'rofs', 'rstt', 'root', 'onam',
        'rnex', 'rext', 'rhnd',
        'exnm', 'exid',
        'rofs_set', 'rstt_set', 'rnex_set', 'rext_set', 'rhnd_set',
    )

    def __init__(self):
        self.tstr = []
        self.tmet = []
        self.mtsz = []
        self.rvtb = []    # encoded offsets (not resolved to global)
        self.rofs = []    # global offsets of pointer fields
        self.rstt = []    # global offsets of string fields
        self.root = []    # encoded offsets
        self.onam = []    # encoded offsets
        self.rnex = []    # global offsets of external named refs
        self.rext = []    # global offsets of external hashed refs
        self.rhnd = []    # global offsets of handle refs
        self.exnm = []    # list of (obj_hash, file_hash) tuples
        self.exid = []    # list of (obj_hash, file_hash) tuples
        self.rofs_set = set()
        self.rstt_set = set()
        self.rnex_set = set()
        self.rext_set = set()
        self.rhnd_set = set()


# --- Nibble decoder ---

def _decode_nibbles(data, offset, count, add_one=True):
    """Decode a delta-encoded nibble stream into a sorted list of offsets.

    Each nibble (4 bits) has:
      - Bit 3 (0x8): continuation flag (1=continue, 0=complete this value)
      - Bits 0-2 (0x7): 3 data bits

    Values accumulate via shifting, then:
      - v6: result = previous + (value + 1) * 4  (add_one=True)
      - v10: result = previous + value * 4        (add_one=False)
    """
    result = []
    pos = offset
    current_value = 0
    current_shift = 0

    while len(result) < count:
        if pos >= len(data):
            break
        byte = data[pos]
        pos += 1

        for nibble_idx in range(2):
            nibble = (byte >> (nibble_idx * 4)) & 0xF
            data_bits = nibble & 0x7
            continuation = nibble & 0x8

            current_value |= (data_bits << current_shift)
            current_shift += 3

            if continuation == 0:
                last = result[-1] if result else 0
                if add_one:
                    result.append(last + (current_value + 1) * 4)
                else:
                    result.append(last + current_value * 4)

                if len(result) >= count:
                    break

                current_value = 0
                current_shift = 0

    return result


# --- IGZ Reader ---

class IGZReader:
    """Reads and parses an IGZ format file."""

    def __init__(self, filepath):
        self.filepath = filepath
        self.filename = os.path.basename(filepath)
        self.data = None
        self.version = 0
        self.platform = 0
        self.chunks = []
        self.pool_names = []
        self.fixups = FixupData()
        self.objects = {}       # global_offset -> IGZObject
        self.objects_by_type = {}  # type_name -> [IGZObject]

    def read(self):
        """Read and parse the entire IGZ file."""
        with open(self.filepath, 'rb') as f:
            self.data = f.read()

        self._parse_header()
        self._parse_chunk_table()
        self._parse_pool_names()
        self._parse_fixups()
        self._instantiate_objects()
        self._resolve_references()
        self._resolve_names()

    def _parse_header(self):
        """Parse the 16-byte IGZ header (magic, version, platform, unused)."""
        if len(self.data) < 16:
            raise ValueError(f"File too small for IGZ header: {len(self.data)} bytes")

        magic = struct.unpack_from('<I', self.data, 0)[0]
        if magic != IGZ_MAGIC:
            raise ValueError(
                f"Invalid IGZ magic: 0x{magic:08X} (expected 0x{IGZ_MAGIC:08X})")

        self.version = struct.unpack_from('<I', self.data, 4)[0]
        self.platform = struct.unpack_from('<I', self.data, 8)[0]

    def _parse_chunk_table(self):
        """Parse the ChunkInfo table (16 bytes each: offset, size, alignment, id)."""
        pos = 0x10  # After 16-byte header

        while pos + 16 <= len(self.data):
            off, size, align, ident = struct.unpack_from('<4I', self.data, pos)

            if off == 0:
                break

            chunk = ChunkInfo(off, size, align, ident)
            self.chunks.append(chunk)
            pos += 16

    def _parse_pool_names(self):
        """Parse memory pool name strings between chunk table and first section."""
        if not self.chunks:
            return

        # Find the padding area between chunk table end and first section
        chunk_table_end = 0x10 + len(self.chunks) * 16 + 16
        first_section = self.chunks[0].offset

        # Scan for the first non-zero byte
        name_start = None
        for i in range(chunk_table_end, min(first_section, len(self.data))):
            if self.data[i] != 0:
                name_start = i
                break

        if name_start is None:
            return

        # Read null-terminated strings, one per unique chunk identifier
        pos = name_start
        seen_ids = set()
        for chunk in self.chunks:
            if chunk.identifier in seen_ids:
                for prev in self.chunks:
                    if prev.identifier == chunk.identifier and prev.name:
                        chunk.name = prev.name
                        break
                continue

            seen_ids.add(chunk.identifier)

            if pos >= first_section:
                break

            try:
                end = self.data.index(0, pos)
            except ValueError:
                break
            name = self.data[pos:end].decode('ascii', errors='replace')
            chunk.name = name
            self.pool_names.append(name)
            pos = end + 1

    def _parse_fixups(self):
        """Parse the fixup section (section 0).

        v6 format: 0x28-byte inner header, then 14 fixup blocks in fixed order.
        Each block: count(4) + totalSize(4) + unused(4) + data.
        """
        if not self.chunks:
            return

        fixup_chunk = self.chunks[0]
        base = fixup_chunk.offset

        # Validate inner magic
        inner_magic = struct.unpack_from('<I', self.data, base)[0]
        if inner_magic != IGZ_MAGIC:
            raise ValueError("Fixup section inner header has wrong magic")

        # Read number of fixup blocks from inner header
        num_fixup_blocks = struct.unpack_from('<I', self.data, base + 0x10)[0]

        # Fixup blocks start after the inner header
        pos = base + V6_FIXUP_INNER_HEADER_SIZE

        add_one = (self.version <= 8)  # v6 uses +1, v10 doesn't

        for block_idx in range(num_fixup_blocks):
            if pos + 12 > len(self.data):
                break

            count, total_size, _ = struct.unpack_from('<III', self.data, pos)
            data_start = pos + 12
            data_end = pos + total_size

            if block_idx < len(V6_FIXUP_ORDER):
                fixup_name = V6_FIXUP_ORDER[block_idx]
                self._parse_fixup_block(fixup_name, count, data_start, data_end,
                                        add_one)

            pos += total_size

    def _parse_fixup_block(self, name, count, data_start, data_end, add_one):
        """Parse a single fixup block by type name."""
        if name == 'TSTR':
            self._parse_string_table(self.fixups.tstr, data_start, data_end, count)
        elif name == 'TMET':
            self._parse_string_table(self.fixups.tmet, data_start, data_end, count)
        elif name == 'MTSZ':
            self._parse_mtsz(data_start, count)
        elif name == 'EXNM':
            self._parse_hash_pairs(self.fixups.exnm, data_start, count)
        elif name == 'EXID':
            self._parse_hash_pairs(self.fixups.exid, data_start, count)
        elif name in ('RVTB', 'RSTT', 'ROFS', 'RPID', 'REXT', 'RHND',
                       'ROOT', 'ONAM', 'NSPC'):
            self._parse_r_fixup(name, data_start, count, add_one)

    def _parse_string_table(self, target_list, start, end, count):
        """Parse null-terminated strings with optional padding byte between entries."""
        pos = start
        for _ in range(count):
            if pos >= end:
                break
            try:
                null_pos = self.data.index(0, pos, end)
            except ValueError:
                null_pos = end
            s = self.data[pos:null_pos].decode('ascii', errors='replace')
            target_list.append(s)
            pos = null_pos + 1
            # Skip padding null (same as Crash NST: if PeekChar() == '\0', read it)
            if pos < end and self.data[pos] == 0:
                pos += 1

    def _parse_mtsz(self, start, count):
        """Parse MTSZ (type sizes) - array of uint32."""
        for i in range(count):
            size = struct.unpack_from('<I', self.data, start + i * 4)[0]
            self.fixups.mtsz.append(size)

    def _parse_hash_pairs(self, target_list, start, count):
        """Parse pairs of uint32 hashes (EXNM or EXID format)."""
        for i in range(count):
            h1 = struct.unpack_from('<I', self.data, start + i * 8)[0]
            h2 = struct.unpack_from('<I', self.data, start + i * 8 + 4)[0]
            target_list.append((h1, h2))

    def _parse_r_fixup(self, name, data_start, count, add_one):
        """Parse an R-type fixup (nibble-encoded offset list)."""
        offsets = _decode_nibbles(self.data, data_start, count, add_one)

        if name == 'RVTB':
            self.fixups.rvtb = offsets
        elif name == 'ROFS':
            self.fixups.rofs = [self._get_global_offset(o) for o in offsets]
            self.fixups.rofs_set = set(self.fixups.rofs)
        elif name == 'RSTT':
            self.fixups.rstt = [self._get_global_offset(o) for o in offsets]
            self.fixups.rstt_set = set(self.fixups.rstt)
        elif name == 'ROOT':
            self.fixups.root = offsets
        elif name == 'ONAM':
            self.fixups.onam = offsets
        elif name == 'RNEX':
            self.fixups.rnex = [self._get_global_offset(o) for o in offsets]
            self.fixups.rnex_set = set(self.fixups.rnex)
        elif name == 'REXT':
            self.fixups.rext = [self._get_global_offset(o) for o in offsets]
            self.fixups.rext_set = set(self.fixups.rext)
        elif name == 'RHND':
            self.fixups.rhnd = [self._get_global_offset(o) for o in offsets]
            self.fixups.rhnd_set = set(self.fixups.rhnd)

    def _get_global_offset(self, encoded_offset):
        """Convert an encoded offset to a global file offset.

        If value <= 0x7FFFFFF: offset relative to chunk[1] (first data section).
        Otherwise: high 5 bits = chunk_index - 1, low 27 bits = local offset.
        """
        if len(self.chunks) < 2:
            return encoded_offset

        if encoded_offset <= 0x7FFFFFF:
            return self.chunks[1].offset + encoded_offset
        else:
            chunk_index = (encoded_offset >> 0x1B) + 1
            local_offset = encoded_offset & 0x7FFFFFF
            if chunk_index < len(self.chunks):
                return self.chunks[chunk_index].offset + local_offset
            return encoded_offset

    def _instantiate_objects(self):
        """Create IGZObject instances from RVTB entries."""
        if not self.fixups.rvtb or not self.fixups.tmet or not self.fixups.mtsz:
            return

        for encoded_offset in self.fixups.rvtb:
            global_offset = self._get_global_offset(encoded_offset)

            if global_offset + 4 > len(self.data):
                continue

            type_index = struct.unpack_from('<I', self.data, global_offset)[0]

            if type_index >= len(self.fixups.tmet):
                continue

            type_name = self.fixups.tmet[type_index]
            type_size = (self.fixups.mtsz[type_index]
                         if type_index < len(self.fixups.mtsz) else 0)

            obj = IGZObject(type_name, type_index, type_size, global_offset)

            if type_size > 0 and global_offset + type_size <= len(self.data):
                obj.raw_data = self.data[global_offset:global_offset + type_size]

            self.objects[global_offset] = obj

            if type_name not in self.objects_by_type:
                self.objects_by_type[type_name] = []
            self.objects_by_type[type_name].append(obj)

    def _resolve_references(self):
        """Use ROFS to resolve pointer fields within objects to target objects."""
        if not self.fixups.rofs_set:
            return

        # Build interval map for fast object lookup
        obj_intervals = []
        for obj in self.objects.values():
            obj_intervals.append((obj.global_offset, obj.global_offset + obj.type_size, obj))
        obj_intervals.sort()

        # For each ROFS offset, find the containing object and resolve the pointer
        for rofs_global in self.fixups.rofs:
            # Binary search for containing object
            owner = None
            lo, hi = 0, len(obj_intervals) - 1
            while lo <= hi:
                mid = (lo + hi) // 2
                start, end, obj = obj_intervals[mid]
                if rofs_global < start:
                    hi = mid - 1
                elif rofs_global >= end:
                    lo = mid + 1
                else:
                    owner = obj
                    break

            if owner is None:
                continue

            field_offset = rofs_global - owner.global_offset
            if field_offset + 4 > owner.type_size:
                continue

            # Read the encoded pointer value (use lower 32 bits)
            ptr_value = struct.unpack_from('<I', owner.raw_data, field_offset)[0]

            # Decode to global offset
            target_global = self._get_global_offset(ptr_value)
            target_obj = self.objects.get(target_global)
            if target_obj is not None:
                owner.references[field_offset] = target_obj
            else:
                # Raw data pointer (vertex buffer, index buffer, etc.)
                owner.references[field_offset] = target_global

    def _resolve_names(self):
        """Resolve object names from ROOT/ONAM fixups and RSTT string refs."""
        # Build a map of global_offset -> (obj, field_offset, tstr_string) for RSTT
        # Use a dict keyed by object global offset for string refs
        string_refs_by_obj = {}  # obj_global_offset -> {field_offset: string}

        if self.fixups.rstt_set and self.fixups.tstr:
            for rstt_global in self.fixups.rstt:
                for obj in self.objects.values():
                    if obj.global_offset <= rstt_global < obj.global_offset + obj.type_size:
                        field_offset = rstt_global - obj.global_offset
                        if field_offset + 4 <= obj.type_size:
                            tstr_index = struct.unpack_from('<I', obj.raw_data, field_offset)[0]
                            if tstr_index < len(self.fixups.tstr):
                                if obj.global_offset not in string_refs_by_obj:
                                    string_refs_by_obj[obj.global_offset] = {}
                                string_refs_by_obj[obj.global_offset][field_offset] = \
                                    self.fixups.tstr[tstr_index]
                        break

        self._string_refs_by_obj = string_refs_by_obj

        # Try to resolve object names from ROOT/ONAM
        if self.fixups.root and self.fixups.onam:
            root_global = self._get_global_offset(self.fixups.root[0])
            onam_global = self._get_global_offset(self.fixups.onam[0])

            root_obj = self.objects.get(root_global)
            onam_obj = self.objects.get(onam_global)

            if root_obj is not None and onam_obj is not None:
                self._resolve_list_names(root_obj, onam_obj)

    def _resolve_list_names(self, root_obj, name_obj):
        """Match igObjectList entries to igNameList entries."""
        # Collect object refs from root_obj
        obj_refs = []
        for fo in sorted(root_obj.references.keys()):
            ref = root_obj.references[fo]
            if isinstance(ref, IGZObject):
                obj_refs.append(ref)

        # Collect name strings from name_obj via RSTT
        name_strings = []
        name_str_refs = self._string_refs_by_obj.get(name_obj.global_offset, {})
        for fo in sorted(name_str_refs.keys()):
            name_strings.append(name_str_refs[fo])

        for i, obj in enumerate(obj_refs):
            if i < len(name_strings):
                obj.object_name = name_strings[i]

    def get_objects_by_type(self, type_name):
        """Get all objects of a given type name."""
        return self.objects_by_type.get(type_name, [])

    def get_data_at(self, global_offset, size):
        """Read raw bytes from the file at a global offset."""
        if global_offset + size <= len(self.data):
            return self.data[global_offset:global_offset + size]
        return None

    def get_section_data(self, section_name):
        """Get the raw data for a named section."""
        for chunk in self.chunks:
            if chunk.name == section_name:
                return self.data[chunk.offset:chunk.offset + chunk.size]
        return None

    def get_section_offset(self, section_name):
        """Get the file offset of a named section."""
        for chunk in self.chunks:
            if chunk.name == section_name:
                return chunk.offset
        return None
