"""流式 MPEG-TS/H.265 PES 载荷统计。

统计口径是 PMT 标记的视频 PID 对应 PES 载荷字节，不包含 TS 包头、适配字段
或 PES 头。解析器允许输入按任意边界分片，并在停止时保留已经识别出的尾部
PES 载荷。
"""

from __future__ import annotations

from typing import Dict, Optional


TS_PACKET_SIZE = 188
TS_SYNC_BYTE = 0x47
PAT_PID = 0
PAT_TABLE_ID = 0x00
PMT_TABLE_ID = 0x02
HEVC_STREAM_TYPE = 0x24


class MpegTsHevcByteCounter:
    """从 MPEG-TS 字节流中增量统计 H.265 PES 有效载荷。"""

    def __init__(self) -> None:
        self._transport_buffer = bytearray()
        self._psi_buffers: Dict[int, bytearray] = {}
        self._pmt_pid: Optional[int] = None
        self._video_pid: Optional[int] = None
        self._pes_buffer = bytearray()
        self._pes_counted_payload = 0
        self._pes_payload_offset: Optional[int] = None
        self._pes_payload_length: Optional[int] = None
        self._pes_unbounded = False
        self._total_bytes = 0
        self._malformed_packets = 0
        self._finished = False

    @property
    def total_bytes(self) -> int:
        return self._total_bytes

    @property
    def pmt_pid(self) -> Optional[int]:
        return self._pmt_pid

    @property
    def video_pid(self) -> Optional[int]:
        return self._video_pid

    @property
    def malformed_packets(self) -> int:
        return self._malformed_packets

    def feed(self, data: bytes) -> int:
        """加入一段 TS 字节并返回本次新增的 H.265 载荷字节数。"""
        if not data:
            return 0
        if self._finished:
            raise RuntimeError("MPEG-TS 统计器已经结束")
        before = self._total_bytes
        self._transport_buffer.extend(data)
        self._consume_transport_packets()
        return self._total_bytes - before

    def finish(self) -> int:
        """结束输入，处理已识别的 PES 尾部并返回新增字节数。"""
        if self._finished:
            return 0
        before = self._total_bytes
        self._consume_pes_buffer(final=True)
        self._transport_buffer.clear()
        self._finished = True
        return self._total_bytes - before

    def _consume_transport_packets(self) -> None:
        while len(self._transport_buffer) >= TS_PACKET_SIZE:
            if self._transport_buffer[0] != TS_SYNC_BYTE:
                sync_index = self._transport_buffer.find(bytes([TS_SYNC_BYTE]))
                if sync_index < 0:
                    del self._transport_buffer[:-TS_PACKET_SIZE + 1]
                    self._malformed_packets += 1
                    return
                del self._transport_buffer[:sync_index]
                self._malformed_packets += 1
                if len(self._transport_buffer) < TS_PACKET_SIZE:
                    return

            packet = bytes(self._transport_buffer[:TS_PACKET_SIZE])
            del self._transport_buffer[:TS_PACKET_SIZE]
            self._consume_transport_packet(packet)

    def _consume_transport_packet(self, packet: bytes) -> None:
        if len(packet) != TS_PACKET_SIZE or packet[0] != TS_SYNC_BYTE:
            self._malformed_packets += 1
            return

        transport_error = bool(packet[1] & 0x80)
        payload_unit_start = bool(packet[1] & 0x40)
        pid = ((packet[1] & 0x1F) << 8) | packet[2]
        scrambling = (packet[3] >> 6) & 0x03
        adaptation_control = (packet[3] >> 4) & 0x03
        if transport_error or scrambling or adaptation_control == 0:
            self._malformed_packets += 1
            return

        offset = 4
        if adaptation_control & 0x02:
            adaptation_length = packet[offset]
            offset += 1
            if offset + adaptation_length > TS_PACKET_SIZE:
                self._malformed_packets += 1
                return
            offset += adaptation_length
        if not adaptation_control & 0x01 or offset >= TS_PACKET_SIZE:
            return

        payload = packet[offset:]
        if pid == PAT_PID:
            self._consume_psi(pid, payload, payload_unit_start, PAT_TABLE_ID)
        elif self._pmt_pid is not None and pid == self._pmt_pid:
            self._consume_psi(pid, payload, payload_unit_start, PMT_TABLE_ID)
        elif self._video_pid is not None and pid == self._video_pid:
            self._consume_pes(payload, payload_unit_start)

    def _consume_psi(
        self,
        pid: int,
        payload: bytes,
        payload_unit_start: bool,
        expected_table_id: int,
    ) -> None:
        buffer = self._psi_buffers.setdefault(pid, bytearray())
        if payload_unit_start:
            if not payload:
                self._malformed_packets += 1
                return
            pointer = payload[0]
            if pointer > len(payload) - 1:
                self._malformed_packets += 1
                buffer.clear()
                return
            if pointer:
                buffer.extend(payload[1:1 + pointer])
                self._consume_complete_psi_sections(
                    buffer,
                    expected_table_id,
                )
            else:
                buffer.clear()
            payload = payload[1 + pointer:]
        buffer.extend(payload)
        self._consume_complete_psi_sections(buffer, expected_table_id)

    def _consume_complete_psi_sections(
        self,
        buffer: bytearray,
        expected_table_id: int,
    ) -> None:
        while len(buffer) >= 3:
            if buffer[0] == 0xFF:
                buffer.clear()
                return
            section_length = ((buffer[1] & 0x0F) << 8) | buffer[2]
            if section_length > 1021:
                buffer.clear()
                self._malformed_packets += 1
                return
            total_length = 3 + section_length
            if len(buffer) < total_length:
                return
            section = bytes(buffer[:total_length])
            del buffer[:total_length]
            if section[0] != expected_table_id:
                continue
            if expected_table_id == PAT_TABLE_ID:
                self._parse_pat(section)
            else:
                self._parse_pmt(section)

    def _parse_pat(self, section: bytes) -> None:
        if len(section) < 12:
            return
        end = len(section) - 4
        for offset in range(8, end - 3, 4):
            program_number = (section[offset] << 8) | section[offset + 1]
            if program_number == 0:
                continue
            candidate = ((section[offset + 2] & 0x1F) << 8) | section[offset + 3]
            if self._pmt_pid != candidate:
                self._pmt_pid = candidate
                self._video_pid = None
                self._pes_reset()
            return

    def _parse_pmt(self, section: bytes) -> None:
        if len(section) < 16:
            return
        program_info_length = ((section[10] & 0x0F) << 8) | section[11]
        offset = 12 + program_info_length
        end = len(section) - 4
        if offset > end:
            self._malformed_packets += 1
            return
        candidate: Optional[int] = None
        while offset + 5 <= end:
            stream_type = section[offset]
            pid = ((section[offset + 1] & 0x1F) << 8) | section[offset + 2]
            es_info_length = ((section[offset + 3] & 0x0F) << 8) | section[offset + 4]
            offset += 5
            if offset + es_info_length > end:
                self._malformed_packets += 1
                return
            if stream_type == HEVC_STREAM_TYPE and candidate is None:
                candidate = pid
            offset += es_info_length
        if offset != end:
            self._malformed_packets += 1
            return
        if candidate != self._video_pid:
            self._video_pid = candidate
            self._pes_reset()

    def _consume_pes(self, payload: bytes, payload_unit_start: bool) -> None:
        if payload_unit_start and self._pes_buffer:
            self._consume_pes_buffer(final=True)
            self._pes_reset()
        self._pes_buffer.extend(payload)
        self._consume_pes_buffer(final=False)

    def _consume_pes_buffer(self, final: bool) -> None:
        while True:
            if self._pes_payload_offset is None:
                if len(self._pes_buffer) < 6:
                    return
                if self._pes_buffer[:3] != b"\x00\x00\x01":
                    marker = self._pes_buffer.find(b"\x00\x00\x01", 1)
                    if marker < 0:
                        if final:
                            self._pes_buffer.clear()
                        else:
                            del self._pes_buffer[:-2]
                        self._malformed_packets += 1
                        return
                    del self._pes_buffer[:marker]
                    self._malformed_packets += 1
                    if len(self._pes_buffer) < 6:
                        return
                stream_id = self._pes_buffer[3]
                if not 0xE0 <= stream_id <= 0xEF:
                    self._malformed_packets += 1
                    self._pes_reset()
                    return
                packet_length = (self._pes_buffer[4] << 8) | self._pes_buffer[5]
                if len(self._pes_buffer) < 9:
                    return
                header_data_length = self._pes_buffer[8]
                payload_offset = 9 + header_data_length
                if payload_offset < 9:
                    self._malformed_packets += 1
                    self._pes_reset()
                    return
                self._pes_payload_offset = payload_offset
                self._pes_unbounded = packet_length == 0
                if self._pes_unbounded:
                    self._pes_payload_length = None
                else:
                    payload_length = 6 + packet_length - payload_offset
                    if payload_length < 0:
                        self._malformed_packets += 1
                        self._pes_reset()
                        return
                    self._pes_payload_length = payload_length

            assert self._pes_payload_offset is not None
            available = max(0, len(self._pes_buffer) - self._pes_payload_offset)
            if self._pes_payload_length is not None:
                available = min(available, self._pes_payload_length)
            if available > self._pes_counted_payload:
                delta = available - self._pes_counted_payload
                self._total_bytes += delta
                self._pes_counted_payload = available

            if self._pes_payload_length is None:
                return
            total_length = self._pes_payload_offset + self._pes_payload_length
            if len(self._pes_buffer) < total_length:
                return
            del self._pes_buffer[:total_length]
            self._pes_reset(keep_buffer=True)
            if not self._pes_buffer:
                return
            if not final and len(self._pes_buffer) < 6:
                return

    def _pes_reset(self, keep_buffer: bool = False) -> None:
        if not keep_buffer:
            self._pes_buffer.clear()
        self._pes_counted_payload = 0
        self._pes_payload_offset = None
        self._pes_payload_length = None
        self._pes_unbounded = False
