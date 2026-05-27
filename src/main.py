#!/bin/env python

import signal
import sys
from argparse import ArgumentParser
from datetime import datetime
from time import sleep, time

import environ
from loguru import logger

from roads_connector.broker_connector import BrokerConnector
from roads_connector.database_connector import DatabaseConnector
from roads_connector.exceptions import RoadsError
from roads_connector.interrupt import handle_signal, is_interrupted

env = environ.Env(
    DEBUG=(bool, False),
    LOG_LEVEL=(str, None),
    BROKER_HOST=(str, "localhost"),
    BROKER_PORT=(int, 5672),
    BROKER_QUEUE_NAME=(str, ""),
    DATABASE_URL=(str, ""),
    SYNC_TABLES=(list, []),
    SYNC_INTERVAL_MINUTES=(int, 30),
)

if not env("SYNC_TABLES"):
    raise RuntimeError(
        "SYNC_TABLES was not defined. You need to at least specify one table to synchronize."
    )

if not env("BROKER_QUEUE_NAME"):
    raise RuntimeError(
        "BROKER_QUEUE_NAME was not defined. You need to specify a queue to push the data into."
    )

if not env("DATABASE_URL"):
    raise RuntimeError(
        "DATABASE_URL was not defined. I won't be able to connect to the database."
    )

DEBUG = env("DEBUG")

# Sleep at least this number of seconds between sync intervals to minimize DDOS against the external services.
SYNC_MIN_SLEEP_TIME_SECONDS = 60

# Flag to recognize interrupts, to be able to shut down gracefully.
signal.signal(signal.SIGINT, handle_signal)
signal.signal(signal.SIGTERM, handle_signal)

logger.remove()  # remove all previously added handles in case some exist
logger.add(
    sys.stderr,
    format="{time:YYYY-MM-DD HH:mm:ss.SSS!UTC}Z | <level>{level: <7}</> | <level>{message}</>",
    level=env("LOG_LEVEL") or ("DEBUG" if DEBUG else "INFO"),
    colorize=True,
    diagnose=False,
    backtrace=False,
)


def main(background: bool = False):
    broker_connector = BrokerConnector(host=env("BROKER_HOST"), port=env("BROKER_PORT"), queue_name=env("BROKER_QUEUE_NAME"))
    database_connector = DatabaseConnector(url=env("DATABASE_URL"), tables=env("SYNC_TABLES"), broker=broker_connector)

    while not is_interrupted():
        start_time = time()
        try:
            database_connector.sync()
        except RoadsError as error:
            logger.error(error)
            exit(1)
        except Exception as error:
            logger.exception("An unexpected error occurred: {error}", error=error)
            exit(1)

        if is_interrupted():
            break

        time_taken = time() - start_time

        if not background:
            logger.info(
                "Synced in {time}s.",
                time=round(time_taken, 4),
            )
            logger.info("Only run once, exiting.")
            break

        # Reduce the sleeping time by the time it took to sync the tables
        # to reduce drift.
        sleeping = max(
            (env("SYNC_INTERVAL_MINUTES") * 60) - time_taken,
            SYNC_MIN_SLEEP_TIME_SECONDS,
        )
        sleeping = round(sleeping, 4)

        wakeup_time = time() + sleeping
        wakeup_time_dt = datetime.fromtimestamp(wakeup_time)
        wakeup_time_human = wakeup_time_dt.strftime("%Y-%m-%d %H:%M:%S")

        logger.info(
            "Synced in {time}s. Next wakeup will be around {wakeup}",
            time=round(time_taken, 4),
            wakeup=wakeup_time_human,
        )

        # Sleep in small increments to allow for faster response to termination signals
        # if the sleep time is long.
        remaining_sleep = sleeping
        while remaining_sleep > 0 and not is_interrupted():
            chunk = min(remaining_sleep, 1.0)
            sleep(chunk)
            remaining_sleep -= chunk

    logger.info("Graceful shutdown complete.")


if __name__ == "__main__":
    parser = ArgumentParser(
        prog="RoadsConnector",
        description="Pushes tables' data and schema of a database to a Broker message broker",
    )
    parser.add_argument(
        "--background",
        help="Run continuously as a background process",
        action="store_true",
    )

    args = parser.parse_args()

    main(background=args.background)
