"""Enbaya animation decompressor — ported from korenkonder/enbrip (MIT License).

Decompresses igEnbayaAnimationSource binary streams into per-track
quaternion + translation keyframes at each sample.

The Enbaya format stores animation data as:
1. A header (enb_anim_stream) with track count, duration, sample rate,
   quantization error, and lengths for 14 variable-length sub-streams.
2. After the header, interleaved sub-streams of different data widths:
   - Initial track data: i32, i16, i8, i2 (for frame 0 values)
   - Per-frame track deltas: i32, i16, i8, i4, i2 (delta-encoded)
   - State data: u32, u16, u8, u2 (which channels are active per frame)
   - Track flags: 1 byte per track (initial active channels bitmask)
3. Decoding:
   - Frame 0: read 7 ints per track (qx,qy,qz,qw, tx,ty,tz) from init streams
   - Multiply by quantization_error → initial quaternion/translation
   - Normalize quaternion
   - Subsequent frames: read second-order delta values, accumulate into
     running-sum accumulators (velocity). multiply velocity by quant_error,
     add to previous values, normalize quaternion. Accumulators persist
     across steps (NOT reset) because deltas are delta-of-deltas.
   - State machine toggles which of the 7 components are active per frame

Reference: https://github.com/korenkonder/enbrip (MIT License, korenkonder 2020-2025)
"""

import struct
import math
from typing import List, Optional, Tuple

# ---- Lookup tables (from the C implementation) ----
_SHIFT_TABLE_I2 = [6, 4, 2, 0]
_SHIFT_TABLE_I4 = [4, 0]
_VALUE_TABLE_I2 = [0, 1, 0, -1]
_VALUE_TABLE_I4 = [0, 8, 2, 3, 4, 5, 6, 7, -8, -7, -6, -5, -4, -3, -2, -9]


# ---- Stream reader helpers ----

class _I2Reader:
    """Read packed 2-bit values from a byte stream."""
    __slots__ = ('data', 'pos', 'counter')

    def __init__(self, data, offset):
        self.data = data
        self.pos = offset
        self.counter = 0

    def read(self):
        if self.counter == 4:
            self.counter = 0
            self.pos += 1
        val = (self.data[self.pos] >> _SHIFT_TABLE_I2[self.counter]) & 0x03
        self.counter += 1
        return val

    def read_backward(self):
        self.counter -= 1
        if self.counter == -1:
            self.counter = 3
            self.pos -= 1
        val = (self.data[self.pos] >> _SHIFT_TABLE_I2[self.counter]) & 0x03
        return val


class _I4Reader:
    """Read packed 4-bit values from a byte stream."""
    __slots__ = ('data', 'pos', 'counter')

    def __init__(self, data, offset):
        self.data = data
        self.pos = offset
        self.counter = 0

    def read(self):
        if self.counter == 2:
            self.counter = 0
            self.pos += 1
        val = (self.data[self.pos] >> _SHIFT_TABLE_I4[self.counter]) & 0x0F
        self.counter += 1
        return val

    def read_backward(self):
        self.counter -= 1
        if self.counter == -1:
            self.counter = 1
            self.pos -= 1
        val = (self.data[self.pos] >> _SHIFT_TABLE_I4[self.counter]) & 0x0F
        return val


