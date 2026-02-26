"""Enbaya animation compression encoder.

Ported from korenkonder/enbrip (MIT License) — C source at
github_refs/enbrip/src/enbaya.c

Compresses per-bone quaternion + translation keyframes into the compact
enbaya binary format used by Alchemy Engine (Vicarious Visions).  The
output is a self-contained blob compatible with igEnbayaAnimationSource.

Encoding pipeline:
    1. Resample tracks to fixed sample rate (SLERP quats, LERP trans)
    2. Flip quaternions for shortest-path consistency
    3. Quantize float values to int32 via quantization_error
    4. Compute second-order deltas (delta-of-deltas)
    5. Identify inactive component ranges (state machine optimisation)
    6. Pack into 14 variable-length sub-streams
    7. Assemble 80-byte header + concatenated sub-stream data
"""

import math
import struct
from typing import List, Tuple, Optional

# ---------------------------------------------------------------------------
# Lookup tables (from enbaya.c lines 211-216)
# ---------------------------------------------------------------------------
_VALUE_TABLE_I2 = [0, 1, 0, -1]        # 2-bit decode: 0→0, 1→+1, 2→(next), 3→-1
_VALUE_TABLE_I4 = [
    0, 8, 2, 3, 4, 5, 6, 7,
    -8, -7, -6, -5, -4, -3, -2, -9,
]

# Reverse lookup for encoding: value → i4 nibble
_I4_ENCODE = {}
for _idx, _val in enumerate(_VALUE_TABLE_I4):
    if _val != 0:  # 0 means "escape to next level"
        _I4_ENCODE[_val] = _idx


# ---------------------------------------------------------------------------
# Bit / byte stream writers
# ---------------------------------------------------------------------------
class _ByteStreamWriter:
    """Accumulates raw bytes."""

    __slots__ = ('_chunks',)

    def __init__(self):
        self._chunks: list = []

    def put_i8(self, v: int):
        self._chunks.append(struct.pack('<b', v))

    def put_u8(self, v: int):
        self._chunks.append(struct.pack('<B', v))

    def put_i16(self, v: int):
        self._chunks.append(struct.pack('<h', v))

    def put_u16(self, v: int):
        self._chunks.append(struct.pack('<H', v))

    def put_i32(self, v: int):
        self._chunks.append(struct.pack('<i', v))

    def put_u32(self, v: int):
        self._chunks.append(struct.pack('<I', v))

    def get_bytes(self) -> bytes:
        return b''.join(self._chunks)

    def __len__(self):
        return sum(len(c) for c in self._chunks)


class _BitWriter2:
    """Packs 2-bit values into bytes (MSB first, 4 values per byte)."""

    __slots__ = ('_stream', '_temp', '_counter')

    def __init__(self):
        self._stream = _ByteStreamWriter()
        self._temp = 0
        self._counter = 0

    def put(self, v: int):
        shift = 2 * (4 - self._counter - 1)
        self._temp |= (v & 0x03) << shift
        self._counter += 1
        if self._counter == 4:
            self._stream.put_u8(self._temp)
            self._temp = 0
            self._counter = 0

    def get_bytes(self) -> bytes:
        # Flush partial byte
        if self._counter > 0:
            self._stream.put_u8(self._temp)
            self._temp = 0
            self._counter = 0
        return self._stream.get_bytes()


class _BitWriter4:
    """Packs 4-bit values into bytes (MSB first, 2 values per byte)."""

    __slots__ = ('_stream', '_temp', '_counter')

    def __init__(self):
        self._stream = _ByteStreamWriter()
        self._temp = 0
        self._counter = 0

    def put(self, v: int):
        shift = 4 * (2 - self._counter - 1)
        self._temp |= (v & 0x0F) << shift
        self._counter += 1
        if self._counter == 2:
            self._stream.put_u8(self._temp)
            self._temp = 0
            self._counter = 0

    def get_bytes(self) -> bytes:
        if self._counter > 0:
            self._stream.put_u8(self._temp)
            self._temp = 0
            self._counter = 0
        return self._stream.get_bytes()


