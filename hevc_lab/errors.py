class LabError(RuntimeError):
    """用户可理解的运行错误。"""


class ToolError(LabError):
    """外部工具缺失或执行失败。"""
