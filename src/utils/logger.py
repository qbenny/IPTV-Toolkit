"""
日志模块 - 使用 Python 标准 logging 模块，输出到文件和终端。
替代原有的 MemoryLogBuffer（内存缓冲日志）。
"""
import logging
import os
import sys

LOG_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "..", "data", "iptv_toolkit.log")


def setup_logger(name: str = "IPTV-Toolkit", level: int = logging.INFO) -> logging.Logger:
    """初始化日志系统。

    Args:
        name: 日志记录器名称
        level: 日志级别，默认 INFO

    Returns:
        配置好的 Logger 实例
    """
    os.makedirs(os.path.dirname(LOG_FILE), exist_ok=True)

    logger = logging.getLogger(name)
    logger.setLevel(level)

    # 避免重复添加 handler（多次调用 setup_logger 时）
    if logger.handlers:
        return logger

    formatter = logging.Formatter(
        "%(asctime)s [%(levelname)s] %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S"
    )

    # 文件 handler
    file_handler = logging.FileHandler(LOG_FILE, encoding="utf-8")
    file_handler.setLevel(level)
    file_handler.setFormatter(formatter)
    logger.addHandler(file_handler)

    # 控制台 handler
    console_handler = logging.StreamHandler(sys.stdout)
    console_handler.setLevel(level)
    console_handler.setFormatter(formatter)
    logger.addHandler(console_handler)

    return logger


# 模块级默认 logger 实例
logger = setup_logger()
