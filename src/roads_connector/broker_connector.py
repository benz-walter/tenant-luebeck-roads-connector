import json
from socket import gaierror
from typing import Any, Self

from loguru import logger
from pika import (
    BasicProperties,
    BlockingConnection,
    ConnectionParameters,
    PlainCredentials,
)
from pika.adapters.blocking_connection import BlockingChannel

from .exceptions import RoadsError


class BrokerConnector:
    def __init__(
        self,
        host: str,
        port: int,
        queue_name: str,
        username: str | None = None,
        password: str | None = None,
    ):
        self._host = host
        self._port = port
        self._queue_name = queue_name

        self._username = username
        self._password = password

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
            properties=BasicProperties(
                delivery_mode=2,  # make message persistent
            ),
        )

    def __enter__(self) -> Self:
        connection_params = {
            "host": self._host,
            "port": self._port,
        }

        if all([self._username, self._password]):
            logger.debug(
                "Broker: Using username '{username}' to connect",
                username=self._username,
            )
            connection_params["credentials"] = PlainCredentials(
                self._username, self._password
            )
        else:
            logger.debug(
                "Broker: Username and/or password not specified, using anonymous connection"
            )

        try:
            self._connection = BlockingConnection(
                ConnectionParameters(**connection_params)
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
