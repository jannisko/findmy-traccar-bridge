
from sqlalchemy import Column, Integer, String, Float, UniqueConstraint, create_engine
from sqlalchemy.orm import declarative_base, sessionmaker, Session
from sqlalchemy.exc import IntegrityError

from loguru import logger

Base = declarative_base()

# ORM model for all metadata
class MetaData(Base):
    __tablename__ = "metadata"
    name = Column(String, primary_key=True, nullable=False)
    value = Column(String, nullable=False)

# ORM model for all locations of all keys
class Location(Base):
    __tablename__ = "locations"
    id = Column(Integer, primary_key = True, autoincrement = True)
    keyId = Column(Integer, nullable=False)
    timestamp = Column(Integer, nullable=False)
    lat = Column(Float, nullable=False)
    lon = Column(Float, nullable=False)

    __table_args__ = (
        UniqueConstraint("keyId", "timestamp", name="uix_id_timestamp"),
    )

# ORM model to track which key has been pushed to which endpoint already
class PushedLocation(Base):
    __tablename__ = "pushed_locations"
    id = Column(Integer, primary_key = True, autoincrement = True)
    keyId = Column(Integer, nullable=False)
    endpointId = Column(Integer, nullable=False)
    timestamp = Column(Integer, nullable=False)

    __table_args__ = (
        UniqueConstraint("keyId", "endpointId", "timestamp", name="uix_key_endpoint_timestamp"),
    )

class MetaDataServer():
    def __init__(self, session: Session):
        self.session = session
    
    def setMetaData(self, name: str, value: str) -> None:
        
        entry = self.session.query(MetaData).filter_by(name=name).first()
        
        if not entry:
            entry = MetaData(name=name, value=value)
            self.session.add(entry)
        
        entry.value = value

        self.session.commit()
    
    def getMetaData(self, name: str) -> str | None:
        entry = self.session.query(MetaData).filter_by(name=name).first()
        return entry.value if entry else None

class LocationStorage:
    def __init__(self, session: Session):
        self.session = session
    
    def addLocation(self, keyId: int, timestamp: int, lat: float, lon: float) -> None:
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
            # Already exists -> rollback
            self.session.rollback()
            logger.debug(f"Location already exists: {keyId}, {timestamp}")


    def getPendingLocations(self, keyId: int, endpointId: int) -> list[Location]:
        """
        Return all locations for a given key that have not yet been pushed to the endpoint.
        """
        # Subquery to check already pushed timestamps
        pushed_exists = (
            self.session.query(PushedLocation)
            .filter(
                PushedLocation.keyId == keyId,
                PushedLocation.endpointId == endpointId,
                PushedLocation.timestamp == Location.timestamp
            )
            .exists()
        )

        # Return pending locations
        return (
            self.session.query(Location)
            .filter(Location.keyId == keyId)
            .filter(~pushed_exists)
            .order_by(Location.timestamp)
            .all()
        )

    def markAsPushed(self, keyId: int, endpointId: int, timestamp: int) -> None:
            """
            Mark a timestamp as successfully pushed for a given key and endpoint.
            """
            pushed_location = PushedLocation(
                keyId=keyId,
                endpointId=endpointId,
                timestamp=timestamp
            )
            self.session.add(pushed_location)
            try:
                self.session.commit()
                logger.debug(f"Marked timestamp {timestamp} as pushed for key {keyId} to endpoint {endpointId}")
            except IntegrityError:
                self.session.rollback()
                logger.debug(f"Timestamp {timestamp} already marked as pushed for key {keyId} to endpoint {endpointId}; rolling back")


# Helper to initialize database and session
def initDb(db_path: str) -> Session:
    engine = create_engine(f"sqlite:///{db_path}", echo=False, future=True)
    Base.metadata.create_all(engine)
    SessionLocal = sessionmaker(bind=engine)
    return SessionLocal()
