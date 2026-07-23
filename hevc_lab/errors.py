class LabError(RuntimeError):
    """用户可理解的实验错误。"""


class ToolError(LabError):
    """外部工具缺失或执行失败。"""


class VideoError(LabError):
    """输入视频无效。"""

