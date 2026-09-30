"""SAT 风格的 Python 调用入口，在线 SOME/IP 通信由原生 vsomeip 进程完成。"""

from .operator import SOAOperator
from .partner import FailType, S2sBaseClass

__all__ = ["SOAOperator", "S2sBaseClass", "FailType"]
