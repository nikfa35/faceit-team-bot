from database.models import User, UserState, UserRating, BanList, UserSettings
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy import not_, select, func, text, update, or_, outerjoin, cast, BigInteger
from sqlalchemy.orm import joinedload
from aiogram import Bot 
import logging
import random

logger = logging.getLogger(__name__)


TIMEZONE_RANGES = {
    'MSK-1 (UTC+2)': ['MSK-1 (UTC+2)', 'MSK+0 (UTC+3)', 'MSK+1 (UTC+4)', 'MSK+2 (UTC+5)', 'MSK+3 (UTC+6)'],
    'MSK+0 (UTC+3)': ['MSK-1 (UTC+2)', 'MSK+0 (UTC+3)', 'MSK+1 (UTC+4)', 'MSK+2 (UTC+5)', 'MSK+3 (UTC+6)'],
    'MSK+1 (UTC+4)': ['MSK-1 (UTC+2)', 'MSK+0 (UTC+3)', 'MSK+1 (UTC+4)', 'MSK+2 (UTC+5)', 'MSK+3 (UTC+6)'],
    'MSK+2 (UTC+5)': ['MSK+0 (UTC+3)', 'MSK+1 (UTC+4)', 'MSK+2 (UTC+5)', 'MSK+3 (UTC+6)', 'MSK+4 (UTC+7)'],
    'MSK+3 (UTC+6)': ['MSK+1 (UTC+4)', 'MSK+2 (UTC+5)', 'MSK+3 (UTC+6)', 'MSK+4 (UTC+7)', 'MSK+5 (UTC+8)'],
    'MSK+4 (UTC+7)': ['MSK+2 (UTC+5)', 'MSK+3 (UTC+6)', 'MSK+4 (UTC+7)', 'MSK+5 (UTC+8)', 'MSK+6 (UTC+9)'],
    'MSK+5 (UTC+8)': ['MSK+3 (UTC+6)', 'MSK+4 (UTC+7)', 'MSK+5 (UTC+8)', 'MSK+6 (UTC+9)', 'MSK+7 (UTC+10)'],
    'MSK+6 (UTC+9)': ['MSK+4 (UTC+7)', 'MSK+5 (UTC+8)', 'MSK+6 (UTC+9)', 'MSK+7 (UTC+10)', 'MSK+8 (UTC+11)'],
    'MSK+7 (UTC+10)': ['MSK+5 (UTC+8)', 'MSK+6 (UTC+9)', 'MSK+7 (UTC+10)', 'MSK+8 (UTC+11)', 'MSK+9 (UTC+12)'],
    'MSK+8 (UTC+11)': ['MSK+6 (UTC+9)', 'MSK+7 (UTC+10)', 'MSK+8 (UTC+11)', 'MSK+9 (UTC+12)', 'MSK+10 (UTC+13)'],
    'MSK+9 (UTC+12)': ['MSK+7 (UTC+10)', 'MSK+8 (UTC+11)', 'MSK+9 (UTC+12)', 'MSK+10 (UTC+13)'],
    'MSK+10 (UTC+13)': ['MSK+8 (UTC+11)', 'MSK+9 (UTC+12)', 'MSK+10 (UTC+13)']
}


