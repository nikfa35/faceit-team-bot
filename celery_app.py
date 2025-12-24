from celery import Celery
from app.handlers import check_vip_expirations
from database.base import create_async_engine_with_config, create_sessionmaker
from aiogram import Bot
import os
from dotenv import load_dotenv
from services.faceit import FaceitService
from database.models import User, UserState
from sqlalchemy import select, update, func, extract
from datetime import datetime
import logging
import asyncio
from celery.schedules import crontab
from redis.exceptions import ConnectionError as RedisConnectionError

logger = logging.getLogger(__name__)
load_dotenv()

# Конфигурация Celery с улучшенными параметрами подключения
app = Celery('tasks', broker='redis://localhost:6379/0')

# Настройки для стабильного подключения
app.conf.update(
    broker_connection_retry_on_startup=True,
    broker_connection_retry=True,
    broker_connection_max_retries=10,
    broker_heartbeat=30,
    broker_connection_timeout=30,
    worker_cancel_long_running_tasks_on_connection_loss=False,
    beat_scheduler='celery.beat.PersistentScheduler',
    beat_schedule_filename='celerybeat-schedule.db',  # Используем SQLite вместо shelve
    task_serializer='json',
    result_serializer='json',
    accept_content=['json'],
    timezone='UTC',
    enable_utc=True
)

# Расписание задач
app.conf.beat_schedule = {
    'check-vip-expirations': {
        'task': 'celery_app.run_vip_check',
        'schedule': crontab(hour=3, minute=0),
    },
    'update-elo': {
        'task': 'celery_app.update_user_elos',
        'schedule': crontab(hour=4, minute=0),
    },
    'update-ages': {
        'task': 'celery_app.update_user_ages',
        'schedule': crontab(hour=5, minute=0),
    },
     'check-blocked-users': {
        'task': 'celery_app.check_blocked_users',
        'schedule': crontab(hour=2, minute=30),  # Выполнять ежедневно в 2:30
    },
}


def setup_async_environment():
    """Создает и настраивает event loop для асинхронных задач"""
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    return loop
@app.task(bind=True, max_retries=3)
def check_blocked_users(self):
    """Проверка заблокировавших бота пользователей"""
    BOT_TOKEN = os.getenv('BOT_TOKEN')
    if not BOT_TOKEN:
        logger.error("BOT_TOKEN не установлен")
        return

    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    
    try:
        engine = create_async_engine_with_config()
        session_pool = create_sessionmaker(engine)
        bot = Bot(token=BOT_TOKEN)

        async def _check():
            async with session_pool() as session:
                users = await session.scalars(select(User))
                deleted_count = 0
                
                for user in users:
                    try:
                        await bot.send_chat_action(chat_id=user.tg_id, action='typing')
                        await asyncio.sleep(0.1)  # Задержка между проверками
                    except Exception as e:
                        if "bot was blocked" in str(e).lower():
                            await delete_user_completely(session, user.id)
                            deleted_count += 1
                            logger.info(f"Удалён заблокировавший пользователь: {user.tg_id}")
                
                await session.commit()
                return deleted_count

        deleted = loop.run_until_complete(_check())
        logger.info(f"Удалено заблокировавших пользователей: {deleted}")
        
    except Exception as e:
        logger.error(f"Ошибка проверки блокировок: {e}")
        self.retry(exc=e, countdown=60)
    finally:
        loop.run_until_complete(bot.session.close())
        loop.run_until_complete(engine.dispose())
        loop.close()

@app.task(bind=True, max_retries=3)
def run_vip_check(self):
    """Проверка истечения VIP-статуса"""
    BOT_TOKEN = os.getenv('BOT_TOKEN')
    if not BOT_TOKEN:
        logger.error("BOT_TOKEN не установлен")
        return

    engine = None
    bot = None
    loop = setup_async_environment()

    try:
        engine = create_async_engine_with_config()
        session_pool = create_sessionmaker(engine)
        bot = Bot(token=BOT_TOKEN)

        loop.run_until_complete(
            check_vip_expirations(session_pool, bot)
        )
    except RedisConnectionError as e:
        logger.error(f"Ошибка подключения к Redis: {e}")
        self.retry(exc=e, countdown=60)
    except Exception as e:
        logger.error(f"Неожиданная ошибка: {e}")
    finally:
        try:
            if engine:
                loop.run_until_complete(engine.dispose())
            if bot:
                loop.run_until_complete(bot.session.close())
        except Exception as e:
            logger.error(f"Ошибка при закрытии соединений: {e}")
        finally:
            loop.close()