# ---------------------------------------------------------------------------
# Stream encoders (matching C struct-based encoders)
# ---------------------------------------------------------------------------
class _InitStreamEncoder:
    """Encodes init values (frame 0) with 2-bit type selector."""

    def __init__(self):
        self.i2 = _BitWriter2()
        self.i8 = _ByteStreamWriter()
        self.i16 = _ByteStreamWriter()
        self.i32 = _ByteStreamWriter()

    def put(self, value: int):
        if value == 0:
            self.i2.put(0)
        elif -0x80 <= value <= 0x7F:
            self.i2.put(1)
            self.i8.put_i8(value)
        elif -0x8000 <= value <= 0x7FFF:
            self.i2.put(2)
            self.i16.put_i16(value)
        else:
            self.i2.put(3)
            self.i32.put_i32(value)


class _TrackStreamEncoder:
    """Encodes per-frame track deltas with cascading variable-length coding.

    Cascade: 2-bit → 4-bit → i8 → i16 → i32
    """

    def __init__(self):
        self.i2 = _BitWriter2()
        self.i4 = _BitWriter4()
        self.i8 = _ByteStreamWriter()
        self.i16 = _ByteStreamWriter()
        self.i32 = _ByteStreamWriter()

    def put(self, value: int):
        # Level 1: 2-bit (values -1, 0, +1)
        if -1 <= value <= 1:
            # Encode: 0→0, 1→+1, 3→-1  (value_table_track_data_i2)
            if value == 0:
                self.i2.put(0)
            elif value == 1:
                self.i2.put(1)
            else:  # -1
                self.i2.put(3)
            return

        # Escape to level 2
        self.i2.put(2)

        # Level 2: 4-bit (values -9..+8 excluding -1,0,+1)
        if -9 <= value <= 8:
            if value == -9:
                self.i4.put(0x0F)
            elif value == 8:
                self.i4.put(0x01)
            elif value in _I4_ENCODE:
                self.i4.put(_I4_ENCODE[value])
            else:
                # Shouldn't happen for values in range
                self.i4.put(0x00)
                self._put_i8_level(value)
            return

        # Escape to level 3: i8
        self.i4.put(0x00)

        # Level 3: i8 (with bias for boundary values)
        if -0x80 - 8 <= value <= 0x7F + 8:
            if value > 0x7F:
                self.i8.put_i8(value - 0x7F)
            elif value < -0x80:
                self.i8.put_i8(value + 0x80)
            else:
                self.i8.put_i8(value)
            return

        # Escape to level 4: i16
        self.i8.put_i8(0)

        if -0x8000 <= value <= 0x7FFF:
            self.i16.put_i16(value)
            return

        # Escape to level 5: i32
        self.i16.put_i16(0)
        self.i32.put_i32(value)

    def _put_i8_level(self, value: int):
        """Put value at i8 level (called when i4 escapes with 0)."""
        if -0x80 - 8 <= value <= 0x7F + 8:
            if value > 0x7F:
                self.i8.put_i8(value - 0x7F)
            elif value < -0x80:
                self.i8.put_i8(value + 0x80)
            else:
                self.i8.put_i8(value)
        else:
            self.i8.put_i8(0)
            if -0x8000 <= value <= 0x7FFF:
                self.i16.put_i16(value)
            else:
                self.i16.put_i16(0)
                self.i32.put_i32(value)


class _StateStreamEncoder:
    """Encodes state transitions (unsigned) with 2-bit type selector."""

    def __init__(self):
        self.u2 = _BitWriter2()
        self.u8 = _ByteStreamWriter()
        self.u16 = _ByteStreamWriter()
        self.u32 = _ByteStreamWriter()

    def put(self, value: int):
        if value == 0:
            self.u2.put(0)
        elif value <= 0xFF:
            self.u2.put(1)
            self.u8.put_u8(value)
        elif value <= 0xFFFF:
            self.u2.put(2)
            self.u16.put_u16(value)
        else:
            self.u2.put(3)
            self.u32.put_u32(value)


