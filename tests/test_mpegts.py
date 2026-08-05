import unittest

from hevc_lab.web.mpegts import MpegTsHevcByteCounter


PAT_PID = 0
PMT_PID = 100
VIDEO_PID = 256


def _make_ts_packet(pid, payload, payload_unit_start, continuity_counter):
    if len(payload) > 184:
        raise ValueError("payload too large")
    header = bytes(
        [
            0x47,
            (0x40 if payload_unit_start else 0) | ((pid >> 8) & 0x1F),
            pid & 0xFF,
            0x10 | (continuity_counter & 0x0F),
        ]
    )
    if len(payload) == 184:
        return header + payload
    adaptation_length = 183 - len(payload)
    adaptation = bytes([adaptation_length, 0]) + bytes(
        [0xFF] * max(0, adaptation_length - 1)
    )
    packet = header[:3] + bytes([0x30 | (continuity_counter & 0x0F)])
    packet += adaptation + payload
    assert len(packet) == 188
    return packet


def _packetize(pid, payload, max_payload=184):
    packets = []
    continuity_counter = 0
    for offset in range(0, len(payload), max_payload):
        packets.append(
            _make_ts_packet(
                pid,
                payload[offset : offset + max_payload],
                offset == 0,
                continuity_counter,
            )
        )
        continuity_counter = (continuity_counter + 1) & 0x0F
    return b"".join(packets)


def _pat_section():
    return bytes(
        [
            0x00,
            0xB0,
            0x0D,
            0x00,
            0x01,
            0xC1,
            0x00,
            0x00,
            0x00,
            0x01,
            0xE0 | (PMT_PID >> 8),
            PMT_PID & 0xFF,
            0x00,
            0x00,
            0x00,
            0x00,
        ]
    )


def _pmt_section():
    return bytes(
        [
            0x02,
            0xB0,
            0x12,
            0x00,
            0x01,
            0xC1,
            0x00,
            0x00,
            0xE0 | (VIDEO_PID >> 8),
            VIDEO_PID & 0xFF,
            0xF0,
            0x00,
            0x24,
            0xE0 | (VIDEO_PID >> 8),
            VIDEO_PID & 0xFF,
            0xF0,
            0x00,
            0x00,
            0x00,
            0x00,
            0x00,
        ]
    )


def _pes(payload, unbounded=False, stream_id=0xE0):
    packet_length = 0 if unbounded else 3 + len(payload)
    return bytes(
        [
            0x00,
            0x00,
            0x01,
            stream_id,
            (packet_length >> 8) & 0xFF,
            packet_length & 0xFF,
            0x80,
            0x00,
            0x00,
        ]
    ) + payload


def _stream(video_payload, video_max_payload=23, unbounded=False):
    data = _packetize(PAT_PID, bytes([0]) + _pat_section(), max_payload=5)
    data += _packetize(PMT_PID, bytes([0]) + _pmt_section(), max_payload=7)
    data += _packetize(0x155, b"unrelated-pid", max_payload=11)
    data += _packetize(
        VIDEO_PID,
        _pes(video_payload, unbounded=unbounded),
        max_payload=video_max_payload,
    )
    return data


class MpegTsHevcByteCounterTests(unittest.TestCase):
    def test_split_reads_pat_pmt_and_pes_across_packets(self):
        payload = bytes(range(251)) * 3
        counter = MpegTsHevcByteCounter()
        data = _stream(payload)
        for offset in range(0, len(data), 13):
            counter.feed(data[offset : offset + 13])

        counter.finish()

        self.assertEqual(counter.pmt_pid, PMT_PID)
        self.assertEqual(counter.video_pid, VIDEO_PID)
        self.assertEqual(counter.total_bytes, len(payload))
        self.assertEqual(counter.malformed_packets, 0)

    def test_ignores_unrelated_pid_and_pes_container_bytes(self):
        payload = b"h265 payload"
        counter = MpegTsHevcByteCounter()
        counter.feed(_stream(payload))

        self.assertEqual(counter.total_bytes, len(payload))

    def test_recovers_after_malformed_transport_packet(self):
        bad = bytearray(188)
        bad[0] = 0x47
        bad[1] = 0x00
        bad[2] = 0x01
        bad[3] = 0x30
        bad[4] = 200
        payload = b"valid after bad packet"
        counter = MpegTsHevcByteCounter()
        data = _stream(payload)
        prefix = _packetize(PAT_PID, bytes([0]) + _pat_section(), max_payload=5)
        prefix += _packetize(PMT_PID, bytes([0]) + _pmt_section(), max_payload=7)
        suffix = data[len(prefix) :]
        counter.feed(prefix + bytes(bad) + suffix)

        self.assertEqual(counter.total_bytes, len(payload))
        self.assertGreaterEqual(counter.malformed_packets, 1)

    def test_stop_flushes_unbounded_pes_tail(self):
        payload = bytes(range(200))
        counter = MpegTsHevcByteCounter()
        data = _stream(payload, unbounded=True)
        counter.feed(data[:-37])
        counter.feed(data[-37:])

        self.assertEqual(counter.finish(), 0)
        self.assertEqual(counter.total_bytes, len(payload))

    def test_invalid_pes_stream_id_is_not_counted(self):
        payload = b"must not count"
        counter = MpegTsHevcByteCounter()
        data = _packetize(PAT_PID, bytes([0]) + _pat_section())
        data += _packetize(PMT_PID, bytes([0]) + _pmt_section())
        data += _packetize(VIDEO_PID, _pes(payload, stream_id=0xBD))
        counter.feed(data)

        self.assertEqual(counter.total_bytes, 0)
        self.assertGreaterEqual(counter.malformed_packets, 1)


if __name__ == "__main__":
    unittest.main()