class _ByteReader:
    """Read signed/unsigned 8/16/32-bit values from a byte stream."""
    __slots__ = ('data', 'pos', 'endian')

    def __init__(self, data, offset, endian='<'):
        self.data = data
        self.pos = offset
        self.endian = endian

    def read_i8(self):
        val = struct.unpack_from('b', self.data, self.pos)[0]
        self.pos += 1
        return val

    def read_u8(self):
        val = self.data[self.pos]
        self.pos += 1
        return val

    def read_i16(self):
        val = struct.unpack_from(self.endian + 'h', self.data, self.pos)[0]
        self.pos += 2
        return val

    def read_u16(self):
        val = struct.unpack_from(self.endian + 'H', self.data, self.pos)[0]
        self.pos += 2
        return val

    def read_i32(self):
        val = struct.unpack_from(self.endian + 'i', self.data, self.pos)[0]
        self.pos += 4
        return val

    def read_u32(self):
        val = struct.unpack_from(self.endian + 'I', self.data, self.pos)[0]
        self.pos += 4
        return val

    def back_i8(self):
        self.pos -= 1
        return struct.unpack_from('b', self.data, self.pos)[0]

    def back_u8(self):
        self.pos -= 1
        return self.data[self.pos]

    def back_i16(self):
        self.pos -= 2
        return struct.unpack_from(self.endian + 'h', self.data, self.pos)[0]

    def back_u16(self):
        self.pos -= 2
        return struct.unpack_from(self.endian + 'H', self.data, self.pos)[0]

    def back_i32(self):
        self.pos -= 4
        return struct.unpack_from(self.endian + 'i', self.data, self.pos)[0]

    def back_u32(self):
        self.pos -= 4
        return struct.unpack_from(self.endian + 'I', self.data, self.pos)[0]


# ---- Composite decoders ----

class _TrackInitDecoder:
    """Decode initial track values (2-bit selector → i8/i16/i32 or 0)."""
    __slots__ = ('i2', 'i8', 'i16', 'i32')

    def __init__(self, data, i2_off, i8_off, i16_off, i32_off, endian='<'):
        self.i2 = _I2Reader(data, i2_off)
        self.i8 = _ByteReader(data, i8_off, endian)
        self.i16 = _ByteReader(data, i16_off, endian)
        self.i32 = _ByteReader(data, i32_off, endian)

    def decode(self):
        sel = self.i2.read()
        if sel == 0:
            return 0
        elif sel == 1:
            return self.i8.read_i8()
        elif sel == 2:
            return self.i16.read_i16()
        else:  # sel == 3
            return self.i32.read_i32()


class _TrackDataDecoder:
    """Decode per-frame track deltas (2-bit → 4-bit → i8/i16/i32 cascade)."""
    __slots__ = ('i2', 'i4', 'i8', 'i16', 'i32')

    def __init__(self, data, i2_off, i4_off, i8_off, i16_off, i32_off, endian='<'):
        self.i2 = _I2Reader(data, i2_off)
        self.i4 = _I4Reader(data, i4_off)
        self.i8 = _ByteReader(data, i8_off, endian)
        self.i16 = _ByteReader(data, i16_off, endian)
        self.i32 = _ByteReader(data, i32_off, endian)

    def decode_forward(self):
        sel = self.i2.read()
        if sel == 2:
            # Use 4-bit sub-table
            nibble = self.i4.read()
            if nibble == 0:
                # Cascade to larger types
                val = self.i8.read_i8()
                if val == 0:
                    val = self.i16.read_i16()
                    if val == 0:
                        val = self.i32.read_i32()
                elif 0 < val < 9:
                    val += 0x7F
                elif -9 < val < 0:
                    val -= 0x80
                return val
            else:
                return _VALUE_TABLE_I4[nibble]
        else:
            return _VALUE_TABLE_I2[sel]

    def decode_backward(self):
        sel = self.i2.read_backward()
        if sel == 2:
            nibble = self.i4.read_backward()
            if nibble == 0:
                val = self.i8.back_i8()
                if val == 0:
                    val = self.i16.back_i16()
                    if val == 0:
                        val = self.i32.back_i32()
                elif 0 < val < 9:
                    val += 0x7F
                elif -9 < val < 0:
                    val -= 0x80
                return val
            else:
                return _VALUE_TABLE_I4[nibble]
        else:
            return _VALUE_TABLE_I2[sel]


