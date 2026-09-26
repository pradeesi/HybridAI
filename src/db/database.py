"""
Purpose: Asynchronous database connection, session management, and schema initialization.
Architecture/Context: Foundation for all data persistence across CRM and MCP services.
Dependencies/Side Effects: Connects to PostgreSQL via asyncpg with defensive SQLite fallback for local test agility.
"""

import logging
from typing import AsyncGenerator, Optional
from sqlalchemy.ext.asyncio import (
    AsyncSession, async_sessionmaker, create_async_engine
)
from src.core.config import settings
from src.db.models import Base

logger = logging.getLogger("hybrid_ai.database")

class SessionFactoryProxy:
    """
    Summary:
        Dynamic callable proxy for SQLAlchemy async_sessionmaker.
        Ensures all modules importing AsyncSessionLocal transparently route to the active
        engine (PostgreSQL or SQLite fallback) even if reconfigured during application lifespan.
    """

    def __init__(self) -> None:
        """
        Summary:
            Initializes the proxy without an underlying sessionmaker.
        """
        self._sessionmaker: Optional[async_sessionmaker[AsyncSession]] = None

    def configure(self, sessionmaker: async_sessionmaker[AsyncSession]) -> None:
        """
        Summary:
            Updates the underlying sessionmaker reference.

        Parameters:
            sessionmaker (async_sessionmaker[AsyncSession]): Configured SQLAlchemy session factory.
        """
        self._sessionmaker = sessionmaker

    def __call__(self, *args, **kwargs) -> AsyncSession:
        """
        Summary:
            Invokes the active sessionmaker to yield an AsyncSession.

        Return Value:
            AsyncSession: Fresh asynchronous SQLAlchemy session.

        Exceptions/Errors:
            RuntimeError: If called before configure() has set an active sessionmaker.
        """
        if self._sessionmaker is None:
            raise RuntimeError("Database session factory has not been initialized.")
        return self._sessionmaker(*args, **kwargs)


# Global singleton proxy instance
AsyncSessionLocal = SessionFactoryProxy()

# Initialize primary database engine
database_url = settings.DATABASE_URL

# Defensive check: if Cloud SQL host socket is empty (e.g. ?host=/cloudsql/ or ?host=/cloudsql)
# default directly to SQLite to avoid socket connection timeouts
if "?host=/cloudsql/" in database_url or database_url.endswith("/cloudsql/") or database_url.endswith("/cloudsql"):
    logger.warning(
        "Detected unpopulated Cloud SQL instance socket path in DATABASE_URL. "
        "Switching immediately to local SQLite fallback."
    )
    database_url = "sqlite+aiosqlite:///./telecom_local.db"

try:
    engine = create_async_engine(
        database_url,
        echo=settings.DEBUG,
        future=True,
        pool_pre_ping=True
    )
except Exception as exc:
    logger.warning("Failed to create engine with %s (%s). Using SQLite.", database_url, exc)
    database_url = "sqlite+aiosqlite:///./telecom_local.db"
    engine = create_async_engine(database_url, echo=settings.DEBUG)

# Configure proxy with initial sessionmaker
AsyncSessionLocal.configure(
    async_sessionmaker(
        bind=engine,
        class_=AsyncSession,
        expire_on_commit=False,
        autoflush=False
    )
)


async def get_db_session() -> AsyncGenerator[AsyncSession, None]:
    """
    Summary:
        FastAPI dependency and context provider yielding an isolated asynchronous database session.

    Yields:
        AsyncSession: Active SQLAlchemy async session.
    """
    async with AsyncSessionLocal() as session:
        try:
            yield session
        except Exception:
            await session.rollback()
            raise
        finally:
            await session.close()


async def init_db() -> None:
    """
    Summary:
        Initializes database schema and tables.
        If the primary PostgreSQL server is unreachable, falls back to local SQLite engine.

    Exceptions/Errors:
        Logs connection errors and switches engine cleanly.
    """
    global engine

    try:
        async with engine.begin() as conn:
            logger.info("Verifying database connectivity and creating schema tables...")
            await conn.run_sync(Base.metadata.create_all)
            logger.info("Database schema initialized successfully.")
    except Exception as exc:
        logger.warning(
            "Primary database connection failed (%s). Falling back to local SQLite engine...",
            exc
        )
        sqlite_url = "sqlite+aiosqlite:///./telecom_local.db"
        engine = create_async_engine(sqlite_url, echo=settings.DEBUG)
        AsyncSessionLocal.configure(
            async_sessionmaker(
                bind=engine,
                class_=AsyncSession,
                expire_on_commit=False,
                autoflush=False
            )
        )
        async with engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)
            logger.info("SQLite fallback database initialized at ./telecom_local.db")
