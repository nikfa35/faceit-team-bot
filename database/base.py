import os
import logging
from sqlalchemy.ext.asyncio import create_async_engine, AsyncEngine, AsyncSession
from sqlalchemy.orm import sessionmaker, declarative_base
from sqlalchemy import text

logger = logging.getLogger(__name__)

# Создаем единую декларативную базу для всего проекта
Base = declarative_base()

def create_async_engine_with_config():
    """Создает асинхронный движок с конфигурацией"""
    POSTGRES_URL = os.getenv('POSTGRES_URL')
    
    return create_async_engine(
        POSTGRES_URL,
        echo=True,
        pool_timeout=120,
        connect_args={
            "timeout": 30,
            "command_timeout": 60
        }
    )

async def init_db(engine: AsyncEngine):
    """Инициализирует базу данных (только создание таблиц)"""
    try:
        async with engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)
            logger.info("Таблицы успешно созданы")
    except Exception as e:
        logger.critical(f"Ошибка инициализации БД: {e}", exc_info=True)
        raise

async def migrate_database(engine: AsyncEngine):
    """Применяет миграции к базе данных (отдельно от создания таблиц)"""
    async with engine.begin() as conn:
        try:
            logger.info("Начало миграции базы данных...")
            
            # 1. Миграции для изменения типа столбцов
            await conn.execute(text("""
                ALTER TABLE users 
                ALTER COLUMN tg_id TYPE BIGINT;
            """))
            await conn.execute(text("""
                ALTER TABLE user_errors 
                ALTER COLUMN tg_id TYPE BIGINT;
            """))
            await conn.execute(text("""
                ALTER TABLE appeals 
                ALTER COLUMN tg_id TYPE BIGINT;
            """))
            
            # 2. Миграции для добавления новых столбцов в users
            await conn.execute(text("""
                ALTER TABLE users 
                ADD COLUMN IF NOT EXISTS consent_accepted BOOLEAN DEFAULT FALSE,
                ADD COLUMN IF NOT EXISTS invite_count INTEGER DEFAULT 0;
            """))
            
            # 3. Миграции для добавления новых столбцов в user_settings
            await conn.execute(text("""
                ALTER TABLE user_settings 
                ADD COLUMN IF NOT EXISTS min_age INTEGER DEFAULT 12,
                ADD COLUMN IF NOT EXISTS max_age INTEGER DEFAULT 60;
            """))
            
            # 4. Миграции для таблицы payments (добавляем отсутствующие столбцы)
            await conn.execute(text("""
                ALTER TABLE payments 
                ADD COLUMN IF NOT EXISTS description VARCHAR(256);
            """))
            await conn.execute(text("""
                ALTER TABLE payments 
                ADD COLUMN IF NOT EXISTS status VARCHAR(20);
            """))
            await conn.execute(text("""
                ALTER TABLE payments 
                ADD COLUMN IF NOT EXISTS subscription_type VARCHAR(20);
            """))
            await conn.execute(text("""
                ALTER TABLE payments 
                ADD COLUMN IF NOT EXISTS created_at TIMESTAMP;
            """))

            # 5. Миграции для новых обязательных полей в user_states
            await conn.execute(text("""
                ALTER TABLE user_states 
                ADD COLUMN IF NOT EXISTS communication_method VARCHAR(20);
            """))
            await conn.execute(text("""
                ALTER TABLE user_states 
                ADD COLUMN IF NOT EXISTS timezone VARCHAR(20);
            """))
            
            logger.info("Миграция успешно завершена")
        except Exception as e:
            logger.error(f"Ошибка миграции: {e}", exc_info=True)
            await conn.rollback()
            raise

# Добавленная функция
def create_sessionmaker(engine: AsyncEngine) -> sessionmaker:
    """Создает фабрику асинхронных сессий"""
    return sessionmaker(
        bind=engine,
        class_=AsyncSession,
        expire_on_commit=False,
        autoflush=False
    )