import pathlib

from loguru import logger
from sqlalchemy import UniqueConstraint, create_engine
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import (
    Mapped,
    Session,
    declarative_base,
    mapped_column,
    sessionmaker,
)

Base = declarative_base()


class MetaData(Base):
    """
    ORM model representing key-value metadata entries.

    Each row stores a unique metadata name and its associated value.
    The `name` column acts as the primary key.
    """

    __tablename__ = "metadata"

    name: Mapped[str] = mapped_column(primary_key=True)
    value: Mapped[str]


class Location(Base):
    """
    ORM model representing the locations of keys.

    Each location entry is uniquely identified by the combination
    of `key_id` and `timestamp`.
    """

    __tablename__ = "locations"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    key_id: Mapped[int]
    timestamp: Mapped[int]
    lat: Mapped[float]
    lon: Mapped[float]

    __table_args__ = (UniqueConstraint("key_id", "timestamp", name="uix_id_timestamp"),)


class PushedLocation(Base):
    """
    ORM model tracking which locations have already been pushed
    to specific endpoints.

    Ensures that the same (key_id, endpoint_id, timestamp) combination
    cannot be stored more than once.
    """

    __tablename__ = "pushed_locations"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    key_id: Mapped[int]
    endpoint_id: Mapped[int]
    timestamp: Mapped[int]

    __table_args__ = (
        UniqueConstraint(
            "key_id", "endpoint_id", "timestamp", name="uix_key_endpoint_timestamp"
        ),
    )


class MetaDataService:
    """
    Service class for managing metadata entries in the database.
    """

    def __init__(self, session: Session):
        """
        Initialize the metadata server.

        Args:
            session: SQLAlchemy session used for database access.
        """
        self.session = session

    def set_metadata(self, name: str, value: str) -> None:
        """
        Create or update a metadata entry.

        If an entry with the given name exists, its value is updated.
        Otherwise, a new entry is created.

        Args:
            name: Unique metadata key.
            value: Metadata value to store.
        """
        entry = self.session.query(MetaData).filter_by(name=name).first()

        if not entry:
            entry = MetaData(name=name, value=value)
            self.session.add(entry)

        entry.value = value
        self.session.commit()

    def get_metadata(self, name: str, default: str = None) -> str | None:
        """
        Retrieve a metadata value by name.

        Args:
            name: Metadata key to retrieve.
            default: Value returned if the key does not exist.

        Returns:
            The stored metadata value, or `default` if not found.
        """
        entry = self.session.query(MetaData).filter_by(name=name).first()
        return entry.value if entry is not None else default


class LocationService:
    """
    Service class for storing and managing location records
    and tracking push status per endpoint.
    """

    def __init__(self, session: Session):
        """
        Initialize the location storage service.

        Args:
            session: SQLAlchemy session used for database access.
        """
        self.session = session

    def add_location(self, key_id: int, timestamp: int, lat: float, lon: float) -> None:
        """
        Store a new location entry.

        If a location with the same (key_id, timestamp) already exists,
        the operation is ignored.

        Args:
            key_id: Identifier of the tracked key.
            timestamp: Unix timestamp of the location record.
            lat: Latitude coordinate.
            lon: Longitude coordinate.
        """
        location = Location(key_id=key_id, timestamp=timestamp, lat=lat, lon=lon)

        self.session.add(location)
        try:
            self.session.commit()
            logger.debug(f"Stored location: {key_id}, {timestamp}, {lat}, {lon}")
        except IntegrityError:
            self.session.rollback()
            logger.debug(f"Location already exists: {key_id}, {timestamp}")

    def get_pending_locations(self, key_id: int, endpoint_id: int) -> list[Location]:
        """
        Retrieve all locations for a given key that have not yet been
        pushed to a specific endpoint.

        Args:
            key_id: Identifier of the tracked key.
            endpoint_id: Identifier of the endpoint.

        Returns:
            A list of `Location` objects ordered by timestamp
            that have not yet been marked as pushed.
        """
        pushed_exists = (
            self.session.query(PushedLocation)
            .filter(
                PushedLocation.key_id == key_id,
                PushedLocation.endpoint_id == endpoint_id,
                PushedLocation.timestamp == Location.timestamp,
            )
            .exists()
        )

        return (
            self.session.query(Location)
            .filter(Location.key_id == key_id)
            .filter(~pushed_exists)
            .order_by(Location.timestamp)
            .all()
        )

    def mark_as_pushed(self, key_id: int, endpoint_id: int, timestamp: int) -> None:
        """
        Mark a specific location timestamp as successfully pushed
        to an endpoint.

        If the entry already exists, the operation is ignored.

        Args:
            key_id: Identifier of the tracked key.
            endpoint_id: Identifier of the endpoint.
            timestamp: Timestamp of the location that was pushed.
        """
        pushedLocation = PushedLocation(
            key_id=key_id, endpoint_id=endpoint_id, timestamp=timestamp
        )

        self.session.add(pushedLocation)
        try:
            self.session.commit()
            logger.debug(
                f"Marked timestamp {timestamp} as pushed for key {key_id} and endpoint {endpoint_id}"
            )
        except IntegrityError:
            self.session.rollback()
            logger.debug(
                f"Timestamp {timestamp} already marked as pushed for key {key_id} and endpoint {endpoint_id}; rolling back"
            )
            logger.warning(
                "A location was about to be marked as pushed repeatedly. This should never happen logically and indicates a bug."
            )


def init_db(db_path: str) -> Session:
    """
    Initialize the SQLite database and return a session.

    This function creates all tables defined in the ORM models
    if they do not already exist.

    Args:
        db_path: Filesystem path to the SQLite database file.

    Returns:
        A configured SQLAlchemy `Session` instance.
    """
    engine = create_engine(f"sqlite:///{db_path}", echo=False, future=True)
    Base.metadata.create_all(engine)
    Session_local = sessionmaker(bind=engine)
    return Session_local()
