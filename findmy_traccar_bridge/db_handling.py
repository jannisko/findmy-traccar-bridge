
from sqlalchemy import Column, Integer, String, Float, UniqueConstraint, create_engine
from sqlalchemy.orm import declarative_base, sessionmaker, Session
from sqlalchemy.exc import IntegrityError

from loguru import logger

Base = declarative_base()

class MetaData(Base):
    """
    ORM model representing key-value metadata entries.

    Each row stores a unique metadata name and its associated value.
    The `name` column acts as the primary key.
    """
    __tablename__ = "metadata"

    name = Column(String, primary_key=True, nullable=False)
    value = Column(String, nullable=False)

class Location(Base):
    """
    ORM model representing the locations of keys.

    Each location entry is uniquely identified by the combination
    of `keyId` and `timestamp`.
    """
    __tablename__ = "locations"

    id = Column(Integer, primary_key=True, autoincrement=True)
    keyId = Column(Integer, nullable=False)
    timestamp = Column(Integer, nullable=False)
    lat = Column(Float, nullable=False)
    lon = Column(Float, nullable=False)

    __table_args__ = (
        UniqueConstraint("keyId", "timestamp", name="uix_id_timestamp"),
    )

class PushedLocation(Base):
    """
    ORM model tracking which locations have already been pushed
    to specific endpoints.

    Ensures that the same (keyId, endpointId, timestamp) combination
    cannot be stored more than once.
    """
    __tablename__ = "pushed_locations"

    id = Column(Integer, primary_key=True, autoincrement=True)
    keyId = Column(Integer, nullable=False)
    endpointId = Column(Integer, nullable=False)
    timestamp = Column(Integer, nullable=False)

    __table_args__ = (
        UniqueConstraint(
            "keyId",
            "endpointId",
            "timestamp",
            name="uix_key_endpoint_timestamp"
        ),
    )

class MetaDataServer:
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

    def setMetaData(self, name: str, value: str) -> None:
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

    def getMetaData(self, name: str, default: str = "") -> str | None:
        """
        Retrieve a metadata value by name.

        Args:
            name: Metadata key to retrieve.
            default: Value returned if the key does not exist.

        Returns:
            The stored metadata value, or `default` if not found.
        """
        entry = self.session.query(MetaData).filter_by(name=name).first()
        return entry.value if entry else default

class LocationServer:
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

    def addLocation(self, keyId: int, timestamp: int, lat: float, lon: float) -> None:
        """
        Store a new location entry.

        If a location with the same (keyId, timestamp) already exists,
        the operation is ignored.

        Args:
            keyId: Identifier of the tracked key.
            timestamp: Unix timestamp of the location record.
            lat: Latitude coordinate.
            lon: Longitude coordinate.
        """
        location = Location(
            keyId=keyId,
            timestamp=timestamp,
            lat=lat,
            lon=lon
        )

        self.session.add(location)
        try:
            self.session.commit()
            logger.debug(f"Stored location: {keyId}, {timestamp}, {lat}, {lon}")
        except IntegrityError:
            self.session.rollback()
            logger.debug(f"Location already exists: {keyId}, {timestamp}")


    def getPendingLocations(self, keyId: int, endpointId: int) -> list[Location]:
        """
        Retrieve all locations for a given key that have not yet been
        pushed to a specific endpoint.

        Args:
            keyId: Identifier of the tracked key.
            endpointId: Identifier of the endpoint.

        Returns:
            A list of `Location` objects ordered by timestamp
            that have not yet been marked as pushed.
        """
        pushed_exists = (
            self.session.query(PushedLocation)
            .filter(
                PushedLocation.keyId == keyId,
                PushedLocation.endpointId == endpointId,
                PushedLocation.timestamp == Location.timestamp
            )
            .exists()
        )

        return (
            self.session.query(Location)
            .filter(Location.keyId == keyId)
            .filter(~pushed_exists)
            .order_by(Location.timestamp)
            .all()
        )

    def markAsPushed(self, keyId: int, endpointId: int, timestamp: int) -> None:
        """
        Mark a specific location timestamp as successfully pushed
        to an endpoint.

        If the entry already exists, the operation is ignored.

        Args:
            keyId: Identifier of the tracked key.
            endpointId: Identifier of the endpoint.
            timestamp: Timestamp of the location that was pushed.
        """
        pushedLocation = PushedLocation(
            keyId=keyId,
            endpointId=endpointId,
            timestamp=timestamp
        )

        self.session.add(pushedLocation)
        try:
            self.session.commit()
            logger.debug(
                f"Marked timestamp {timestamp} as pushed for key {keyId} and endpoint {endpointId}"
            )
        except IntegrityError:
            self.session.rollback()
            logger.debug(
                f"Timestamp {timestamp} already marked as pushed for key {keyId} and endpoint {endpointId}; rolling back"
            )

def initDb(db_path: str) -> Session:
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
    SessionLocal = sessionmaker(bind=engine)
    return SessionLocal()