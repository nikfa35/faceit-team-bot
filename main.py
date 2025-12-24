import asyncio
import os
import logging
import signal
import sys

from sqlalchemy import text
from dotenv import load_dotenv
from aiogram import Bot, Dispatcher
from aiogram.fsm.storage.memory import MemoryStorage
from aiogram.client.default import DefaultBotProperties
from aiogram.enums import ParseMode

from database.base import create_async_engine_with_config, init_db, create_sessionmaker, migrate_database
from services.faceit import FaceitService
from app.handlers import router
from app.middleware import DbSessionMiddleware, ServiceMiddleware, ErrorHandlingMiddleware

# Загрузка переменных окружения
load_dotenv()

# Настройка логирования
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    handlers=[
        logging.StreamHandler(),
        logging.FileHandler("bot.log")
    ]
)
logger = logging.getLogger(__name__)

class BotRunner:
    def __init__(self):
        self.shutdown_event = asyncio.Event()
        signal.signal(signal.SIGINT, self.handle_shutdown)
        signal.signal(signal.SIGTERM, self.handle_shutdown)

    def handle_shutdown(self, signum, frame):
        logger.info(f"Получен сигнал завершения {signum}")
        self.shutdown_event.set()

    async def initialize_services(self):
        """Инициализация всех сервисов"""
        try:
            # 1. Инициализация БД
            logger.info("Инициализация базы данных...")
            self.engine = create_async_engine_with_config()
            
            # Проверка подключения
            async with self.engine.connect() as conn:
                await conn.execute(text("SELECT 1"))
            
            # Применение миграций
            await init_db(self.engine)
            await migrate_database(self.engine)
            
            self.async_session_maker = create_sessionmaker(self.engine)

            # 2. Инициализация FaceitService
            self.faceit_service = FaceitService(
                session_pool=self.async_session_maker,
                api_keys=None,
                cache_ttl=3600,
                maxsize=1000
            )
            await self.faceit_service.initialize()
            
            return True
        except Exception as e:
            logger.critical(f"Ошибка инициализации сервисов: {e}", exc_info=True)
            return False

    async def run_bot(self):
        """Основной цикл работы бота"""
        bot_token = os.getenv('BOT_TOKEN')
        if not bot_token:
            logger.critical("Отсутствует BOT_TOKEN")
            return False

        bot = Bot(
            token=bot_token,
            default=DefaultBotProperties(parse_mode=ParseMode.HTML))
        
        dp = Dispatcher(storage=MemoryStorage())
        
        # Подключение middleware
        dp.update.middleware(ServiceMiddleware(self.faceit_service))
        dp.update.middleware(DbSessionMiddleware(session_pool=self.async_session_maker))
        dp.update.middleware(ErrorHandlingMiddleware())
        dp.include_router(router)

        allowed_updates = [
            "message", 
            "callback_query", 
            "pre_checkout_query",
            "successful_payment"
        ]

        try:
            logger.info("Запуск бота...")
            await dp.start_polling(
                bot,
                allowed_updates=allowed_updates,
                skip_updates=False
            )
            return True
        except Exception as e:
            logger.error(f"Ошибка в работе бота: {e}", exc_info=True)
            return False
        finally:
            await bot.session.close()

    async def cleanup(self):
        """Очистка ресурсов"""
        logger.info("Очистка ресурсов...")
        if hasattr(self, 'faceit_service'):
            await self.faceit_service.close()
            logger.info("FaceitService закрыт")
        if hasattr(self, 'engine'):
            await self.engine.dispose()
            logger.info("Движок БД закрыт")

    async def run(self):
        """Основной цикл приложения"""
        while not self.shutdown_event.is_set():
            try:
                if not await self.initialize_services():
                    break
                    
                if not await self.run_bot():
                    break
                    
            except Exception as e:
                logger.error(f"Критическая ошибка: {e}", exc_info=True)
                await asyncio.sleep(5)  # Пауза перед перезапуском
            finally:
                await self.cleanup()
                
        logger.info("Приложение завершено")

async def main():
    runner = BotRunner()
    await runner.run()

if __name__ == '__main__':
    # Для корректной работы в Windows
    if sys.platform == 'win32':
        asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())
    
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        logger.info("Приложение остановлено пользователем")
    except Exception as e:
        logger.critical(f"Необработанная ошибка: {e}", exc_info=True)
        sys.exit(1)