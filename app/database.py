# app/database.py
#
# Creates the async SQLAlchemy engine + session factory for Postgres.
# This must match your Docker compose credentials.

import os
from sqlalchemy.ext.asyncio import create_async_engine, async_sessionmaker, AsyncSession
from sqlalchemy.orm import declarative_base

POSTGRES_USER = "tracker"
POSTGRES_PASSWORD = "trackerpass"
POSTGRES_DB = "stock_tracker"
POSTGRES_HOST = "localhost"
POSTGRES_PORT = "5433"  # note: we mapped 5433 -> 5432 in docker

DATABASE_URL = os.getenv(
    "DATABASE_URL",
    f"postgresql+asyncpg://{POSTGRES_USER}:{POSTGRES_PASSWORD}@{POSTGRES_HOST}:{POSTGRES_PORT}/{POSTGRES_DB}",
)

engine = create_async_engine(
    DATABASE_URL,
    echo=False,
    future=True,
)

AsyncSessionLocal = async_sessionmaker(
    bind=engine,
    class_=AsyncSession,
    expire_on_commit=False,
)

Base = declarative_base()

# FastAPI dependency-style getter
async def get_db():
    async with AsyncSessionLocal() as session:
        yield session

