import io
import os
import atexit
import logging
from datetime import datetime


log_capture_string = io.StringIO()
log_memory_handler = None

TRANSLATE_LEVEL = 25
logging.addLevelName(TRANSLATE_LEVEL, 'TRANSLATE')


def translate(self, message, *args, **kwargs):
    if self.isEnabledFor(TRANSLATE_LEVEL):
        self._log(TRANSLATE_LEVEL, message, args, **kwargs)


logging.Logger.translate = translate


class AlignedFormatter(logging.Formatter):
    """
    Custom logging formatter that mimics FastAPI's log format with colored output.
    """
    COLORS = {
        'DEBUG': '\033[36m',
        'INFO': '\033[32m',
        'WARNING': '\033[33m',
        'ERROR': '\033[31m',
        'CRITICAL': '\033[35m',
    }
    RESET = '\033[0m'

    def __init__(self, colored: bool = False):
        super().__init__()
        self.colored = colored

    def format(self, record):
        level_text = f'{record.levelname}:'
        padded_level = level_text.ljust(9)
        
        if self.colored:
            color = self.COLORS.get(record.levelname, '')
            level_name = padded_level.replace(
                level_text, f'{color}{record.levelname}{self.RESET}:'
            )
        else:
            level_name = padded_level
            
        timestamp = self.formatTime(record, "%Y-%m-%d %H:%M:%S")
        return f"{level_name} {timestamp} - {record.getMessage()}"


def create_styled_header():
    """Create a beautifully styled header for log files."""
    timestamp = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
    
    top_left = "╭"
    top_right = "╮"
    bottom_left = "╰"
    bottom_right = "╯"
    horizontal = "─"
    vertical = "│"
    
    title = "TRANSCRIPTION LOGS"
    session_line = f"Session terminated on: {timestamp}"
    gladia_line = "Script made by Gladia"
    author_line = "Max Deckmyn"
    
    box_width = max(len(title), len(session_line), 
                   len(gladia_line) + len(author_line) + 2) + 4
    
    header_lines = [
        f"{top_left}{horizontal * (box_width - 2)}{top_right}",
        f"{vertical} {title:^{box_width - 4}} {vertical}",
        f"{vertical} {session_line:^{box_width - 4}} {vertical}",
        f"{vertical} {gladia_line:<{box_width - 4 - len(author_line)}}{author_line:>{len(author_line)}} {vertical}",
        f"{bottom_left}{horizontal * (box_width - 2)}{bottom_right}",
        ""
    ]
    
    return "\n".join(header_lines)


def setup_logging_with_capture():
    """Configure logging system with both console output and in-memory capture."""
    global log_memory_handler, log_capture_string
    
    console_handler = logging.StreamHandler()
    console_formatter = AlignedFormatter(colored=False)
    console_handler.setFormatter(console_formatter)
    
    log_capture_string = io.StringIO()
    memory_handler = logging.StreamHandler(log_capture_string)
    memory_formatter = AlignedFormatter(colored=False)
    memory_handler.setFormatter(memory_formatter)
    
    logger = logging.getLogger()
    
    logging.getLogger('websockets').setLevel(logging.WARNING)
    logging.getLogger('websockets.client').setLevel(logging.WARNING)
    logging.getLogger('websockets.asyncio.client').setLevel(logging.WARNING)
    logging.getLogger('pydub.converter').setLevel(logging.WARNING)
    logging.getLogger('pydub.utils').setLevel(logging.WARNING)
    logging.getLogger('subprocess').setLevel(logging.ERROR)
    
    logger.addHandler(console_handler)
    logger.addHandler(memory_handler)
    
    return logger


def save_logs_to_file():
    """Save captured logs to a timestamped file in the logs directory."""
    global log_capture_string
    
    if log_capture_string is None:
        return
        
    try:
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        filename = f"gladia_logs_{timestamp}.txt"
        log_content = log_capture_string.getvalue()
        
        if log_content.strip():
            logs_dir = "logs"
            if not os.path.exists(logs_dir):
                os.makedirs(logs_dir)
                
            filepath = os.path.join(logs_dir, filename)
            with open(filepath, 'w', encoding='utf-8') as f:
                f.write(create_styled_header())
                f.write("\n")
                f.write(log_content)
            print(f"\nLogs saved to: {filepath}")
        else:
            print("\nNo logs to save.")
    except Exception as e:
        print(f"\nError saving logs: {e}")


atexit.register(save_logs_to_file)