class _StateDataDecoder:
    """Decode state transitions (2-bit selector → u8/u16/u32 or 0)."""
    __slots__ = ('u2', 'u8', 'u16', 'u32')

    def __init__(self, data, u2_off, u8_off, u16_off, u32_off, endian='<'):
        self.u2 = _I2Reader(data, u2_off)
        self.u8 = _ByteReader(data, u8_off, endian)
        self.u16 = _ByteReader(data, u16_off, endian)
        self.u32 = _ByteReader(data, u32_off, endian)

    def decode_forward(self):
        sel = self.u2.read()
        if sel == 0:
            return 0
        elif sel == 1:
            return self.u8.read_u8()
        elif sel == 2:
            return self.u16.read_u16()
        else:
            return self.u32.read_u32()

    def decode_backward(self):
        sel = self.u2.read_backward()
        if sel == 0:
            return 0
        elif sel == 1:
            return self.u8.back_u8()
        elif sel == 2:
            return self.u16.back_u16()
        else:
            return self.u32.back_u32()


# ---- Enbaya stream header ----

class EnbayaStream:
    """Parsed header for an Enbaya animation stream."""

    HEADER_SIZE = 0x50  # 20 uint32 fields = 80 bytes

    def __init__(self, data, offset=0, endian='<'):
        """Parse the enb_anim_stream header from binary data."""
        self.data = data
        self.offset = offset
        self.endian = endian

        fmt = endian + 'IIffI' + 'I' * 14
        fields = struct.unpack_from(fmt, data, offset)

        self.signature = fields[0]
        self.track_count = fields[1]
        self.quantization_error = fields[2]
        self.duration = fields[3]
        self.sample_rate = fields[4]

        # Sub-stream lengths
        self.track_data_init_i2_length = fields[5]
        self.track_data_init_i8_length = fields[6]
        self.track_data_init_i16_length = fields[7]
        self.track_data_init_i32_length = fields[8]
        self.track_data_i2_length = fields[9]
        self.track_data_i4_length = fields[10]
        self.track_data_i8_length = fields[11]
        self.track_data_i16_length = fields[12]
        self.track_data_i32_length = fields[13]
        self.state_data_u2_length = fields[14]
        self.state_data_u8_length = fields[15]
        self.state_data_u16_length = fields[16]
        self.state_data_u32_length = fields[17]
        self.track_flags_length = fields[18]

        # Compute sub-stream offsets (following the C implementation's layout)
        base = offset + self.HEADER_SIZE

        # The layout chains are (from the C code):
        # i32_init starts right after header
        self.off_track_data_init_i32 = base
        # state_data_u32 follows track_data_i32
        # track_data_i32 follows track_data_init_i32
        self.off_track_data_i32 = self.off_track_data_init_i32 + self.track_data_init_i32_length
        self.off_state_data_u32 = self.off_track_data_i32 + self.track_data_i32_length

        # track_data_init_i16 follows state_data_u32
        self.off_track_data_init_i16 = self.off_state_data_u32 + self.state_data_u32_length
        # track_data_i16 follows track_data_init_i16
        self.off_track_data_i16 = self.off_track_data_init_i16 + self.track_data_init_i16_length
        # state_data_u16 follows track_data_i16
        self.off_state_data_u16 = self.off_track_data_i16 + self.track_data_i16_length

        # track_data_init_i2 follows state_data_u16
        self.off_track_data_init_i2 = self.off_state_data_u16 + self.state_data_u16_length
        # track_data_init_i8 follows track_data_init_i2
        self.off_track_data_init_i8 = self.off_track_data_init_i2 + self.track_data_init_i2_length

        # track_data_i2 follows track_data_init_i8
        self.off_track_data_i2 = self.off_track_data_init_i8 + self.track_data_init_i8_length
        # track_data_i4 follows track_data_i2
        self.off_track_data_i4 = self.off_track_data_i2 + self.track_data_i2_length
        # track_data_i8 follows track_data_i4
        self.off_track_data_i8 = self.off_track_data_i4 + self.track_data_i4_length

        # state_data_u2 follows track_data_i8
        self.off_state_data_u2 = self.off_track_data_i8 + self.track_data_i8_length
        # state_data_u8 follows state_data_u2
        self.off_state_data_u8 = self.off_state_data_u2 + self.state_data_u2_length

        # track_flags follows state_data_u8
        self.off_track_flags = self.off_state_data_u8 + self.state_data_u8_length


