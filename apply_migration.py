from database.base import migrate_database, create_async_engine_with_config
import asyncio
from dotenv import load_dotenv  # Добавьте импорт

async def main():
    load_dotenv()  # Загрузите переменные окружения из .env файла
    engine = create_async_engine_with_config()
    await migrate_database(engine)
    await engine.dispose()

if __name__ == "__main__":
    asyncio.run(main())