from typing import Optional


REALTIME_SPEED_THRESHOLD_X = 1.0
ENGINEERING_HEADROOM_THRESHOLD_X = 1.1


def validate_speed_gate(min_speed_x: Optional[float]) -> None:
    if min_speed_x is not None and min_speed_x <= 0:
        raise ValueError("连续处理速度门槛必须大于0或设为无门槛")


def speed_gate_passes(
    encode_speed_x: float,
    min_speed_x: Optional[float],
) -> bool:
    validate_speed_gate(min_speed_x)
    return min_speed_x is None or encode_speed_x >= min_speed_x


def classify_speed(
    encode_speed_x: float,
    min_speed_x: Optional[float],
) -> str:
    """返回面向报告的速度等级，不参与激进模式候选淘汰。"""
    validate_speed_gate(min_speed_x)
    if encode_speed_x >= ENGINEERING_HEADROOM_THRESHOLD_X:
        return "realtime_headroom"
    if encode_speed_x >= REALTIME_SPEED_THRESHOLD_X:
        return "realtime"
    if min_speed_x is not None and encode_speed_x >= min_speed_x:
        return "near_realtime"
    return "offline"