def _normalize_quat(x, y, z, w):
    """Normalize a quaternion, returning (x, y, z, w)."""
    length_sq = x * x + y * y + z * z + w * w
    if length_sq < 1e-15:
        return (0.0, 0.0, 0.0, 1.0)
    inv_len = 1.0 / math.sqrt(length_sq)
    return (x * inv_len, y * inv_len, z * inv_len, w * inv_len)


def _lerp(a, b, t):
    """Linear interpolation."""
    return a + (b - a) * t


def _slerp_quat(q0, q1, t):
    """Spherical linear interpolation for quaternions (x,y,z,w tuples)."""
    dot = q0[0]*q1[0] + q0[1]*q1[1] + q0[2]*q1[2] + q0[3]*q1[3]

    # If dot < 0, negate one to take shortest path
    if dot < 0.0:
        q1 = (-q1[0], -q1[1], -q1[2], -q1[3])
        dot = -dot

    if dot > 0.9995:
        # Very close — use linear interpolation
        result = (
            _lerp(q0[0], q1[0], t),
            _lerp(q0[1], q1[1], t),
            _lerp(q0[2], q1[2], t),
            _lerp(q0[3], q1[3], t),
        )
        length_sq = sum(v*v for v in result)
        if length_sq > 1e-15:
            inv_len = 1.0 / math.sqrt(length_sq)
            result = tuple(v * inv_len for v in result)
        return result

    theta = math.acos(min(dot, 1.0))
    sin_theta = math.sin(theta)
    if abs(sin_theta) < 1e-10:
        return q0

    s0 = math.sin((1.0 - t) * theta) / sin_theta
    s1 = math.sin(t * theta) / sin_theta
    return (
        s0*q0[0] + s1*q1[0],
        s0*q0[1] + s1*q1[1],
        s0*q0[2] + s1*q1[2],
        s0*q0[3] + s1*q1[3],
    )


