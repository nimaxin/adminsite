from typing import Any

from sqlalchemy import Connection, Engine, MetaData, Table, create_engine, inspect, text
from sqlalchemy.orm import Session
from sqlalchemy.schema import CreateColumn

from adminsite.backends.sqlalchemy.session import Database, SessionSource


class Store:
    """Tables adminsite keeps for itself, such as the audit log.

    Given a SQLite URL, the store opens that file and creates its tables,
    which needs no setup. Given your engine, it uses your database and
    leaves the tables to your migrations, unless `create_table` says
    otherwise.
    """

    metadata: MetaData

    def __init__(
        self,
        source: Database | SessionSource | str,
        *,
        create_table: bool | None = None,
    ) -> None:
        own_file = isinstance(source, str) and source.startswith("sqlite")
        self._owned_engine: Engine | None = None
        if isinstance(source, str):
            options: dict[str, Any] = (
                {"connect_args": {"check_same_thread": False}} if own_file else {}
            )
            self._owned_engine = create_engine(source, **options)
            self.database = Database(self._owned_engine)
        elif isinstance(source, Database):
            self.database = source
        else:
            self.database = Database(source)

        # Creating a table in someone's own database uninvited is rude, so
        # that only happens by default for the file adminsite owns.
        self.create_table = own_file if create_table is None else create_table
        self._ready = False

    def close(self) -> None:
        """Release the connections, if this store opened its own database."""
        if self._owned_engine is not None:
            self._owned_engine.dispose()

    async def prepare(self) -> None:
        """Create the tables if this store is allowed to."""
        if self._ready or not self.create_table:
            return
        async with self.database.session() as session:
            await session.run(self._create_tables)
            await session.commit()
        self._ready = True

    def _create_tables(self, session: Session) -> None:
        connection = session.connection()
        self.metadata.create_all(connection)
        # create_all leaves a table that is already there alone, so one made
        # by an older adminsite gets the columns and indexes added since.
        for table in self.metadata.sorted_tables:
            add_missing(connection, table)


def add_missing(connection: Connection, table: Table) -> None:
    """Add the columns and indexes an existing table does not have yet.

    Every column added after a table's first release may be empty, so
    adding it needs nothing more than ALTER TABLE ... ADD COLUMN.
    """
    present = {column["name"] for column in inspect(connection).get_columns(table.name)}
    quoted = connection.dialect.identifier_preparer.format_table(table)
    for column in table.columns:
        if column.name not in present:
            spec = CreateColumn(column).compile(dialect=connection.dialect)
            connection.execute(text(f"ALTER TABLE {quoted} ADD COLUMN {spec}"))
    for index in table.indexes:
        index.create(connection, checkfirst=True)