@app.task(bind=True, max_retries=3)
def update_user_elos(self):
    """Обновление ELO пользователей"""
    logger.info("### ЗАПУСК ЗАДАЧИ ОБНОВЛЕНИЯ ELO ###")
    loop = setup_async_environment()

    try:
        loop.run_until_complete(update_elos_async())
    except RedisConnectionError as e:
        logger.error(f"Ошибка подключения к Redis: {e}")
        self.retry(exc=e, countdown=60)
    except Exception as e:
        logger.error(f"Неожиданная ошибка: {e}")
    finally:
        loop.close()

async def update_elos_async():
    """Асинхронная часть обновления ELO"""
    logger.info("Инициализация FaceitService")
    
    api_keys_str = os.getenv("FACEIT_API_KEYS", "")
    api_keys = [key.strip() for key in api_keys_str.split(',') if key.strip()] or None
    
    engine = create_async_engine_with_config()
    session_pool = create_sessionmaker(engine)
    
    async with session_pool() as session:
        users = await session.execute(
            select(User.id, User.faceit_nickname)
            .where(User.faceit_nickname.isnot(None))
        )
        users = users.all()
        
        faceit_service = FaceitService(
            session_pool=session_pool,
            api_keys=api_keys,
            cache_ttl=3600,
            maxsize=1000
        )

        logger.info(f"Найдено пользователей для обновления: {len(users)}")
        await faceit_service.initialize()
        
        update_count = 0
        nickname_update_count = 0
        
        for idx, user in enumerate(users):
            user_id, nickname = user
            logger.info(f"Обработка пользователя {idx+1}/{len(users)}: {nickname}")
            
            try:
                if idx % 5 == 0:
                    await asyncio.sleep(1)
                
                player_data = await faceit_service.get_player_stats(nickname)
                if not player_data:
                    continue
                    
                new_nickname = player_data.get('nickname')
                if new_nickname and new_nickname != nickname:
                    await session.execute(
                        update(User)
                        .where(User.id == user_id)
                        .values(faceit_nickname=new_nickname)
                    )
                    nickname_update_count += 1
                    logger.info(f"Обновлен никнейм: {nickname} -> {new_nickname}")
                
                elo = player_data.get('faceit_elo')
                if elo:
                    await session.execute(
                        update(UserState)
                        .where(UserState.user_id == user_id)
                        .values(elo=elo)
                    )
                    update_count += 1
                    logger.info(f"Обновлен ELO для {nickname}: {elo}")
                    
            except Exception as e:
                logger.error(f"Ошибка обновления ELO для {nickname}: {e}")
                await session.rollback()
        
        await session.commit()
        logger.info(f"Успешно обновлено: {update_count} ELO, {nickname_update_count} ников")
        await faceit_service.close()

@app.task(bind=True, max_retries=3)
def update_user_ages(self):
    """Обновление возраста пользователей"""
    loop = setup_async_environment()
    engine = create_async_engine_with_config()
    session_pool = create_sessionmaker(engine)
    
    async def inner():
        async with session_pool() as session:
            today = datetime.utcnow()
            day, month = today.day, today.month
            
            await session.execute(
                update(User)
                .where(
                    extract('month', User.created_at) == month,
                    extract('day', User.created_at) == day,
                    User.age.isnot(None)
                )
                .values(age=User.age + 1)
            )
            await session.commit()
    
    try:
        loop.run_until_complete(inner())
    except RedisConnectionError as e:
        logger.error(f"Ошибка подключения к Redis: {e}")
        self.retry(exc=e, countdown=60)
    except Exception as e:
        logger.error(f"Неожиданная ошибка: {e}")
    finally:
        try:
            loop.run_until_complete(engine.dispose())
        except Exception as e:
            logger.error(f"Ошибка при закрытии соединения: {e}")
        finally:
            loop.close()