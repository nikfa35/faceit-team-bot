from aiogram import BaseMiddleware
from typing import Callable, Awaitable, Any, Dict
from aiogram.types import TelegramObject, Update
from sqlalchemy.ext.asyncio import AsyncSession
from services.faceit import FaceitService
import logging

logger = logging.getLogger(__name__)


class DbSessionMiddleware(BaseMiddleware):
    def __init__(self, session_pool):
        self.session_pool = session_pool

    async def __call__(self, handler, event, data):
        async with self.session_pool() as session:
            data["session"] = session
            return await handler(event, data)

class ErrorHandlingMiddleware(BaseMiddleware):
    async def __call__(self, handler, event, data):
        try:
            return await handler(event, data)
        except Exception as e:
            logger.error(f"Unhandled exception: {e}", exc_info=True)
            bot = data.get('bot')
            if bot and hasattr(event, 'message'):
                await event.message.answer("⚠️ Произошла ошибка. Попробуйте позже.")
            return None

class ServiceMiddleware(BaseMiddleware):
    """
    Middleware для инъекции сервиса Faceit в обработчики
    """
    def __init__(self, faceit_service: FaceitService):
        self.faceit_service = faceit_service

    async def __call__(
        self,
        handler: Callable[[TelegramObject, Dict[str, Any]], Awaitable[Any]],
        event: TelegramObject,
        data: Dict[str, Any]
    ) -> Any:
        data["faceit_service"] = self.faceit_service
        return await handler(event, data)

class LoggingMiddleware(BaseMiddleware):
    """
    Middleware для логирования входящих обновлений
    """
    async def __call__(
        self,
        handler: Callable[[Update, Dict[str, Any]], Awaitable[Any]],
        event: Update,
        data: Dict[str, Any]
    ) -> Any:
        logger.info(f"Received update: {event.update_id} | Type: {event.event_type}")
        
        # Пробрасываем обновление дальше по цепочке middleware
        result = await handler(event, data)
        
        logger.info(f"Finished processing update: {event.update_id}")
        return result