# ---------------------------------------------------------------------------
# Math helpers
# ---------------------------------------------------------------------------
def _slerp_quat(a, b, t):
    """SLERP between two quaternions (x,y,z,w)."""
    ax, ay, az, aw = a
    bx, by, bz, bw = b

    # Normalise
    la = math.sqrt(ax*ax + ay*ay + az*az + aw*aw)
    if la > 0:
        la = 1.0 / la
    ax, ay, az, aw = ax*la, ay*la, az*la, aw*la

    lb = math.sqrt(bx*bx + by*by + bz*bz + bw*bw)
    if lb > 0:
        lb = 1.0 / lb
    bx, by, bz, bw = bx*lb, by*lb, bz*lb, bw*lb

    dot = ax*bx + ay*by + az*bz + aw*bw
    if dot < 0:
        bx, by, bz, bw = -bx, -by, -bz, -bw
        dot = -dot

    if 1.0 - dot <= 0.08:
        # Near-parallel: fall back to LERP
        b0, b1 = 1.0 - t, t
    else:
        if dot > 1.0:
            dot = 1.0
        theta = math.acos(dot)
        if theta == 0:
            return (ax, ay, az, aw)
        st = 1.0 / math.sin(theta)
        b0 = math.sin((1.0 - t) * theta) * st
        b1 = math.sin(t * theta) * st

    rx = b0 * ax + b1 * bx
    ry = b0 * ay + b1 * by
    rz = b0 * az + b1 * bz
    rw = b0 * aw + b1 * bw

    lr = math.sqrt(rx*rx + ry*ry + rz*rz + rw*rw)
    if lr > 0:
        lr = 1.0 / lr
    return (rx*lr, ry*lr, rz*lr, rw*lr)


def _lerp_vec3(a, b, t):
    """LERP between two 3-vectors."""
    b0, b1 = 1.0 - t, t
    return (a[0]*b0 + b[0]*b1, a[1]*b0 + b[1]*b1, a[2]*b0 + b[2]*b1)


# ---------------------------------------------------------------------------
# Encoder pipeline
# ---------------------------------------------------------------------------
def compress_enbaya(track_keyframes, duration, sample_rate=30,
                    quantization_error=0.005):
    """Compress animation keyframes to an enbaya blob.

    Args:
        track_keyframes: List of per-track keyframes.  Each element is a
            list of ``(time_seconds, quat_xyzw, trans_xyz)`` tuples where
            *quat_xyzw* is ``(x, y, z, w)`` and *trans_xyz* is ``(x, y, z)``.
        duration: Animation duration in seconds.
        sample_rate: Target sample rate in Hz (default 30).
        quantization_error: Precision parameter (default 0.005).  Smaller
            values → higher quality, larger blob.

    Returns:
        ``bytes`` — the complete enbaya blob (header + sub-stream data).
    """
    num_tracks = len(track_keyframes)
    if num_tracks == 0:
        raise ValueError("No tracks provided")

    # The header stores doubled quantization_error (C line 1091 + 1396)
    qe_doubled = quantization_error * 2.0

    # 1. Resample all tracks to fixed sample rate
    sps = 1.0 / sample_rate  # seconds per sample
    max_samples = int(duration / sps) + 2
    resampled = _resample_all_tracks(track_keyframes, num_tracks, duration,
                                     sample_rate, max_samples)

    # Pad all tracks to same length (largest track)
    largest_count = max(len(t) for t in resampled)

    # 2. Flip rotations for shortest-path consistency
    for track in resampled:
        _flip_rotations(track)

    # 3. Quantize to integers
    quantized = _quantize_all_tracks(resampled, num_tracks, qe_doubled)

    # 4+5. Compute second-order deltas and find inactive ranges
    samples_per_track = _compute_samples(quantized, num_tracks, largest_count)

    # 5b. Find inactive ranges
    for track_samples in samples_per_track:
        _find_value_ranges(track_samples, len(track_samples), 9)

    # 6. Encode to sub-streams
    init_enc = _InitStreamEncoder()
    track_enc = _TrackStreamEncoder()
    state_enc = _StateStreamEncoder()
    flags_stream = _ByteStreamWriter()

    _encode_streams(samples_per_track, num_tracks, largest_count,
                    init_enc, track_enc, state_enc, flags_stream)

    # 7. Assemble blob
    return _assemble_blob(
        num_tracks, qe_doubled, duration, sample_rate,
        init_enc, track_enc, state_enc, flags_stream
    )


