"""
日志模块 - 使用 Python 标准 logging 模块，输出到文件和终端。
替代原有的 MemoryLogBuffer（内存缓冲日志）。

日志文件按大小自动轮转（RotatingFileHandler），避免无限制增长撑爆磁盘。
默认：单文件上限 3MB，最多保留 3 个备份（LOG_FILE.1 ~ LOG_FILE.3），
即日志目录最多占用约 12MB。
"""
import logging
import os
import sys
from logging.handlers import RotatingFileHandler

LOG_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "..", "data", "iptv_toolkit.log")

# 日志自动清理参数
MAX_LOG_BYTES = 3 * 1024 * 1024  # 单文件上限 3MB
BACKUP_COUNT = 3                 # 保留 3 个历史备份


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

    # 文件 handler（按大小自动轮转）
    file_handler = RotatingFileHandler(
        LOG_FILE, maxBytes=MAX_LOG_BYTES, backupCount=BACKUP_COUNT,
        encoding="utf-8"
    )
    file_handler.setLevel(level)
    file_handler.setFormatter(formatter)
    logger.addHandler(file_handler)

    # 启动时若已有日志超过上限，立即轮转一次，让臃肿的旧日志归档，
    # 当前日志文件回到接近空的状态，避免一开机就停在 5MB+。
    if os.path.exists(LOG_FILE) and os.path.getsize(LOG_FILE) > MAX_LOG_BYTES:
        try:
            file_handler.doRollover()
        except Exception:
            pass  # 轮转失败不影响正常启动

    # 控制台 handler
    console_handler = logging.StreamHandler(sys.stdout)
    console_handler.setLevel(level)
    console_handler.setFormatter(formatter)
    logger.addHandler(console_handler)

    return logger


# 模块级默认 logger 实例
logger = setup_logger()
