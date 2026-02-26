"""IGB file header parser and writer."""

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
    VERSION_MASK,
)


class IGBHeader:
    """Represents the 48-byte IGB file header."""

    def __init__(self):
        self.endian = "<"  # default little-endian
        self.fields = [0] * 12
        self.version = 0
        self.has_info = False
        self.has_external = False
        self.shared_entries = False
        self.has_memory_pool_names = False

    @property
    def entry_buffer_size(self):
        return self.fields[H_ENTRY_BUFFER_SIZE]

    @property
    def entry_count(self):
        return self.fields[H_ENTRY_COUNT]

    @property
    def meta_obj_buffer_size(self):
        return self.fields[H_META_OBJ_BUFFER_SIZE]

    @property
    def meta_obj_count(self):
        return self.fields[H_META_OBJ_COUNT]

    @property
    def obj_buffer_size(self):
        return self.fields[H_OBJ_BUFFER_SIZE]

    @property
    def obj_count(self):
        return self.fields[H_OBJ_COUNT]

    @property
    def mref_buffer_size(self):
        return self.fields[H_MREF_BUFFER_SIZE]

    @property
    def mref_count(self):
        return self.fields[H_MREF_COUNT]

    @property
    def mf_buffer_size(self):
        return self.fields[H_MF_BUFFER_SIZE]

    @property
    def mf_count(self):
        return self.fields[H_MF_COUNT]

    @classmethod
    def read(cls, data):
        """Read and parse a 48-byte IGB header from raw data.

        Args:
            data: bytes or memoryview of at least HEADER_SIZE bytes

        Returns:
            IGBHeader instance

        Raises:
            ValueError: if data is too small or magic cookie is invalid
        """
        if len(data) < HEADER_SIZE:
            raise ValueError(f"Data too small for IGB header: {len(data)} < {HEADER_SIZE}")

        header = cls()

        # Detect endianness from magic cookie at offset 0x28
        magic_le = struct.unpack_from("<I", data, 0x28)[0]
        if magic_le == IGB_MAGIC_COOKIE:
            header.endian = "<"
        else:
            magic_be = struct.unpack_from(">I", data, 0x28)[0]
            if magic_be == IGB_MAGIC_COOKIE:
                header.endian = ">"
            else:
                raise ValueError(
                    f"Invalid IGB magic cookie: 0x{magic_le:08x} (LE) / 0x{magic_be:08x} (BE)"
                )

        # Parse all 12 uint32 fields
        header.fields = list(struct.unpack_from(header.endian + "I" * 12, data, 0))

        # Extract version and flags from verFlags field
        ver_flags = header.fields[H_VER_FLAGS]
        header.version = ver_flags & VERSION_MASK
        header.has_info = bool(ver_flags & FLAG_HAS_INFO)
        header.has_external = bool(ver_flags & FLAG_HAS_EXTERNAL)
        header.shared_entries = bool(ver_flags & FLAG_SHARED_ENTRIES)
        header.has_memory_pool_names = bool(ver_flags & FLAG_HAS_MEMORY_POOL_NAMES)

        # Validation
        if header.version >= 9:
            raise ValueError(f"Unsupported IGB version: {header.version} (must be < 9)")
        if header.version < 5 and header.has_external:
            raise ValueError(f"IGB v{header.version} cannot have external directories")

        return header

    def write(self):
        """Serialize the header to 48 bytes.

        Returns:
            bytes of length HEADER_SIZE
        """
        return struct.pack(self.endian + "I" * 12, *self.fields)

    def __repr__(self):
        return (
            f"IGBHeader(version={self.version}, endian='{self.endian}', "
            f"entries={self.entry_count}, metaObjs={self.meta_obj_count}, "
            f"objects={self.obj_count}, memRefs={self.mref_count}, "
            f"metaFields={self.mf_count}, "
            f"hasInfo={self.has_info}, hasExternal={self.has_external}, "
            f"sharedEntries={self.shared_entries}, hasMemPoolNames={self.has_memory_pool_names})"
        )