def _resample_all_tracks(track_keyframes, num_tracks, duration,
                          sample_rate, max_samples):
    """Resample all tracks to fixed sample rate.

    Returns list of lists: each inner list contains (qx, qy, qz, qw, tx, ty, tz)
    tuples (7 floats per sample).
    """
    sps = 1.0 / sample_rate
    result = []

    for track_id in range(num_tracks):
        kfs = track_keyframes[track_id]
        if not kfs:
            # Empty track: fill with identity
            samples = [(0.0, 0.0, 0.0, 1.0, 0.0, 0.0, 0.0)] * max_samples
            result.append(samples)
            continue

        samples = []

        # Frame 0: first keyframe
        kf0 = kfs[0]
        q0, t0 = kf0[1], kf0[2]
        samples.append((q0[0], q0[1], q0[2], q0[3], t0[0], t0[1], t0[2]))

        # Intermediate frames
        j = 1  # index into original keyframes
        for i in range(1, max_samples - 1):
            time = float(i) * sps
            # Advance j until we bracket the time
            while j < len(kfs) and kfs[j][0] < time:
                j += 1
            if j >= len(kfs):
                j = len(kfs) - 1

            prev_kf = kfs[j - 1] if j > 0 else kfs[0]
            next_kf = kfs[j]

            dt = next_kf[0] - prev_kf[0]
            if dt > 1e-9:
                blend = (time - prev_kf[0]) / dt
            else:
                blend = 0.0

            # SLERP quaternion, LERP translation
            q = _slerp_quat(prev_kf[1], next_kf[1], blend)
            t = _lerp_vec3(prev_kf[2], next_kf[2], blend)
            samples.append((q[0], q[1], q[2], q[3], t[0], t[1], t[2]))

        # Last frame: last keyframe
        kf_last = kfs[-1]
        ql, tl = kf_last[1], kf_last[2]
        samples.append((ql[0], ql[1], ql[2], ql[3], tl[0], tl[1], tl[2]))

        result.append(samples)

    return result


def _flip_rotations(samples):
    """Flip quaternions for shortest-path consistency (C line 1140)."""
    for i in range(1, len(samples)):
        prev_w = samples[i - 1][3]
        curr_w = samples[i][3]
        if prev_w * curr_w < 0 and abs(curr_w - prev_w) > 0.1:
            s = samples[i]
            # Flip to equivalent quaternion via angle manipulation
            theta = math.acos(max(-1.0, min(1.0, s[3])))
            sin_theta = math.sin(theta)
            if abs(sin_theta) > 1e-6:
                ax = s[0] / sin_theta
                ay = s[1] / sin_theta
                az = s[2] / sin_theta
                theta *= 2.0
                if theta <= 0:
                    theta += math.pi * 2.0
                else:
                    theta -= math.pi * 2.0
                theta /= 2.0
                cos_val = math.cos(theta)
                sin_val = math.sin(theta)
                samples[i] = (
                    float(sin_val * ax), float(sin_val * ay),
                    float(sin_val * az), float(cos_val),
                    s[4], s[5], s[6],
                )


def _quantize_all_tracks(resampled, num_tracks, qe):
    """Quantize all tracks to integer values.

    Returns list of lists of 7-int tuples.
    """
    result = []
    for track_id in range(num_tracks):
        qsamples = []
        for s in resampled[track_id]:
            qx, qy, qz, qw = s[0], s[1], s[2], s[3]
            # Normalise quaternion
            length = math.sqrt(qx*qx + qy*qy + qz*qz + qw*qw)
            if length > 0:
                inv_l = 1.0 / length
            else:
                inv_l = 0.0

            iq = (
                int(qx * inv_l / qe),
                int(qy * inv_l / qe),
                int(qz * inv_l / qe),
                int(qw * inv_l / qe),
                int(s[4] / qe),
                int(s[5] / qe),
                int(s[6] / qe),
            )
            qsamples.append(iq)
        result.append(qsamples)
    return result


