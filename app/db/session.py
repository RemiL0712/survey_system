import os
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

DATABASE_URL = os.getenv("DATABASE_URL")

engine = create_async_engine(DATABASE_URL, echo=False)
SessionLocal = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)

async def get_session() -> AsyncSession:
    async with SessionLocal() as session:
        yield session