def decompress_enbaya(data, endian='<', fps=30.0):
    """Decompress an Enbaya animation stream into per-track keyframes.

    Args:
        data: Raw bytes of the igEnbayaAnimationSource._enbayaAnimationStream.
        endian: '<' for little-endian, '>' for big-endian.
        fps: Output sample rate in frames per second.

    Returns:
        Tuple of (track_count, duration, keyframes) where keyframes is a list
        of length `num_frames`, each element being a list of `track_count`
        (quat_xyzw, trans_xyz) tuples.
        Quaternion order is XYZW (Alchemy convention).
    """
    stream = EnbayaStream(data, 0, endian)

    if stream.track_count == 0 or stream.duration <= 0.0:
        return 0, 0.0, []

    # Compute number of output frames
    if fps < stream.sample_rate:
        fps = float(stream.sample_rate)
    if fps > 600.0:
        fps = 600.0

    frames_float = stream.duration * fps
    num_frames = int(frames_float) + (1 if (frames_float % 1.0) >= 0.5 else 0) + 1
    sps = 1.0 / float(stream.sample_rate)

    # Create decoders
    init_dec = _TrackInitDecoder(
        data, stream.off_track_data_init_i2, stream.off_track_data_init_i8,
        stream.off_track_data_init_i16, stream.off_track_data_init_i32, endian
    )
    track_dec = _TrackDataDecoder(
        data, stream.off_track_data_i2, stream.off_track_data_i4,
        stream.off_track_data_i8, stream.off_track_data_i16,
        stream.off_track_data_i32, endian
    )
    state_dec = _StateDataDecoder(
        data, stream.off_state_data_u2, stream.off_state_data_u8,
        stream.off_state_data_u16, stream.off_state_data_u32, endian
    )

    track_count = stream.track_count
    quant_err = stream.quantization_error

    # Per-track state: [quat_accum(4), trans_accum(3), qt_prev(7), qt_next(7), flags]
    # We use a simpler approach: keep integer accumulators and two keyframe slots

    # Integer accumulators for delta coding
    accum = [[0] * 7 for _ in range(track_count)]

    # Two keyframe slots per track (prev and next for interpolation)
    # Each slot: (qx, qy, qz, qw, tx, ty, tz, time)
    qt = [[[0.0]*8, [0.0]*8] for _ in range(track_count)]

    # Track flags (which components are active)
    flags = [0] * track_count

    # State machine
    state_next_step = 0
    state_prev_step = 0
    current_sample = 0
    track_selector = 0
    track_direction = 0  # 0=init, 1=forward, 2=backward

    # ---- Step 1: Initialize (frame 0) ----
    # Read initial values for all tracks
    for i in range(track_count):
        for j in range(7):
            accum[i][j] = init_dec.decode()

    # Apply initial values
    for i in range(track_count):
        qx = accum[i][0] * quant_err
        qy = accum[i][1] * quant_err
        qz = accum[i][2] * quant_err
        qw = accum[i][3] * quant_err
        tx = accum[i][4] * quant_err
        ty = accum[i][5] * quant_err
        tz = accum[i][6] * quant_err

        qx, qy, qz, qw = _normalize_quat(qx, qy, qz, qw)

        qt[i][0][:] = [qx, qy, qz, qw, tx, ty, tz, 0.0]
        qt[i][1][:] = [qx, qy, qz, qw, tx, ty, tz, 0.0]

        # Reset accumulators
        accum[i] = [0] * 7

    # Read initial state and track flags
    state_next_step = state_dec.decode_forward()
    state_prev_step = 0

    # Read track flags
    flag_offset = stream.off_track_flags
    for i in range(track_count):
        flags[i] = data[flag_offset + i] if flag_offset + i < len(data) else 0

    current_sample = 0
    track_selector = 0

    # ---- Step 2: Decompress all output frames ----
    keyframes = []

    def _get_frame_data():
        """Extract current interpolated data for all tracks at a given time."""
        frame = []
        for i in range(track_count):
            s_next = track_selector & 0x01
            s_prev = s_next ^ 0x01
            q_next = qt[i][s_next]
            q_prev = qt[i][s_prev]
            frame.append((
                (q_next[0], q_next[1], q_next[2], q_next[3]),  # quat xyzw
                (q_next[4], q_next[5], q_next[6]),              # trans xyz
            ))
        return frame

    def _step_state_forward():
        """Advance the state machine one step forward."""
        nonlocal state_next_step, state_prev_step
        track_comps_count = track_count * 7
        i = 0
        while i < track_comps_count:
            j = state_next_step
            if j == 0:
                ti = i // 7
                ci = i % 7
                flags[ti] ^= (1 << ci)
                state_next_step = state_dec.decode_forward()
                state_prev_step = 0
                i += 1
            else:
                temp = min(j, track_comps_count - i)
                i += temp
                state_next_step -= temp
                state_prev_step += temp

    def _step_tracks_forward():
        """Read delta values for active track components."""
        for i in range(track_count):
            if flags[i] == 0:
                continue
            for j in range(7):
                if (flags[i] & (1 << j)) == 0:
                    continue
                val = track_dec.decode_forward()
                accum[i][j] += val

    def _apply_forward(time):
        """Apply accumulated deltas to produce new keyframe."""
        nonlocal track_selector
        s0 = track_selector & 0x01
        s1 = s0 ^ 0x01
        track_selector = s1

        for i in range(track_count):
            qx = accum[i][0] * quant_err + qt[i][s0][0]
            qy = accum[i][1] * quant_err + qt[i][s0][1]
            qz = accum[i][2] * quant_err + qt[i][s0][2]
            qw = accum[i][3] * quant_err + qt[i][s0][3]
            tx = accum[i][4] * quant_err + qt[i][s0][4]
            ty = accum[i][5] * quant_err + qt[i][s0][5]
            tz = accum[i][6] * quant_err + qt[i][s0][6]

            qx, qy, qz, qw = _normalize_quat(qx, qy, qz, qw)

            qt[i][s1][:] = [qx, qy, qz, qw, tx, ty, tz, time]

    # We need to step through internal samples and interpolate to output fps.
    # The Enbaya context operates at its native sample_rate. We output at fps.
    # For simplicity, we'll sample at native rate and let the caller interpolate,
    # or we can output at the requested fps with interpolation.

    # Collect all samples at native rate first
    native_samples = []

    # Frame 0 is already initialized
    native_samples.append(_get_frame_data())

    # Step through remaining native samples
    num_native_samples = int(stream.duration * stream.sample_rate) + 2
    for sample_idx in range(1, num_native_samples):
        # State step
        if track_direction == 2:
            state_dec.decode_forward()  # skip
            track_direction = 1
        elif current_sample > 0:
            _step_state_forward()
            track_direction = 1

        # Track step
        _step_tracks_forward()
        current_sample = sample_idx

        # Compute time for this sample
        sample_time = sample_idx * sps
        if sample_time > stream.duration:
            sample_time = stream.duration

        # Apply
        _apply_forward(sample_time)
        native_samples.append(_get_frame_data())

        # NOTE: Accumulators are NOT reset between steps!
        # Enbaya deltas are second-order (delta-of-deltas / accelerations).
        # The running sum in accum acts as a first-order delta (velocity),
        # which is scaled by quantization_error and added to the previous
        # sample's float value in _apply_forward(). Resetting would lose
        # the accumulated velocity and produce under-interpolated curves.

        if sample_time >= stream.duration - 0.00001:
            break

    # Now resample to output fps with interpolation
    for fi in range(num_frames):
        time = fi / fps
        if time > stream.duration:
            time = stream.duration

        # Find bracketing native samples
        native_idx = time * stream.sample_rate
        idx0 = int(native_idx)
        idx1 = idx0 + 1

        if idx0 >= len(native_samples):
            idx0 = len(native_samples) - 1
        if idx1 >= len(native_samples):
            idx1 = len(native_samples) - 1

        blend = native_idx - int(native_idx)
        if idx0 == idx1:
            blend = 0.0

        frame = []
        for ti in range(track_count):
            q0 = native_samples[idx0][ti][0]
            t0 = native_samples[idx0][ti][1]
            q1 = native_samples[idx1][ti][0]
            t1 = native_samples[idx1][ti][1]

            if blend < 0.001:
                q_out = q0
                t_out = t0
            elif blend > 0.999:
                q_out = q1
                t_out = t1
            else:
                q_out = _slerp_quat(q0, q1, blend)
                t_out = (
                    _lerp(t0[0], t1[0], blend),
                    _lerp(t0[1], t1[1], blend),
                    _lerp(t0[2], t1[2], blend),
                )

            frame.append((q_out, t_out))

        keyframes.append(frame)

    return track_count, stream.duration, keyframes


def decompress_enbaya_to_tracks(data, endian='<', fps=30.0):
    """Decompress Enbaya data and return per-track keyframe lists.

    Args:
        data: Raw bytes of the Enbaya stream.
        endian: '<' for little-endian, '>' for big-endian.
        fps: Output sample rate.

    Returns:
        List of track_count elements, each being a list of
        (time_ms, quat_wxyz, trans_xyz) tuples.
        Quaternion order is WXYZ (Blender convention).
    """
    track_count, duration, keyframes = decompress_enbaya(data, endian, fps)

    if not keyframes or track_count == 0:
        return []

    # Reorganize: per-frame → per-track
    tracks = [[] for _ in range(track_count)]

    for fi, frame in enumerate(keyframes):
        time_ms = (fi / fps) * 1000.0

        for ti in range(track_count):
            qx, qy, qz, qw = frame[ti][0]
            tx, ty, tz = frame[ti][1]

            # Convert XYZW → WXYZ for Blender
            tracks[ti].append((
                time_ms,
                (qw, qx, qy, qz),
                (tx, ty, tz),
            ))

    return tracks
