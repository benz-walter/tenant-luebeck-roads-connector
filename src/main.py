#!/bin/env python

import json
import signal
import sys
from argparse import ArgumentParser
from datetime import datetime
from socket import gaierror
from time import sleep, time
from typing import Any, Self

import environ
import pika
from loguru import logger
from pika import BlockingConnection, ConnectionParameters
from pika.adapters.blocking_connection import BlockingChannel
from sqlalchemy import MetaData, Table, create_engine, inspect
from sqlalchemy.engine import Engine
from sqlalchemy.exc import NoSuchTableError, OperationalError

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

# Sleep at least this amount of seconds between sync intervals to minimize DDOS against the external services.
SYNC_MIN_SLEEP_TIME_SECONDS = 60

# Flag to recognize interrupts, to be able to shut down gracefully.
STOP_EXECUTION = False


class RoadsError(Exception):
    def __init__(self, message, error=None):
        super().__init__(message)
        self.error = error


def handle_signal(sig, frame):
    global STOP_EXECUTION
    logger.info(
        "Received signal {sig}. Requesting graceful shutdown...",
        sig=signal.Signals(sig).name,
    )
    STOP_EXECUTION = True


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


class DatabaseConnector:
    def __init__(self):
        self._url = env("DATABASE_URL")

        self._tables = [t.strip() for t in env("SYNC_TABLES") or [] if t.strip()]

        if not self._tables:
            logger.error(
                "No tables specified for export. Please set SYNC_TABLES environment variable."
            )
            exit(1)

    def _engine(self) -> Engine:
        return create_engine(self._url, plugins=["geoalchemy2"])

    def sync(self) -> None:
        """
        Connects to the database and fetches the content of a table, yielding it in the following
        :return:
        """

        logger.info(
            "Start to synchronize tables: {tables}", tables=", ".join(self._tables)
        )

        try:
            engine = self._engine()
            inspector = inspect(engine)
        except OperationalError as error:
            raise RoadsError(
                f"Could not connect to the database: {error}", error=error
            ) from error

        with engine.connect() as connection, BrokerConnector() as connector:
            for table_name in self._tables:
                if STOP_EXECUTION:
                    logger.info("Interrupt detected. Skipping remaining tables.")
                    break

                logger.debug("{table}: Lookup...", table=table_name)

                try:
                    columns_info = inspector.get_columns(table_name)
                except NoSuchTableError as error:
                    raise RoadsError(
                        f"Table '{table_name}' does not exist or is not readable",
                        error=error,
                    ) from error

                # Map columns to their types
                schema_columns = {
                    col["name"]: str(col["type"]).lower() for col in columns_info
                }
                logger.debug(
                    "{table}: Received schema ({columns} columns)",
                    table=table_name,
                    columns=len(schema_columns),
                )

                metadata = MetaData()
                table = Table(table_name, metadata, autoload_with=engine)
                query = table.select()
                result = connection.execute(query)

                # Fetch rows and convert them to dictionaries
                # SQLAlchemy already converts database types to Python types.
                # Since the goal is JSON, we can handle non-JSON types (like datetime)
                # using a default=str during json.dumps later on.
                rows = [dict(row) for row in result.mappings()]
                logger.debug(
                    "{table}: Received data ({rows} rows)",
                    table=table_name,
                    rows=len(rows),
                )

                connector.publish(
                    {
                        "schema": {
                            "table": table_name,
                            "columns": schema_columns,
                        },
                        "data": rows,
                    }
                )
                logger.debug("{table}: Published table", table=table_name)


class BrokerConnector:
    def __init__(self):
        self._host = env("BROKER_HOST")
        self._port = env("BROKER_PORT")
        self._queue_name = env("BROKER_QUEUE_NAME")

        self._connection: BlockingConnection | None = None
        self._channel: BlockingChannel | None = None

    def connection(self) -> BlockingConnection:
        if not self._connection:
            raise RuntimeError(
                "Need to first connect to Broker prior to accessing the connection"
            ) from None

        return self._connection

    def publish(self, data: dict[str, Any]) -> None:
        message = json.dumps(data, default=str)
        self._channel.basic_publish(
            exchange="",
            routing_key=self._queue_name,
            body=message,
            properties=pika.BasicProperties(
                delivery_mode=2,  # make message persistent
            ),
        )

    def __enter__(self) -> Self:
        try:
            self._connection = BlockingConnection(
                ConnectionParameters(host=self._host, port=self._port)
            )
        except gaierror as error:
            raise RoadsError(
                f"Could not connect to Broker {self._host}:{self._port}: {error}",
                error=error,
            ) from error

        self._channel = self._connection.channel()
        self._channel.queue_declare(queue=self._queue_name, durable=True)

        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        if self._channel and self._channel.is_open:
            self._channel.close()

        if self._connection and self._connection.is_open:
            self._connection.close()

        self._channel = None
        self._connection = None


def main(background: bool = False):
    database_connector = DatabaseConnector()

    while not STOP_EXECUTION:
        start_time = time()
        try:
            database_connector.sync()
        except RoadsError as error:
            logger.error(error)
            exit(1)
        except Exception as error:
            logger.exception("An unexpected error occurred: {error}", error=error)
            exit(1)

        if STOP_EXECUTION:
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
        while remaining_sleep > 0 and not STOP_EXECUTION:
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
