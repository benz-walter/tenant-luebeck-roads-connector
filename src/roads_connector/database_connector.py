from loguru import logger
from sqlalchemy import Engine, MetaData, Table, create_engine, inspect
from sqlalchemy.exc import NoSuchTableError, OperationalError

from .broker_connector import BrokerConnector
from .exceptions import RoadsError
from .interrupt import is_interrupted


class DatabaseConnector:
    def __init__(self, url: str, tables: list[str], broker: BrokerConnector):
        self._url = url
        self._tables = [t.strip() for t in tables or [] if t.strip()]
        self._broker = broker

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

        with engine.connect() as connection, self._broker as connector:
            for table_name in self._tables:
                if is_interrupted():
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
