"""
Purpose: Asynchronous database connection, session management, and schema initialization.
Architecture/Context: Foundation for all data persistence across CRM and MCP services.
Dependencies/Side Effects: Connects to PostgreSQL via asyncpg with defensive SQLite fallback for local test agility.
"""

import logging
from typing import AsyncGenerator
from sqlalchemy.ext.asyncio import (
    AsyncSession, async_sessionmaker, create_async_engine
)
from src.core.config import settings
from src.db.models import Base

logger = logging.getLogger("hybrid_ai.database")

# Primary engine initialization
database_url = settings.DATABASE_URL

# Provide defensive fallback to sqlite if running standalone without postgres container
if "postgresql" in database_url and "localhost" in database_url:
    # We will attempt connection; if failed during init_db, fallback to sqlite
    pass

engine = create_async_engine(
    database_url,
    echo=settings.DEBUG,
    future=True,
    pool_pre_ping=True
)

AsyncSessionLocal = async_sessionmaker(
    bind=engine,
    class_=AsyncSession,
    expire_on_commit=False,
    autoflush=False
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
    global engine, AsyncSessionLocal

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
        AsyncSessionLocal = async_sessionmaker(
            bind=engine,
            class_=AsyncSession,
            expire_on_commit=False,
            autoflush=False
        )
        async with engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)
            logger.info("SQLite fallback database initialized at ./telecom_local.db")