def _compute_samples(quantized, num_tracks, largest_count):
    """Compute second-order delta samples per track.

    Returns list (per track) of lists (per frame) of
    [(value, has_value)] × 7 components.
    """
    all_samples = []

    for track_id in range(num_tracks):
        qdata = quantized[track_id]
        num_frames = len(qdata)

        # Pad to largest_count if needed
        while len(qdata) < largest_count:
            qdata.append(qdata[-1] if qdata else (0, 0, 0, 0, 0, 0, 0))

        samples = []
        for comp in range(7):
            prev_delta = 0
            prev_val = qdata[0][comp]

            # Frame 0: raw value
            comp_samples = [(prev_val, True)]

            for j in range(1, largest_count):
                cur_val = qdata[j][comp] if j < len(qdata) else qdata[-1][comp]
                delta = cur_val - prev_val
                dod = delta - prev_delta  # delta-of-delta
                comp_samples.append((dod, True))
                prev_val = cur_val
                prev_delta = delta

            # Store per-component
            if not samples:
                samples = [[] for _ in range(largest_count)]
            for f in range(largest_count):
                if len(samples[f]) < 7:
                    samples[f].append(comp_samples[f])
                else:
                    samples[f][comp] = comp_samples[f]

        all_samples.append(samples)

    return all_samples


