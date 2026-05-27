import signal

from loguru import logger

STOP_EXECUTION = False


def handle_signal(sig, frame):
    global STOP_EXECUTION
    logger.info(
        "Received signal {sig}. Requesting graceful shutdown...",
        sig=signal.Signals(sig).name,
    )
    STOP_EXECUTION = True

def is_interrupted():
    return STOP_EXECUTION
