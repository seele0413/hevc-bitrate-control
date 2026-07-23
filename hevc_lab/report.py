"""旧版顶层导入的兼容层；新代码应从 :mod:`hevc_lab.reports` 导入。"""

from .reports import write_reports

__all__ = ["write_reports"]
