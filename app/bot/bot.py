import os
import asyncio
import logging
import httpx

from aiogram import Bot, Dispatcher
from aiogram.fsm.storage.redis import RedisStorage, DefaultKeyBuilder
from redis.asyncio import Redis

from app.bot.handlers.main import register_handlers

logging.basicConfig(level=logging.INFO)

BOT_TOKEN = os.getenv("BOT_TOKEN", "")
REDIS_URL = os.getenv("REDIS_URL", "redis://redis:6379/0")


async def main():
    if not BOT_TOKEN:
        raise RuntimeError("BOT_TOKEN is empty. Set BOT_TOKEN in .env")

    redis = Redis.from_url(REDIS_URL)
    storage = RedisStorage(
        redis=redis,
        key_builder=DefaultKeyBuilder(with_bot_id=True, with_destiny=True),
    )

    bot = Bot(BOT_TOKEN)
    dp = Dispatcher(storage=storage)
    client = httpx.AsyncClient()

    register_handlers(dp, bot, client)

    try:
        await dp.start_polling(bot)
    finally:
        await client.aclose()
        await bot.session.close()
        await storage.close()
        await redis.aclose()


if __name__ == "__main__":
    asyncio.run(main())