async def search_teammates(
    session: AsyncSession,
    current_user_tg_id: int,
    ban_list: list = None,
    elo_range: int = None,
    limit: int = 100,  # Увеличено для большего разнообразия
    exclude_ids: list = None  # Новый параметр для исключения уже показанных игроков
):
    try:
        # Получаем данные текущего пользователя с JOIN состояния и настроек
        current_user = await session.execute(
            select(User)
            .options(
                joinedload(User.state),
                joinedload(User.settings)
            )
            .where(User.tg_id == cast(current_user_tg_id, BigInteger))
        )
        current_user = current_user.scalars().unique().first()

        if not current_user or not current_user.state:
            logger.error("Текущий пользователь или его состояние не найдены")
            return []

        # Получаем настройки пользователя
        settings = current_user.settings or UserSettings(
            user_id=current_user.id,
            min_age=12,
            max_age=60,
            elo_range=300
        )

        if elo_range is None:
            elo_range = settings.elo_range
            
        # Получаем бан-лист
        ban_list_query = await session.execute(
            select(BanList.banned_nickname)
            .where(BanList.user_id == current_user.id)
        )
        ban_list = [nickname.lower().strip() for nickname in ban_list_query.scalars().all()]

        # Получаем часовой пояс текущего пользователя
        current_timezone = current_user.state.timezone
        allowed_timezones = TIMEZONE_RANGES.get(current_timezone, [])

        # Основной запрос с учетом настроек
        query = (
            select(User, UserState, UserSettings)  # Выбираем три сущности
            .join(UserState, User.id == UserState.user_id)
            .outerjoin(UserSettings, User.id == UserSettings.user_id)  # Outer join для настроек
            .options(
                joinedload(User.state),
                joinedload(User.settings)  # Явная загрузка отношений
            )
            .where(
                User.id != current_user.id,
                UserState.search_team == True,
                UserState.elo.is_not(None),
                User.faceit_nickname.is_not(None),
                User.age.between(settings.min_age, settings.max_age),
                UserState.is_verified.is_not(None),
                not_(UserRating.is_banned),
                UserState.elo.between(
                    current_user.state.elo - settings.elo_range,
                    current_user.state.elo + settings.elo_range
                )
            )
            .order_by(func.random())  # Добавлен случайный порядок
            .limit(limit)
        )
        
        # Исключаем уже показанных игроков
        if exclude_ids:
            query = query.where(User.id.notin_(exclude_ids))
        
        # Фильтр по часовым поясам
        if allowed_timezones:
            query = query.where(UserState.timezone.in_(allowed_timezones))
            
        # Фильтр по бан-листу
        if ban_list:
            query = query.where(
                not_(func.lower(User.faceit_nickname).in_(ban_list))
                if ban_list 
                else True
            ) 
              
        result = await session.execute(query)
        teammates_data = result.all()
        logger.info(f"Найдено {len(teammates_data)} потенциальных тиммейтов")
        
        # Новая улучшенная логика фильтрации по ролям
        filtered_teammates = []
        unique_roles = ["in-Game Leader (IGL)", "AWPer", "Support/Lurker", "Entry Fragger"]
        current_role = current_user.state.role
        
        # Перемешиваем результаты перед обработкой
        random.shuffle(teammates_data)
        
        # Определяем нужные роли в зависимости от роли текущего пользователя
        if current_role and (current_role in unique_roles or current_role == "Опорник"):
            if current_role in unique_roles:
                # Для уникальных ролей: нужны все остальные уникальные + опорник
                needed_roles = set(unique_roles) - {current_role}
                needed_roles.add("Опорник")
            else:  # current_role == "Опорник"
                # Для опорника: нужны все уникальные роли
                needed_roles = set(unique_roles)
            
            added_roles = set()
            
            for teammate in teammates_data:
                _, state, _ = teammate
                role = state.role
                
                if role and role in needed_roles and role not in added_roles:
                    filtered_teammates.append(teammate)
                    added_roles.add(role)
                    
                    # Останавливаемся когда набрали 4 игрока
                    if len(filtered_teammates) == 4:
                        break
            
            # Если не набрали нужное количество по ролям, добавляем случайных игроков
            if len(filtered_teammates) < 4:
                for teammate in teammates_data:
                    if teammate not in filtered_teammates:
                        filtered_teammates.append(teammate)
                        if len(filtered_teammates) == 4:
                            break
        else:
            # Для пользователей без роли или с неизвестной ролью
            # Берем первых 4 игроков из перемешанного списка
            filtered_teammates = teammates_data[:4]
        
        return filtered_teammates
    
    except Exception as e:
        # Обязательный откат при ошибках
        await session.rollback()
        logger.error(f'Ошибка при поиске тиммейтов: {e}', exc_info=True)
        return []

async def add_to_ban_list(
    session: AsyncSession, 
    user_id: int, 
    nickname: str,
    reason: str = "Добавлено пользователем"
):
    """Добавление игрока в бан-лист пользователя"""
    ban = BanList(
        user_id=user_id,
        banned_nickname=nickname,
        reason=reason
    )
    session.add(ban)
    await session.commit()

async def remove_blocked_users(session: AsyncSession, bot: Bot):
    """Удаляет пользователей, заблокировавших бота"""
    users = await session.scalars(select(User))
    for user in users:
        try:
            # Проверяем статус бота у пользователя
            await bot.send_chat_action(chat_id=user.tg_id, action="typing")
        except Exception as e:
            if "bot was blocked" in str(e).lower():
                logger.info(f"Удаляем заблокированного пользователя: {user.tg_id}")
                await session.delete(user)
    await session.commit()