def _find_value_ranges(samples, size, min_range_size):
    """Mark inactive component ranges (C line 986).

    Modifies samples in-place: sets has_value=False for long zero runs.
    """
    for comp in range(7):
        # Check if component has any non-zero values (skip frame 0)
        no_value = True
        for j in range(1, size):
            if samples[j][comp][0] != 0:
                no_value = False
                break

        if no_value:
            for j in range(1, size):
                samples[j][comp] = (samples[j][comp][0], False)
            continue

        range_size = 0
        set_no_value = True
        l = 1
        while l < size:
            if samples[l][comp][0] != 0:
                if ((set_no_value and range_size > min_range_size // 2) or
                        range_size > min_range_size):
                    for k in range(l - 1, l - 1 - range_size, -1):
                        samples[k][comp] = (samples[k][comp][0], False)
                range_size = 0
                set_no_value = False
            else:
                range_size += 1
            l += 1

        if range_size > min_range_size // 2:
            for k in range(l - 1, l - 1 - range_size, -1):
                samples[k][comp] = (samples[k][comp][0], False)


def _encode_streams(samples_per_track, num_tracks, num_samples,
                    init_enc, track_enc, state_enc, flags_stream):
    """Encode all data into the sub-streams (C line 1023)."""

    # Init stream: frame 0 values for all tracks
    if num_samples > 0:
        for track_id in range(num_tracks):
            for comp in range(7):
                init_enc.put(samples_per_track[track_id][0][comp][0])

    # Track data stream: frames 1+ for active components only
    for frame in range(1, num_samples):
        for track_id in range(num_tracks):
            for comp in range(7):
                val, has_value = samples_per_track[track_id][frame][comp]
                if has_value:
                    track_enc.put(val)

    # Track flags: initial active-component bitmask per track
    for track_id in range(num_tracks):
        flags = 0x00
        if num_samples > 1:
            for comp in range(7):
                if samples_per_track[track_id][1][comp][1]:  # has_value at frame 1
                    flags |= (1 << comp)
        flags_stream.put_u8(flags)

    # State stream: track which components toggle between frames
    step = 0
    for frame in range(2, num_samples):
        for track_id in range(num_tracks):
            for comp in range(7):
                step += 1
                cur_active = samples_per_track[track_id][frame][comp][1]
                prev_active = samples_per_track[track_id][frame - 1][comp][1]
                if cur_active != prev_active:
                    state_enc.put(step - 1)
                    step = 0

    # Final state sentinel
    state_enc.put(step + 100)


def _assemble_blob(num_tracks, qe_doubled, duration, sample_rate,
                   init_enc, track_enc, state_enc, flags_stream):
    """Assemble the final enbaya blob.

    Physical layout in memory (matching decoder getter functions):
        [header 80B]
        [init_i32]   [track_i32]   [state_u32]
        [init_i16]   [track_i16]   [state_u16]
        [init_i2]    [init_i8]
        [track_i2]   [track_i4]    [track_i8]
        [state_u2]   [state_u8]
        [track_flags]
    """
    # Collect all sub-stream bytes
    init_i2  = init_enc.i2.get_bytes()
    init_i8  = init_enc.i8.get_bytes()
    init_i16 = init_enc.i16.get_bytes()
    init_i32 = init_enc.i32.get_bytes()

    track_i2  = track_enc.i2.get_bytes()
    track_i4  = track_enc.i4.get_bytes()
    track_i8  = track_enc.i8.get_bytes()
    track_i16 = track_enc.i16.get_bytes()
    track_i32 = track_enc.i32.get_bytes()

    state_u2  = state_enc.u2.get_bytes()
    state_u8  = state_enc.u8.get_bytes()
    state_u16 = state_enc.u16.get_bytes()
    state_u32 = state_enc.u32.get_bytes()

    flags = flags_stream.get_bytes()

    # Build 80-byte header
    # Field order matches enb_anim_stream (enbaya.h lines 18-39)
    header = struct.pack(
        '<'          # little-endian
        'I'          # signature (0)
        'I'          # track_count
        'f'          # quantization_error (doubled)
        'f'          # duration
        'I'          # sample_rate
        'I'          # track_data_init_i2_length
        'I'          # track_data_init_i8_length
        'I'          # track_data_init_i16_length
        'I'          # track_data_init_i32_length
        'I'          # track_data_i2_length
        'I'          # track_data_i4_length
        'I'          # track_data_i8_length
        'I'          # track_data_i16_length     (offset 0x30)
        'I'          # track_data_i32_length     (offset 0x34 — NOTE: header field at 0x34 is actually state_data_u2_length, see below)
        'I'          # state_data_u2_length
        'I'          # state_data_u8_length
        'I'          # state_data_u16_length
        'I'          # state_data_u32_length
        'I'          # track_flags_length
        'I',         # data (runtime pointer, set to 0)
        0,                              # signature
        num_tracks,                     # track_count
        qe_doubled,                     # quantization_error
        duration,                       # duration
        sample_rate,                    # sample_rate
        len(init_i2),                   # track_data_init_i2_length
        len(init_i8),                   # track_data_init_i8_length
        len(init_i16),                  # track_data_init_i16_length
        len(init_i32),                  # track_data_init_i32_length
        len(track_i2),                  # track_data_i2_length
        len(track_i4),                  # track_data_i4_length
        len(track_i8),                  # track_data_i8_length
        len(track_i16),                 # track_data_i16_length
        len(track_i32),                 # track_data_i32_length
        len(state_u2),                  # state_data_u2_length
        len(state_u8),                  # state_data_u8_length
        len(state_u16),                 # state_data_u16_length
        len(state_u32),                 # state_data_u32_length
        len(flags),                     # track_flags_length
        0,                              # data pointer (unused)
    )

    # Concatenate in the exact physical order the decoder expects.
    # Derived from the chain of get_*() functions in enbaya.c:
    #   get_init_i32 → header + sizeof(header)
    #   get_i32      → init_i32 + len(init_i32)
    #   get_state_u32→ i32 + len(i32)
    #   get_init_i16 → state_u32 + len(state_u32)
    #   get_i16      → init_i16 + len(init_i16)
    #   get_state_u16→ i16 + len(i16)
    #   get_init_i2  → state_u16 + len(state_u16)
    #   get_init_i8  → init_i2 + len(init_i2)
    #   get_i2       → init_i8 + len(init_i8)
    #   get_i4       → i2 + len(i2)
    #   get_i8       → i4 + len(i4)
    #   get_state_u2 → i8 + len(i8)
    #   get_state_u8 → state_u2 + len(state_u2)
    #   get_flags    → state_u8 + len(state_u8)
    blob = (
        header +
        init_i32 + track_i32 + state_u32 +
        init_i16 + track_i16 + state_u16 +
        init_i2  + init_i8 +
        track_i2 + track_i4 + track_i8 +
        state_u2 + state_u8 +
        flags
    )

    return blob
