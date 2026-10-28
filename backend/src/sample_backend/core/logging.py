"""使用 Loguru 输出终端与轮转文件日志，避免将敏感请求内容写入日志。"""

import sys
from loguru import logger

from .config import data_directory


def configure_logging() -> None:
    log_dir = data_directory() / "logs"
    log_dir.mkdir(parents=True, exist_ok=True)

    # 移除默认 sink，避免多次创建 App / 测试时重复输出。
    logger.remove()
    if sys.stderr is not None and not sys.stderr.closed:
        logger.add(sys.stderr, level="DEBUG" if __debug__ else "INFO", colorize=False)
    # 入队写文件并轮转；diagnose=False 防止把异常局部变量写入日志。
    logger.add(
        log_dir / "backend.log",
        level="INFO",
        rotation="10 MB",
        retention="7 days",
        encoding="utf-8",
        enqueue=True,
        backtrace=True,
        diagnose=False,
    )
    logger.info("Backend logging ready; log directory: {}", log_dir)
