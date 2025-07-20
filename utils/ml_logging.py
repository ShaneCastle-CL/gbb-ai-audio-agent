import functools
import logging
import json
import os
import time
from typing import Callable, Optional

from opentelemetry import trace
from opentelemetry.sdk._logs import LoggingHandler
from utils.telemetry_config import setup_azure_monitor
from colorama import Fore, Style, init as colorama_init

colorama_init(autoreset=True)

# Define a new logging level named "KEYINFO" with a level of 25
KEYINFO_LEVEL_NUM = 25
logging.addLevelName(KEYINFO_LEVEL_NUM, "KEYINFO")


def keyinfo(self: logging.Logger, message, *args, **kws):
    if self.isEnabledFor(KEYINFO_LEVEL_NUM):
        self._log(KEYINFO_LEVEL_NUM, message, args, **kws)


logging.Logger.keyinfo = keyinfo


class JsonFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        record.funcName = getattr(record, "func_name_override", record.funcName)
        record.filename = getattr(record, "file_name_override", record.filename)
        record.trace_id = getattr(record, "trace_id", "-")
        record.span_id = getattr(record, "span_id", "-")
        record.session_id = getattr(record, "session_id", "-")
        record.call_connection_id = getattr(record, "call_connection_id", "-")

        log_record = {
            "timestamp": self.formatTime(record, self.datefmt),
            "name": record.name,
            "process": record.processName,
            "level": record.levelname,
            "trace_id": record.trace_id,
            "span_id": record.span_id,
            "session_id": record.session_id,
            "call_connection_id": record.call_connection_id,
            "message": record.getMessage(),
            "file": record.filename,
            "function": record.funcName,
            "line": record.lineno,
        }
        return json.dumps(log_record)


class PrettyFormatter(logging.Formatter):
    LEVEL_COLORS = {
        "DEBUG": Fore.CYAN,
        "INFO": Fore.GREEN,
        "WARNING": Fore.YELLOW,
        "ERROR": Fore.RED,
        "CRITICAL": Fore.MAGENTA,
        "KEYINFO": Fore.BLUE,
    }

    def format(self, record: logging.LogRecord) -> str:
        timestamp = self.formatTime(record, self.datefmt)
        level = record.levelname
        name = record.name
        msg = record.getMessage()

        color = self.LEVEL_COLORS.get(level, "")
        return f"{Fore.WHITE}[{timestamp}]{Style.RESET_ALL} {color}{level}{Style.RESET_ALL} - {Fore.BLUE}{name}{Style.RESET_ALL}: {msg}"


class TraceLogFilter(logging.Filter):
    def filter(self, record):
        span = trace.get_current_span()
        context = span.get_span_context() if span else None
        record.trace_id = f"{context.trace_id:016x}" if context and context.trace_id else "-"
        record.span_id = f"{context.span_id:016x}" if context and context.span_id else "-"

        # Capture additional context attributes if available (for real-time correlation)
        record.session_id = getattr(span, "session_id", "-") if span else "-"
        record.call_connection_id = getattr(span, "call_connection_id", "-") if span else "-"
        return True

def configure_azure_monitor(logger_name: Optional[str] = None):
    setup_azure_monitor(logger_name)
    handler = LoggingHandler(level=logging.INFO)
    target_logger = logging.getLogger(logger_name) if logger_name else logging.getLogger()
    target_logger.addHandler(handler)
    target_logger.addFilter(TraceLogFilter())

def get_logger(
    name: str = "micro",
    level: Optional[int] = None,
    include_stream_handler: bool = True,
) -> logging.Logger:
    logger = logging.getLogger(name)

    if level is not None or logger.level == 0:
        logger.setLevel(level or logging.INFO)

    is_production = os.environ.get("ENV", "dev").lower() == "prod"

    if include_stream_handler and not any(isinstance(h, logging.StreamHandler) for h in logger.handlers):
        sh = logging.StreamHandler()
        sh.setFormatter(JsonFormatter() if is_production else PrettyFormatter())
        sh.addFilter(TraceLogFilter())
        logger.addHandler(sh)

    return logger


def log_function_call(
    logger_name: str, log_inputs: bool = False, log_output: bool = False
) -> Callable:
    def decorator_log_function_call(func):
        @functools.wraps(func)
        def wrapper_log_function_call(*args, **kwargs):

            from opentelemetry.trace import get_current_span

            span = get_current_span()
            if span and span.is_recording():
                # These values must be passed via kwargs or resolved from context/session manager
                session_id = kwargs.get("session_id", "-")
                call_connection_id = kwargs.get("call_connection_id", "-")

                span.set_attribute("ai.session.id", call_connection_id)
                span.set_attribute("ai.user.id", session_id)
                
            logger = get_logger(logger_name)
            func_name = func.__name__

            if log_inputs:
                args_str = ", ".join(map(str, args))
                kwargs_str = ", ".join(f"{k}={v}" for k, v in kwargs.items())
                logger.info(
                    f"Function {func_name} called with arguments: {args_str} and keyword arguments: {kwargs_str}"
                )
            else:
                logger.info(f"Function {func_name} called")

            start_time = time.time()
            result = func(*args, **kwargs)
            duration = time.time() - start_time

            if log_output:
                logger.info(f"Function {func_name} output: {result}")

            logger.info(json.dumps({
                "event": "execution_duration",
                "function": func_name,
                "duration_seconds": round(duration, 2)
            }))
            logger.info(f"Function {func_name} completed")

            return result

        return wrapper_log_function_call

    return decorator_log_function_call
