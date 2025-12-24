import asyncio
import logging
import uuid
import app.keyboards as kb
import database.requests as rq
import httpx
import time
import json

from apscheduler.schedulers.asyncio import AsyncIOScheduler
from database.requests import TIMEZONE_RANGES 
from sqlalchemy.orm import selectinload, joinedload
from requests import session
from fastapi import Depends
from aiogram.methods import SendInvoice
from aiogram.types import LabeledPrice, PreCheckoutQuery, SuccessfulPayment
from aiogram.enums import ParseMode
from services.payment import create_yoomoney_payment
from database.base import create_async_engine_with_config, create_sessionmaker
from typing import AsyncGenerator, Union
from aiogram.utils.keyboard import InlineKeyboardBuilder
from aiogram import F, Router, types, Bot
from aiogram.filters import Command, or_f
from aiogram.types import Message, CallbackQuery, ReplyKeyboardRemove
from aiogram.fsm.state import State, StatesGroup
from aiogram.fsm.context import FSMContext
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import desc, distinct, select, func, text, cast, BigInteger, outerjoin, update
from database.models import APIServiceStats, User, UserState, UserReport, UserRating, Appeal, Payment, UserError, BanList, UserReputation, UserSettings, UserActivity
from services.faceit import FaceitService
from datetime import datetime, timedelta
from config import (
    YOOMONEY_PROVIDER_TOKEN, 
    VIP_PRICES,
    PAYMENT_CURRENCY,
    PAYMENT_PROVIDER_DATA,
    ADMINS
)
from aiogram.types import InlineKeyboardMarkup, InlineKeyboardButton
from config import ADMINS

last_invites = {}
logger = logging.getLogger(__name__)
router = Router()
scheduler = AsyncIOScheduler()

async_session_maker = None  # Будет установлен при инициализации

def setup_scheduler(session_maker):
    """Функция для инициализации сессии в этом модуле"""
    global async_session_maker
    async_session_maker = session_maker
    logger.info("Инициализирован планировщик очистки")

class AdminStates(StatesGroup):
    waiting_for_broadcast_message = State()
    waiting_for_user_message = State()

class Register(StatesGroup):
    faceit_nickname = State()
    age = State()

class ReportStates(StatesGroup):
    waiting_for_nickname = State()
    waiting_for_reason = State()

class RatePlayerStates(StatesGroup):
    waiting_for_nickname = State()
    waiting_for_rating = State()

class ErrorStates(StatesGroup):
    waiting_for_error_description = State()

class SettingsStates(StatesGroup):
    waiting_for_ban_nickname = State()

class AppealStates(StatesGroup):
    waiting_for_date = State()
    waiting_for_description = State()

class UnifiedRatingStates(StatesGroup):
    waiting_for_nickname = State()
    waiting_for_action = State()
    waiting_for_report_reason = State()
    waiting_for_praise_reason = State()

class ConsentStates(StatesGroup):
    waiting_for_consent = State()

MAIN_MENU_COMMANDS = [
    '🔍 Поиск тиммейтов', 
    '⭐️ Оценить игрока', 
    '📊 Мои данные',
    '⚙️ Настройки профиля', 
    '💎 VIP возможности', 
    '❓ Сообщить об ошибке',
    'ℹ️ О нас', 
    '🔒 Бан-лист', 
    '📊 Диапазон ELO',
    'Главное меню']


def get_reason_text(reason: int) -> str:
    reasons = {
        1: "Оскорбительное общение/поведение",
        2: "Грифинг (намеренное вредительство команде)",
        3: "Смурф"
    }
    return reasons.get(reason, "Неизвестная причина")

def is_profile_complete(user: User, user_state: UserState) -> bool:
    """Проверяет, заполнены ли все обязательные настройки профиля"""
    return all([
        user.faceit_nickname is not None,
        user.age is not None,
        user_state.is_verified is not None,
        user_state.role is not None,
        user_state.search_team is not None,
        user_state.communication_method is not None,
        user_state.timezone is not None
    ])

async def delete_unfinished_users(session: AsyncSession):
    """Удаляет пользователей без faceit_nickname старше 1 дня."""
    try:
        # Находим ID "незавершённых" пользователей
        stmt = (
            delete(User)
            .where(
                and_(
                    User.faceit_nickname.is_(None),
                    User.created_at < datetime.utcnow() - timedelta(days=1)
                )
            )
            .returning(User.id)
        )
        
        result = await session.execute(stmt)
        deleted_ids = result.scalars().all()
        
        if deleted_ids:
            logger.info(f"Удалено незавершённых пользователей: {len(deleted_ids)}")
        
        await session.commit()
        return deleted_ids
        
    except Exception as e:
        await session.rollback()
        logger.error(f"Ошибка при очистке пользователей: {e}", exc_info=True)
        return []

async def cleanup_inactive_users(session_pool, bot: Bot, days=180):
    while True:
        await asyncio.sleep(86400)  # Проверка раз в сутки
        try:
            async with session_pool() as session:
                inactive_users = await session.scalars(
                    select(User)
                    .where(User.last_activity < datetime.utcnow() - timedelta(days=days))
                )
                
                for user in inactive_users:
                    await delete_user_completely(session, user.id)
                
                await session.commit()
        except Exception as e:
            logger.error(f"Ошибка очистки неактивных пользователей: {e}")

async def check_blocked_users(session_pool, bot: Bot):
    while True:
        await asyncio.sleep(86400)  # Проверка раз в сутки
        try:
            async with session_pool() as session:
                # Получаем всех пользователей
                users = await session.scalars(select(User))
                
                for user in users:
                    try:
                        # Пытаемся отправить тестовое сообщение
                        await bot.send_message(
                            chat_id=user.tg_id,
                            text="Это тестовое сообщение для проверки блокировки"
                        )
                    except Exception as e:
                        if "bot was blocked" in str(e).lower():
                            # Удаляем пользователя
                            await delete_user_completely(session, user.id)
                            logger.info(f"Пользователь {user.tg_id} заблокировал бота и был удален")
                
                await session.commit()
        except Exception as e:
            logger.error(f"Ошибка проверки заблокированных пользователей: {e}")

async def delete_user_completely(session: AsyncSession, user_id: int):
    """Полное удаление пользователя и всех связанных данных"""
    try:
        # Удаляем все связанные записи в правильном порядке
        await session.execute(delete(UserActivity).where(UserActivity.user_id == user_id))
        await session.execute(delete(UserRating).where(UserRating.user_id == user_id))
        await session.execute(delete(UserState).where(UserState.user_id == user_id))
        
        # Удаляем самого пользователя
        await session.execute(delete(User).where(User.id == user_id))
        await session.commit()
        return True
    except Exception as e:
        await session.rollback()
        logger.error(f"Ошибка при полном удалении пользователя {user_id}: {e}")
        return False

async def get_user_by_tg_id(session: AsyncSession, tg_id: int) -> User | None:
    """Поиск пользователя по Telegram ID с загрузкой состояния и настроек"""
    try:
        result = await session.execute(
            select(User)
            .options(
                joinedload(User.state),  # Явная загрузка состояния
                joinedload(User.settings)
            )
            .where(User.tg_id == tg_id)
        )
        return result.unique().scalar_one_or_none()
    except Exception as e:
        logger.error(f"Ошибка при поиске пользователя: {e}")
        return None

async def check_vip_expirations(session_pool, bot: Bot):
    while True:
        await asyncio.sleep(86400)  # Проверка раз в сутки
        try:
            async with session_pool() as session:
                # Находим пользователей с истекшей подпиской
                expired_users = await session.scalars(
                    select(User)
                    .where(User.is_vip == True)
                    .where(User.vip_expires_at < datetime.utcnow())
                )
                
                for user in expired_users:
                    user.is_vip = False
                    user.vip_expires_at = None  # Явно сбрасываем дату истечения
                    session.add(user)
                    try:
                        # Уведомляем пользователя
                        await bot.send_message(
                            chat_id=user.tg_id,
                            text="⚠️ Ваша VIP подписка истекла. Для продления используйте меню VIP."
                        )
                    except Exception as e:
                        if "bot was blocked" in str(e).lower():
                            # Удаляем пользователя, если он заблокировал бота
                            await delete_user_completely(session, user.id)
                        else:
                            logger.error(f"Не удалось уведомить пользователя: {e}")
                
                await session.commit()
        except Exception as e:
            logger.error(f"Ошибка проверки VIP подписок: {e}")

async def activate_vip_subscription(
    session: AsyncSession,
    user_id: int, 
    sub_type: str
):
    try:
        logger.info(f"Активация VIP для user_id={user_id}, тип={sub_type}")
        
        # Загружаем пользователя вместе с настройками
        result = await session.execute(
            select(User)
            .options(joinedload(User.settings))  # Явная загрузка настроек
            .where(User.id == user_id)
        )
        user = result.scalars().unique().first()
        
        if not user:
            logger.error(f"Пользователь {user_id} не найден")
            return False
            
        # Рассчитываем дату окончания
        now = datetime.utcnow()
        if sub_type == "month":
            expires_at = now + timedelta(days=30)
        elif sub_type == "3month":
            expires_at = now + timedelta(days=90)
        elif sub_type == "year":
            expires_at = now + timedelta(days=365)
        elif sub_type == "permanent":
            expires_at = None
        else:
            logger.error(f"Неизвестный тип подписки: {sub_type}")
            return False
        
        logger.info(f"Установка VIP: expires_at={expires_at}")
        # Обновляем данные пользователя
        user.is_vip = True
        user.vip_expires_at = expires_at
        
        # Создаем настройки, если их нет
        if not user.settings:
            logger.info("Создание настроек для пользователя")
            user.settings = UserSettings(
                user_id=user.id, 
                elo_range=300,
                min_age=12,
                max_age=60,
                notifications=True
            )
            session.add(user.settings)
        else:
            logger.info("Настройки уже существуют")
        
        await session.commit()
        logger.info("VIP активирован успешно")
        return True
        
    except Exception as e:
        logger.error(f"Ошибка активации VIP: {e}", exc_info=True)
        await session.rollback()
        return False

async def notify_admin(error_text: str, user_tg_id: int, bot: Bot):
    admin_id = ADMINS
    try:
        await bot.send_message(
            chat_id=admin_id,
            text=f"⚠️ Новое сообщение об ошибке:\n\n"
                f"От пользователя: {user_tg_id}\n"
                f"Текст: {error_text}"
        )
    except Exception as e:
        await session.rollback() 
        logger.error(f"Ошибка при уведомлении админа: {e}")

async def add_to_ban_list(session: AsyncSession, user_id: int, nickname: str):
    ban = BanList(
        user_id=user_id,
        banned_nickname=nickname,
        reason="Добавлено пользователем"
    )
    session.add(ban)
    await session.commit()

async def handle_unified_cancel(callback: CallbackQuery, state: FSMContext, session: AsyncSession):
    try:
        await state.clear()
        user = await get_user_by_tg_id(session, callback.from_user.id)
        is_vip = user.is_vip if user else False
        
        await callback.message.edit_text(
            "Оценка игрока отменена",
            reply_markup=None
        )
        
        await callback.message.answer(
            "Выберите действие:",
            reply_markup=kb.get_main_keyboard(is_vip))
    except Exception as e:
        await session.rollback() 
        logger.error(f"Ошибка при отмене оценки: {e}")
    finally:
        await callback.answer()

async def get_session() -> AsyncGenerator[AsyncSession, None]:
    engine = create_async_engine_with_config()
    session_factory = create_sessionmaker(engine)
    async with session_factory() as session:
        yield session

async def show_api_stats(target: Union[Message, CallbackQuery], faceit_service: FaceitService):
    stats = faceit_service.get_stats()
    
    # Форматируем статистику ключей с защитой от ошибок
    key_stats = stats.get('key_stats', [])
    key_text = "\n".join(
        f"  - {stat.get('key', 'N/A')}: "
        f"запросы={stat.get('requests', 0)}, "
        f"ошибки={stat.get('errors', 0)}, "
        f"последнее использование={stat.get('last_used', 'N/A')}"
        for stat in key_stats
    ) if key_stats else "  Нет данных о ключах"
    
    response = (
        "📊 Статистика Faceit API:\n\n"
        f"• Всего запросов: {stats.get('total_requests', 0)}\n"
        f"• Ошибок: {stats.get('error_count', 0)}\n"
        f"• Ключей API: {stats.get('api_keys', 0)}\n"
        f"• Размер кеша: {stats.get('cache_size', 0)}\n"
        f"• Попаданий в кеш: {stats.get('cache_hits', 0)}\n"
        f"• Промахов кеша: {stats.get('cache_misses', 0)}\n"
        f"• Процент попаданий: {stats.get('cache_hit_rate', 0.0):.2%}\n"
        f"• Запросов за час: {stats.get('requests_last_hour', 0)}\n"
        f"• Среднее время ответа: {stats.get('avg_response_time', 0.0):.2f} сек\n\n"
        "📈 Статистика по ключам:\n"
        f"{key_text}"
    )
    
    builder = InlineKeyboardBuilder()
    builder.button(text="🔄 Обновить", callback_data="refresh_api_stats")
    builder.button(text="🧹 Очистить кеш", callback_data="clear_api_cache")
    
    # Для сообщений используем answer, для callback - edit_text
    if isinstance(target, Message):
        await target.answer(response, reply_markup=builder.as_markup())
    else:
        await target.message.edit_text(response, reply_markup=builder.as_markup())        

async def track_activity(session: AsyncSession, user_id: int, activity_type: str):
    try:
        # Находим пользователя в БД
        user = await session.scalar(select(User).where(User.tg_id == user_id))
        if user:
            # Создаем запись активности
            activity = UserActivity(
                user_id=user.id,
                activity_type=activity_type
            )
            session.add(activity)
            await session.commit()
    except Exception as e:
        logger.error(f"Ошибка записи активности: {e}")

@router.message(
    UnifiedRatingStates.waiting_for_nickname,
    F.text.in_(MAIN_MENU_COMMANDS)
)
@router.message(
    ReportStates.waiting_for_nickname,
    F.text.in_(MAIN_MENU_COMMANDS))
@router.message(
    SettingsStates.waiting_for_ban_nickname,
    F.text.in_(MAIN_MENU_COMMANDS))

async def cancel_state_on_main_menu(message: Message, state: FSMContext, session: AsyncSession):
    try:
        user = await get_user_by_tg_id(session, message.from_user.id)
        is_vip = user.is_vip if user else False
        await state.clear()
        await message.answer(
            "❌ Предыдущее действие отменено",
            reply_markup=kb.get_main_keyboard(is_vip))
        
        handler_map = {
            '🔍 Поиск тиммейтов': player_search,
            '⭐️ Оценить игрока': start_unified_rating,
            '📊 Мои данные': handle_my_data,
            '⚙️ Настройки профиля': handle_profile_settings,
            '💎 VIP возможности': show_vip_features,
            '❓ Сообщить об ошибке': report_error_start,
            'ℹ️ О нас': about_us,
            '🔒 Бан-лист': ban_list_menu,
            '📊 Диапазон ELO': handle_elo_range,
            'Главное меню': main_menu,
        }
        
        if message.text in handler_map:
            if message.text in ['🔍 Поиск тиммейтов', '📊 Мои данные', '⚙️ Настройки профиля',
                               '🔒 Бан-лист', '📊 Диапазон ELO']:
                await handler_map[message.text](message, session)
            else:
                await handler_map[message.text](message)
    
    except Exception as e:
        await session.rollback() 
        logger.error(f"Ошибка при обработке команды меню: {e}", exc_info=True)
        await message.answer(
            "Произошла ошибка при обработке команды",
            reply_markup=kb.get_main_keyboard()
        )

async def get_user_by_faceit_nickname(session: AsyncSession, nickname: str) -> User | None:
    """Поиск пользователя по никнейму с проверкой актуальности"""
    result = await session.execute(
        select(User)
        .where(func.lower(User.faceit_nickname) == nickname.lower())
    )
    user = result.scalars().first()
    
    if not user:
        # Пробуем найти через Faceit API
        player_data = await faceit_service.get_player_stats(nickname)
        if player_data and 'player_id' in player_data:
            result = await session.execute(
                select(User)
                .where(User.faceit_player_id == player_data['player_id'])
            )
            user = result.scalars().first()
            
            # Обновляем никнейм если нашли
            if user:
                await session.execute(
                    update(User)
                    .where(User.id == user.id)
                    .values(faceit_nickname=player_data['nickname'])
                )
    
    return user

async def update_user_activity(session: AsyncSession, user_id: int, activity_type: str):
    try:
        # Находим пользователя с состоянием
        user = await session.scalar(
            select(User)
            .options(joinedload(User.state))
            .where(User.tg_id == user_id))
        
        if user:
            new_activity = UserActivity(
                user_id=user.id,
                activity_type=activity_type
            )
            session.add(new_activity)
            await session.commit()
    except Exception as e:
        logger.error(f"Ошибка записи активности: {e}")

@router.message(Command('start'))
async def start_registration(message: Message, state: FSMContext, session: AsyncSession, faceit_service: FaceitService):
    user = await get_user_by_tg_id(session, message.from_user.id)
    
    if user:
        if user.consent_accepted:
            if user.faceit_nickname:
                # Только здесь записываем активность для существующего пользователя
                await update_user_activity(session, user.id, "start")
                reply_text = 'Вы уже зарегистрированы!\n'
                if user.is_vip:
                    reply_text += 'У вас VIP 💎 подписка!\n\n'
                await message.answer(
                    reply_text + 'Используйте меню для навигации.',
                    reply_markup=kb.get_main_keyboard(user.is_vip))
            else:
                await message.answer(
                    'Для регистрации введите ваш Faceit никнейм:',
                    reply_markup=kb.cancel_registration()
                )
                await state.set_state(Register.faceit_nickname)
        else:
            await show_consent_agreement(message, state)
    else:
        await show_consent_agreement(message, state)

async def show_consent_agreement(message: Message, state: FSMContext):
    await message.answer(
        "Привет! 👋 \n\n"
        "Желаем тебе отличных тиммейтов и незабываемых каток! 😉\n\n"
        "P.S. Мы только набираем обороты, поэтому игроков в поиске очень мало. Если тебе никто не попался, то значит мы еще собираем таких, как ты!\n"
        "Итоги розыгрышей будут опубликованы в нашем тг канале, ссылку ты увидишь дальше. 🤝",
        reply_markup=kb.consent_keyboard()
    )
    await state.set_state(ConsentStates.waiting_for_consent)

@router.callback_query(ConsentStates.waiting_for_consent, F.data == "consent_accept")
async def accept_consent(callback: CallbackQuery, state: FSMContext, session: AsyncSession):
    user = await get_user_by_tg_id(session, callback.from_user.id)
    
    if not user:
        user = User(
            tg_id=callback.from_user.id,
            tg_username=callback.from_user.username,
            consent_accepted=True
        )
        session.add(user)
        await session.commit()  # Фиксируем, чтобы получить ID
        await session.refresh(user)  # Обновляем объект
    else:
        user.consent_accepted = True
        await session.commit()
    
    # Теперь записываем активность после создания пользователя
    await update_user_activity(session, user.id, "accept_consent")
    
    await callback.message.edit_text(
        "✅ Согласие на обработку данных подтверждено!",
        reply_markup=None
    )
    
    await callback.message.answer(
        "Для регистрации введите ваш Faceit никнейм:",
        reply_markup=kb.cancel_registration()
    )
    await state.set_state(Register.faceit_nickname)
    await callback.answer()

@router.callback_query(ConsentStates.waiting_for_consent, F.data == "consent_reject")
async def reject_consent(callback: CallbackQuery, state: FSMContext):
    await state.clear()
    await callback.message.edit_text(
        "❌ Вы отклонили соглашение. Бот не может быть использован без вашего согласия.",
        reply_markup=None
    )
    await callback.answer()

@router.message(Register.faceit_nickname)
async def process_faceit_nickname(message: Message, state: FSMContext, session: AsyncSession, faceit_service: FaceitService):
    faceit_nickname = message.text.strip()
    
    # Проверяем отмену регистрации
    if faceit_nickname == "❌ Отменить регистрацию":
        await state.clear()
        await message.answer("Регистрация отменена", reply_markup=types.ReplyKeyboardRemove())
        return
    
    try:
        # Проверяем существование аккаунта Faceit
        if not await faceit_service.check_account_exists(faceit_nickname):
            await message.answer(
                "Аккаунт Faceit с таким никнеймом не найден. Пожалуйста, введите корректный никнейм:",
                reply_markup=kb.cancel_registration()
            )
            return
        
        # Проверяем, не зарегистрирован ли уже этот никнейм
        existing_user = await session.execute(
            select(User).where(func.lower(User.faceit_nickname) == faceit_nickname.lower())
        )
        if existing_user.scalar():
            await message.answer(
                "Этот Faceit аккаунт уже зарегистрирован. Пожалуйста, используйте другой никнейм.",
                reply_markup=kb.cancel_registration()
            )
            return
        
        # Получаем данные игрока
        player_data = await faceit_service.get_player_stats(faceit_nickname)
        if not player_data:
            raise ValueError("Не удалось получить данные игрока")
        
        # Сохраняем ВСЕ необходимые данные в состоянии
        await state.update_data(
            faceit_nickname=faceit_nickname,
            faceit_player_id=player_data.get('player_id'),
            faceit_elo=player_data.get('faceit_elo', 0)
        )
        
        # Переходим к следующему шагу
        await message.answer(
            "Отлично! Теперь введите ваш возраст:",
            reply_markup=kb.cancel_registration()
        )
        await state.set_state(Register.age)
        
    except Exception as e:
        logger.error(f"Ошибка при обработке никнейма: {e}", exc_info=True)
        await message.answer(
            "Произошла ошибка при проверке Faceit аккаунта. Пожалуйста, попробуйте позже.",
            reply_markup=kb.cancel_registration()
        )

@router.message(Register.age)
async def process_age(message: Message, state: FSMContext, session: AsyncSession, faceit_service: FaceitService):
    try:
        age_str = message.text.strip()
        
        # Проверка на отмену
        if age_str == "❌ Отменить регистрацию":
            await state.clear()
            await message.answer("Регистрация отменена", reply_markup=types.ReplyKeyboardRemove())
            return
            
        if not age_str.isdigit():
            await message.answer("Возраст должен быть числом. Попробуйте еще раз:")
            return
        
        age = int(age_str)
        if not 12 <= age <= 60:
            await message.answer("Возраст должен быть от 12 до 60 лет. Попробуйте еще раз:")
            return

        # Получаем ВСЕ данные из состояния
        user_data = await state.get_data()
        faceit_nickname = user_data.get('faceit_nickname')
        faceit_player_id = user_data.get('faceit_player_id')
        elo = user_data.get('faceit_elo', 0)
        
        if not faceit_nickname:
            raise ValueError("Не найден faceit_nickname в данных состояния")
            
        # Ищем существующего пользователя
        user = await session.scalar(
            select(User)
            .options(joinedload(User.settings))
            .where(User.tg_id == message.from_user.id)
        )
        
        if user:
            # Обновляем существующего пользователя
            user.faceit_nickname = faceit_nickname
            user.faceit_player_id = faceit_player_id
            user.age = age
            user.last_activity = datetime.utcnow()
            
            # Получаем или создаем состояние
            user_state = await session.scalar(
                select(UserState)
                .where(UserState.user_id == user.id)
            )
            
            if not user_state:
                user_state = UserState(
                    user_id=user.id,
                    elo=elo,
                    is_verified=None,
                    search_team=None,
                    communication_method="Не указан",
                    timezone="MSK+0 (UTC+3)"
                )
                session.add(user_state)
            else:
                user_state.elo = elo
            
            # Получаем или создаем рейтинг
            user_rating = await session.scalar(
                select(UserRating)
                .where(UserRating.user_id == user.id)
            )
            
            if not user_rating:
                user_rating = UserRating(
                    user_id=user.id,
                    faceit_nickname=faceit_nickname,
                    nickname_rating=50,
                    is_banned=False
                )
                session.add(user_rating)
            
            # Создаем настройки если их нет
            if not user.settings:
                user.settings = UserSettings(
                    user_id=user.id,
                    elo_range=300,
                    min_age=12,
                    max_age=60,
                    notifications=True
                )
                session.add(user.settings)
        else:
            # Создаем нового пользователя
            new_user = User(
                tg_id=message.from_user.id,
                faceit_nickname=faceit_nickname,
                faceit_player_id=faceit_player_id,
                age=age,
                is_vip=False,
                vip_expires_at=None,
                tg_username=message.from_user.username,
                invite_count=0,
                consent_accepted=True
            )
            session.add(new_user)
            await session.flush()
            
            # Создаем состояние
            user_state = UserState(
                user_id=new_user.id,
                elo=elo,
                is_verified=False,
                search_team=False,
                communication_method="Не указан",
                timezone="MSK+0 (UTC+3)"
            )
            session.add(user_state)
            
            # Создаем рейтинг
            new_rating = UserRating(
                user_id=new_user.id,
                faceit_nickname=faceit_nickname,
                nickname_rating=50,
                is_banned=False
            )
            session.add(new_rating)
            
            # Создаем настройки
            new_settings = UserSettings(
                user_id=new_user.id,
                elo_range=300,
                min_age=12,
                max_age=60,
                notifications=True
            )
            session.add(new_settings)
        
        # Фиксируем изменения в БД
        await session.commit()
        
        # Отправляем сообщение с настройками профиля
        await message.answer(
            f"✅ Регистрация завершена!\n\n"
            f"Ваше ELO: {elo}\n\n"
            f"Наш тг канал для розыгрышей и обсуждений: https://t.me/+ALI6nCGkpSgxNjgy\n\n"
            "🔥 Отличная новость! Ты можешь получить VIP-подписку на месяц БЕСПЛАТНО.\n Условия:\n"
            "1) Пригласи друга с faceit аккаунтом в наш бот.\n2) Оставь его точный тг никнейм в комментариях к первому посту в нашем Канале.\n"
            "3) Получи VIP-подписку.\n\n"
            "Пожалуйста, заполните обязательные настройки профиля:",
            reply_markup=kb.profile_settings(user_state)
        )
        await state.clear()

    except Exception as e:
        await session.rollback()
        logger.error(f"Ошибка регистрации: {e}", exc_info=True)
        await message.answer(
            "⚠️ Произошла ошибка. Попробуйте начать регистрацию заново с помощью /start",
            reply_markup=kb.get_main_keyboard()
        )
        await state.clear()

@router.message(F.text == "❌ Отменить регистрацию")
async def cancel_handler(message: Message, state: FSMContext):
    await state.clear()
    await message.answer("Регистрация отменена", reply_markup=types.ReplyKeyboardRemove())

@router.message(F.text == 'Главное меню')
async def main_menu(message: Message, session: AsyncSession):
    user = await get_user_by_tg_id(session, message.from_user.id)
    
    if not user:
        await message.answer(
            "Сначала зарегистрируйтесь с помощью /start",
            reply_markup=kb.get_default_main_keyboard()
        )
        return
    
    logging.info(f"User VIP status for {message.from_user.id}: is_vip={user.is_vip}, expires={user.vip_expires_at}")
    
    await message.answer(
        "Главное меню:",
        reply_markup=kb.get_main_keyboard(user.is_vip))
    

@router.message(F.text == '🔍 Поиск тиммейтов')
async def player_search(message: Message, session: AsyncSession):
    try:
        # Записываем активность
        await track_activity(session, message.from_user.id, "player_search")
        
        # Явно загружаем пользователя со всеми зависимостями
        logger.info(f"Загрузка пользователя {message.from_user.id} для поиска тиммейтов")
        result = await session.execute(
            select(User)
            .options(
                joinedload(User.state), 
                joinedload(User.settings),
                joinedload(User.bans)
            )
            .where(User.tg_id == cast(message.from_user.id, BigInteger))
        )
        user = result.unique().scalar_one_or_none()
        
        if not user:
            logger.warning(f"Пользователь {message.from_user.id} не найден")
            await message.answer("Сначала зарегистрируйтесь с помощью команды /start")
            return
        
        logger.info(f"Пользователь найден: ID={user.id}, ник={user.faceit_nickname}")
        
        # Запоминаем VIP статус для использования в случае ошибки
        is_vip = user.is_vip
        
        # Проверяем наличие состояния
        if not user.state:
            logger.warning(f"У пользователя {user.id} отсутствует состояние!")
            await message.answer(
                "❌ Ваш профиль не настроен. Пожалуйста, заполните настройки профиля.",
                reply_markup=kb.profile_settings(user.state)  # Передаем состояние (даже если None)
            )
            return
        
        logger.info(f"Состояние пользователя: " 
                    f"is_verified={user.state.is_verified}, "
                    f"role={user.state.role}, "
                    f"search_team={user.state.search_team}, "
                    f"communication_method={user.state.communication_method}, "
                    f"timezone={user.state.timezone}")
        
        # Проверяем заполненность профиля
        missing_fields = []
        
        if not user.faceit_nickname:
            logger.warning("Отсутствует faceit_nickname")
            missing_fields.append("Faceit никнейм")
        if not user.age:
            logger.warning("Отсутствует age")
            missing_fields.append("возраст")
        if user.state.is_verified is None:
            logger.warning("Отсутствует is_verified")
            missing_fields.append("статус верификации")
        if not user.state.role:
            logger.warning("Отсутствует role")
            missing_fields.append("роль в команде")
        if user.state.search_team is None:
            logger.warning("Отсутствует search_team")
            missing_fields.append("статус поиска команды")
        if not user.state.communication_method:
            logger.warning("Отсутствует communication_method")
            missing_fields.append("способ коммуникации")
        if not user.state.timezone:
            logger.warning("Отсутствует timezone")
            missing_fields.append("часовой пояс")
        
        if missing_fields:
            logger.warning(f"Незаполненные поля: {', '.join(missing_fields)}")
            await message.answer(
                "❌ Пожалуйста, заполните все настройки профиля перед поиском:\n\n"
                f"Не заполнено: {', '.join(missing_fields)}",
                reply_markup=kb.profile_settings(user.state)  # Передаем состояние
            )
            return

        # Сохраняем VIP статус для использования в случае ошибки
        is_vip = user.is_vip
        
        # Определяем текущие настройки поиска
        if user.is_vip and user.settings:
            min_age = user.settings.min_age
            max_age = user.settings.max_age
            elo_range = user.settings.elo_range
        else:
            min_age = 12
            max_age = 60
            elo_range = 300
            
        # Формируем информационное сообщение
        current_timezone = user.state.timezone
        allowed_timezones = TIMEZONE_RANGES.get(current_timezone, [])
        
        logger.info(f"Параметры поиска: "
                    f"min_age={min_age}, max_age={max_age}, "
                    f"elo_range={elo_range}, timezone={current_timezone}")
        
        await message.answer(
            f"⏳ Подбираем тиммейтов с параметрами:\n"
            f"• Возраст: от {min_age} до {max_age} лет\n"
            f"• Диапазон ELO: ±{elo_range}\n"
            f"• Часовой пояс: {current_timezone} (допустимые: {', '.join(allowed_timezones)})\n"
            "Пожалуйста, подождите...",
            parse_mode="HTML"
        )
        
        # Получаем бан-лист
        ban_list = [b.banned_nickname for b in user.bans] if user.bans else []
        logger.info(f"Бан-лист пользователя: {ban_list}")

        # Выполняем поиск
        teammates = await rq.search_teammates(
            session, 
            message.from_user.id,
            ban_list=ban_list,
            elo_range=elo_range
        )
    
        # Обработка случая, когда не найдено тиммейтов
        if not teammates:
            logger.info("Не найдено подходящих тиммейтов")
            await message.answer(
                "😕 Не найдено подходящих тиммейтов.\n\n"
                "Попробуйте позже или измените параметры поиска.",
                reply_markup=kb.search_results()
            )
            return
        
        logger.info(f"Найдено {len(teammates)} тиммейтов")
    
        # Формируем список ID найденных тиммейтов
        teammate_ids = [teammate.id for teammate, _, _ in teammates]
        
        # Получаем рейтинги тиммейтов
        ratings_result = await session.execute(
            select(UserRating.user_id, UserRating.nickname_rating)
            .where(UserRating.user_id.in_(teammate_ids)))
        ratings_dict = {user_id: rating for user_id, rating in ratings_result.all()}
        
        # Формируем сообщение с результатами
        response = ["🎮 Найдены потенциальные тиммейты:\n"]
        keyboard_buttons = []
        
        seen_ids = set()
        valid_count = 0
        
        for i, (teammate, teammate_state, _) in enumerate(teammates, 1):
            if teammate.id in seen_ids:
                continue
            seen_ids.add(teammate.id)
            valid_count += 1
            
            rating = ratings_dict.get(teammate.id, 10)
            
            response.append(
                f"\n{valid_count}. {'💎 ' if teammate.is_vip else ''}👤 <a href='https://www.faceit.com/ru/players/{teammate.faceit_nickname}'>{teammate.faceit_nickname}</a>\n"
                f"   🎂 Возраст: {teammate.age}\n"
                f"   ⚡️ ELO: {teammate_state.elo}\n"
                f"   🎮 Роль: {teammate_state.role or 'Не указана'}\n"
                f"   👍 Репутация: {rating}\n"
                f"   ✅ Верификация: {'Да' if teammate_state.is_verified else 'Нет'}\n"
                f"   🕒 Часовой пояс: {teammate_state.timezone}\n"
                f"   💬 Способ связи: {teammate_state.communication_method}\n"
            )
            
            keyboard_buttons.append([
                InlineKeyboardButton(
                    text=f"📨 Пригласить {teammate.faceit_nickname}",
                    callback_data=f"invite_single_{teammate.id}"
                )
            ])
        
        # Добавляем общие кнопки
        keyboard_buttons.append([
            InlineKeyboardButton(text='📨 Пригласить всех', callback_data='invite_all'),
            InlineKeyboardButton(text='🔄 Новый поиск', callback_data='new_search'),
        ])
        
        # Создаем клавиатуру
        markup = InlineKeyboardMarkup(inline_keyboard=keyboard_buttons)
        
        # Отправляем результаты
        await message.answer(
            "".join(response),
            reply_markup=markup,
            parse_mode="HTML",
            disable_web_page_preview=True
        )
        logger.info("Результаты поиска успешно отправлены")
        
    except Exception as e:
        logger.error(f"Критическая ошибка в player_search: {e}", exc_info=True)
        await session.rollback()
        
        # Используем сохраненный VIP статус
        await message.answer(
            "Произошла ошибка при поиске. Пожалуйста, попробуйте позже.",
            reply_markup=kb.get_main_keyboard(is_vip)
        )

@router.callback_query(F.data.startswith('invite_single_'))
async def handle_invite_single(callback: CallbackQuery, session: AsyncSession, bot: Bot):
    try:
        teammate_id = int(callback.data.split('_')[-1])
        user_id = callback.from_user.id
        
        current_time = time.time()
        last_time = last_invites.get(user_id, 0)
        
        if current_time - last_time < 600:
            await callback.answer("Вы можете отправлять приглашения раз в 10 минут", show_alert=True)
            return
        
        last_invites[user_id] = current_time
        
        # Явно загружаем отправителя со всеми необходимыми отношениями
        sender_result = await session.execute(
            select(User)
            .options(
                joinedload(User.state),  # Способ связи в состоянии!
                joinedload(User.settings)
            )
            .where(User.tg_id == callback.from_user.id)
        )
        sender = sender_result.scalars().unique().first()
        
        teammate = await session.get(User, teammate_id)
        
        if not sender or not teammate:
            await callback.answer("Ошибка: пользователь не найден", show_alert=True)
            return
        
        # Получаем рейтинг отправителя
        rating_result = await session.execute(
            select(UserRating.nickname_rating)
            .where(UserRating.user_id == sender.id)
        )
        rating = rating_result.scalar_one_or_none() or 50
        
        # Формируем текст приглашения - ИСПРАВЛЕН СПОСОБ КОММУНИКАЦИИ
        invite_text = (
            f"🎮 Вас приглашает игрок:\n\n"
            f"👤 <a href='https://www.faceit.com/ru/players/{sender.faceit_nickname}'>{sender.faceit_nickname}</a>\n"
            f"   🎂 Возраст: {sender.age}\n"
            f"   ⚡️ ELO: {sender.state.elo if sender.state else 'не указан'}\n"
            f"   ⭐️ Репутация: {rating}\n"
            f"   🎮 Роль: {sender.state.role if sender.state else 'не указана'}\n"
            f"   ✅ Верификация: {'Да' if sender.state and sender.state.is_verified else 'Нет'}\n"
            f"   🕒 Часовой пояс: {sender.state.timezone if sender.state else 'Не указан'}\n"
            f"   💬 Способ связи: {sender.state.communication_method if sender.state else 'Не указан'}\n"  # ИСПРАВЛЕНО!
            "Нажмите 'Принять', чтобы получить контактную информацию игрока"
        )
        
        try:
            # Отправляем приглашение
            await bot.send_message(
                chat_id=teammate.tg_id,
                text=invite_text,
                parse_mode="HTML",
                reply_markup=kb.invite_player_keyboard(sender.id)
            )
            logger.info(f"Приглашение отправлено {teammate.faceit_nickname}")

        except Exception as e:
            if "bot was blocked" in str(e).lower():
                await callback.answer(f"Пользователь {teammate.faceit_nickname} заблокировал бота", show_alert=True)
                logger.warning(f"Пользователь заблокировал бота: {teammate.tg_id}")
            else:
                logger.error(f"Ошибка отправки приглашения: {e}", exc_info=True)
                raise

        # Обновляем счетчик приглашений
        sender.invite_count += 1
        await session.commit()
        logger.info(f"Счетчик приглашений обновлен для {sender.faceit_nickname}")

        await callback.answer(f"Приглашение отправлено {teammate.faceit_nickname}")
        
    except Exception as e:
        # Обработка ошибок
        logger.error(f"Ошибка отправки индивидуального приглашения: {e}", exc_info=True)
        try:
            await session.rollback()
        except:
            pass
        
        await callback.answer("Произошла ошибка при отправке приглашения", show_alert=True)

@router.callback_query(F.data == 'new_search')
async def handle_new_search(callback: CallbackQuery, session: AsyncSession):
    try:
        await callback.answer()
        
        try:
            await callback.message.delete()
        except Exception as delete_error:
            logger.warning(f"Ошибка при удалении сообщения: {delete_error}")
        
        # Загружаем пользователя с явной загрузкой отношений
        result = await session.execute(
            select(User)
            .options(
                joinedload(User.state), 
                joinedload(User.settings),
                joinedload(User.bans)
            )
            .where(User.tg_id == callback.from_user.id)
        )
        user = result.unique().scalar_one_or_none()
        
        if not user or not user.state:
            await callback.message.answer(
                "Ошибка: профиль не найден",
                reply_markup=kb.get_main_keyboard()
            )
            return
        
        # Проверяем бан
        is_banned = await session.scalar(
            select(UserRating.is_banned)
            .where(UserRating.user_id == user.id)
        )
        if is_banned:
            await callback.message.answer(
                "❌ Вы забанены и не можете искать тиммейтов!",
                reply_markup=kb.get_main_keyboard()
            )
            return

        elo_range = user.settings.elo_range if (user.is_vip and user.settings) else 300
        
        ban_list = []
        if user.is_vip:
            bans = await session.scalars(
                select(BanList.banned_nickname)
                .where(BanList.user_id == user.id)
            )
            ban_list = [b.lower() for b in bans.all()]

        search_msg = await callback.message.answer("🔍 Идет поиск тиммейтов...")
        await asyncio.sleep(1)
        
        teammates = await rq.search_teammates(
            session,
            callback.from_user.id,
            elo_range=elo_range,
            ban_list=ban_list
        )

        await search_msg.delete()

        if not teammates:
            await callback.message.answer(
                "😕 Не найдено подходящих тиммейтов.\n\n"
                "Попробуйте позже или измените параметры поиска.",
                reply_markup=kb.search_results()
            )
            return

        response = ["🎮 Результаты нового поиска:\n"]
        keyboard_buttons = []
        seen_ids = set()
        valid_count = 0
        
        # ИСПРАВЛЕНА РАСПАКОВКА: теперь 3 элемента
        for teammate, teammate_state, _ in teammates:
            # Проверяем бан тиммейта
            is_banned = await session.scalar(
                select(UserRating.is_banned)
                .where(UserRating.user_id == teammate.id)
            )
            if is_banned:
                continue
                
            if user.is_vip and teammate.faceit_nickname.lower() in ban_list:
                continue
                
            valid_count += 1
            
            # Получаем рейтинг тиммейта
            rating = await session.scalar(
                select(UserRating.nickname_rating)
                .where(UserRating.user_id == teammate.id)
            )
            rating_value = rating or 10
            
            response.append(
                f"\n{valid_count}. {'💎 ' if teammate.is_vip else ''}👤 <a href='https://www.faceit.com/ru/players/{teammate.faceit_nickname}'>{teammate.faceit_nickname}</a>\n"
                f"   🎂 Возраст: {teammate.age}\n"
                f"   ⚡️ ELO: {teammate_state.elo}\n"
                f"   🎮 Роль: {teammate_state.role or 'Не указана'}\n"
                f"   💬 Способ связи: {teammate_state.communication_method}\n"
                f"   ⏰ Часовой пояс: {teammate_state.timezone}\n"
                f"   👍 Репутация: {rating_value}\n"
                f"   ✅ Верификация: {'Да' if teammate_state.is_verified else 'Нет'}\n"
            )

            keyboard_buttons.append([
                InlineKeyboardButton(
                    text=f"📨 Пригласить {teammate.faceit_nickname}",
                    callback_data=f"invite_single_{teammate.id}"
                )
            ])

        if valid_count == 0:
            await callback.message.answer(
                "😕 Не найдено подходящих тиммейтов в новом поиске.",
                reply_markup=kb.search_results()
            )
            return

        keyboard_buttons.append([
            InlineKeyboardButton(text='📨 Пригласить всех', callback_data='invite_all'),
            InlineKeyboardButton(text='🔄 Новый поиск', callback_data='new_search')
        ])
        
        markup = InlineKeyboardMarkup(inline_keyboard=keyboard_buttons)
        
        await callback.message.answer(
            "".join(response),
            reply_markup=markup,
            parse_mode="HTML",
            disable_web_page_preview=True
        )

    except Exception as e:
        logger.error(f"Ошибка в handle_new_search: {e}", exc_info=True)
        try:
            await session.rollback()
            await callback.message.answer(
                "Произошла ошибка при выполнении нового поиска",
                reply_markup=kb.get_main_keyboard()
            )
        except Exception as inner_e:
            logger.error(f"Двойная ошибка в handle_new_search: {inner_e}")

@router.callback_query(F.data == 'invite_all')
async def handle_invite_all(callback: CallbackQuery, session: AsyncSession, bot: Bot):
    try:
        successful_invites = 0
        
        sender_result = await session.execute(
            select(User, UserState, UserRating)
            .join(UserState, User.id == UserState.user_id)
            .join(UserRating, User.id == UserRating.user_id)
            .where(User.tg_id == callback.from_user.id)
        )
        sender_data = sender_result.first()
        
        if not sender_data:
            await callback.answer("Ошибка: пользователь не найден", show_alert=True)
            return

        sender_user, sender_state, sender_rating = sender_data

        teammates = await search_teammates(
            session,
            callback.from_user.id
        )
        
        if not teammates:
            await callback.answer("Нет игроков для приглашения", show_alert=True)
            return
            
        invite_text = (
            f"🎮 Вас приглашает игрок:\n\n"
            f"👤 <a href='https://www.faceit.com/ru/players/{sender_user.faceit_nickname}'>{sender_user.faceit_nickname}</a>\n"
            f"   🎂 Возраст: {sender_user.age}\n"
            f"   ⚡️ ELO: {sender_state.elo if sender_state else 'не указан'}\n"
            f"   🎮 Роль: {sender_state.role if sender_state else 'не указана'}\n"
            f"   👍 Репутация: {sender_rating.nickname_rating if sender_rating else 10}\n"
            f"   ✅ Верификация: {'Да' if sender_state and sender_state.is_verified else 'Нет'}\n\n"
            f"Хотите создать команду с этим игроком?"
        )
        
        for teammate, teammate_state, _ in teammates:
            if teammate.tg_id:
                try:
                    await bot.send_message(
                        chat_id=teammate.tg_id,
                        text=invite_text,
                        parse_mode="HTML",
                        reply_markup=kb.invite_player_keyboard(sender_user.id)
                    )
                    successful_invites += 1
                except Exception as e:
                    if "bot was blocked" in str(e).lower():
                        logger.warning(f"Пользователь {teammate.faceit_nickname} заблокировал бота")
                        await delete_user_completely(session, teammate.id)
                    else:
                        logger.error(f"Ошибка отправки игроку {teammate.faceit_nickname}: {e}")
        
        sender_user.invite_count += successful_invites
        await session.commit()
        
        await callback.answer(
            f"Приглашения отправлены {successful_invites} игрокам",
            show_alert=True
        )
    except Exception as e:
        await session.rollback() 
        logger.error(f"Ошибка в handle_invite_all: {e}", exc_info=True)
        await callback.answer("Произошла ошибка при отправке приглашений", show_alert=True)

@router.callback_query(F.data.startswith('accept_invite_'))
async def handle_accept_invite(callback: CallbackQuery, session: AsyncSession, bot: Bot):
    try:
        sender_id = int(callback.data.split('_')[-1])
        
        # Явная загрузка отношений для sender
        sender_result = await session.execute(
            select(User)
            .options(joinedload(User.state), joinedload(User.settings))
            .where(User.id == sender_id)
        )
        sender = sender_result.scalars().unique().first()
        
        # Явная загрузка отношений для receiver
        receiver_result = await session.execute(
            select(User)
            .options(joinedload(User.state), joinedload(User.settings))
            .where(User.tg_id == callback.from_user.id)
        )
        receiver = receiver_result.scalars().unique().first()
        
        if not sender or not receiver:
            await callback.answer("Ошибка: пользователь не найден", show_alert=True)
            return
            
        # Форматируем Telegram username
        sender_tg = f"@{sender.tg_username}" if sender.tg_username else "не указан"
        receiver_tg = f"@{receiver.tg_username}" if receiver.tg_username else "не указан"
        
        # Получаем рейтинги
        sender_rating = await session.scalar(
            select(UserRating.nickname_rating).where(UserRating.user_id == sender.id)) or 50
        receiver_rating = await session.scalar(
            select(UserRating.nickname_rating).where(UserRating.user_id == receiver.id)) or 50
        
        # Сообщение для ИГРОКА, КОТОРЫЙ ОТПРАВИЛ приглашение (sender)
        sender_message = (
            f"🎮 Игрок {receiver.faceit_nickname} принял ваше приглашение!\n\n"
            f"👤 <a href='https://www.faceit.com/ru/players/{receiver.faceit_nickname}'>{receiver.faceit_nickname}</a>\n"
            f"📱 Telegram: {receiver_tg}\n"
            f"   🎂 Возраст: {receiver.age}\n"
            f"   ⚡️ ELO: {receiver.state.elo if receiver.state else 'не указан'}\n"
            f"   ⭐️ Репутация: {receiver_rating}\n"
            f"   🎮 Роль: {receiver.state.role if receiver.state else 'не указана'}\n"
            f"   ✅ Верификация: {'Да' if receiver.state and receiver.state.is_verified else 'Нет'}\n"
            f"   🕒 Часовой пояс: {receiver.state.timezone if receiver.state else 'Не указан'}\n"
            f"   💬 Способ связи: {receiver.state.communication_method if receiver.state else 'Не указан'}\n"
            "Свяжитесь с игроком, чтобы создать команду!"
        )
        
        # Сообщение для ИГРОКА, КОТОРЫЙ ПРИНЯЛ приглашение (receiver)
        receiver_message = (
            f"🎮 Вы приняли приглашение от игрока {sender.faceit_nickname}!\n\n"
            f"👤 <a href='https://www.faceit.com/ru/players/{sender.faceit_nickname}'>{sender.faceit_nickname}</a>\n"
            f"📱 Telegram: {sender_tg}\n"
            f"   🎂 Возраст: {sender.age}\n"
            f"   ⚡️ ELO: {sender.state.elo if sender.state else 'не указан'}\n"
            f"   ⭐️ Репутация: {sender_rating}\n"
            f"   🎮 Роль: {sender.state.role if sender.state else 'не указана'}\n"
            f"   ✅ Верификация: {'Да' if sender.state and sender.state.is_verified else 'Нет'}\n"
            f"   🕒 Часовой пояс: {sender.state.timezone if sender.state else 'Не указан'}\n"
            f"   💬 Способ связи: {sender.state.communication_method if sender.state else 'Не указан'}\n"
            "Свяжитесь с игроком, чтобы создать команду!"
        )
        
        await bot.send_message(
            chat_id=sender.tg_id,
            text=sender_message,
            parse_mode="HTML"
        )
        
        await bot.send_message(
            chat_id=receiver.tg_id,
            text=receiver_message,
            parse_mode="HTML"
        )
        
        await callback.message.edit_text(
            "✅ Вы приняли приглашение! Контактная информация отправлена обоим игрокам.",
            reply_markup=None
        )
        
    except Exception as e:
        await session.rollback() 
        logger.error(f"Ошибка при обработке принятия приглашения: {e}", exc_info=True)
        await callback.answer("Произошла ошибка", show_alert=True)

@router.callback_query(F.data.startswith('decline_invite_'))
async def handle_decline_invite(callback: CallbackQuery, session: AsyncSession):
    try:
        sender_id = int(callback.data.split('_')[-1])
        sender = await session.get(User, sender_id)
        
        if sender:
            try:
                await callback.bot.send_message(
                    chat_id=sender.tg_id,
                    text=f"Игрок {callback.from_user.username or callback.from_user.full_name} отклонил ваше приглашение"
                )
            except Exception as e:
                logger.error(f"Не удалось уведомить отправителя: {e}")
        
        await callback.message.edit_text(
            "Вы отклонили приглашение",
            reply_markup=None
        )
        await callback.answer()
        
    except Exception as e:
        await session.rollback() 
        logger.error(f"Ошибка при обработке отклонения приглашения: {e}")
        await callback.answer("Произошла ошибка", show_alert=True)

@router.message(F.text == "⚙️ Настройки профиля")
async def handle_profile_settings(message: Message, session: AsyncSession):
    try:
        user = await get_user_by_tg_id(session, message.from_user.id)
        
        if not user:
            await message.answer("Сначала зарегистрируйтесь с помощью /start")
            return
        
        # Создаем состояние, если его нет
        if not user.state:
            user.state = UserState(user_id=user.id)
            session.add(user.state)
            await session.commit()
            await session.refresh(user, ['state'])
        
        await message.answer(
            "⚙️ Настройки профиля:",
            reply_markup=kb.profile_settings(user.state)  # Передаем состояние
        )
        
    except Exception as e:
        await session.rollback() 
        logger.error(f"Ошибка в обработчике настроек: {e}")
        await message.answer(
            "⚠️ Произошла ошибка при открытии настроек",
            reply_markup=kb.get_main_keyboard(getattr(user, 'is_vip', False))
        )

@router.callback_query(F.data == "communication_settings")
async def communication_settings_menu(callback: CallbackQuery):
    await callback.message.edit_text(
        "Выберите предпочитаемый способ коммуникации:",
        reply_markup=kb.communication_settings_keyboard()
    )
    await callback.answer()

@router.callback_query(F.data == "timezone_settings")
async def timezone_settings_menu(callback: CallbackQuery):
    await callback.message.edit_text(
        "Выберите ваш часовой пояс:",
        reply_markup=kb.timezone_settings_keyboard()
    )
    await callback.answer()

@router.callback_query(F.data.startswith('comm_'))
async def process_communication_setting(callback: CallbackQuery, session: AsyncSession):
    comm_map = {
        'comm_ds': 'DS',
        'comm_ts': 'TS',
        'comm_ds_ts': 'DS/TS',
        'comm_ingame': 'В игре'
    }
    comm_value = comm_map.get(callback.data)
    
    if not comm_value:
        await callback.answer("Неизвестный способ коммуникации")
        return

    try:
        # Явная загрузка пользователя и состояния
        user = await get_user_by_tg_id(session, callback.from_user.id)
        if not user:
            await callback.answer("Пользователь не найден")
            return
        
        # Создаем состояние если его нет
        if not user.state:
            user.state = UserState(
                user_id=user.id,
                communication_method=comm_value
            )
            session.add(user.state)
        else:
            user.state.communication_method = comm_value
        
        await session.commit()
        
        # Проверяем заполненность профиля
        if is_profile_complete(user, user.state):
            await callback.message.edit_text(
                "✅ Все настройки профиля заполнены!",
                reply_markup=None
            )
            await callback.message.answer(
                "Главное меню:",
                reply_markup=kb.get_main_keyboard(user.is_vip)
            )
        else:
            await callback.message.edit_text(
                f"✅ Способ коммуникации установлен: {comm_value}",
                reply_markup=kb.profile_settings(user.state)
            )
    except Exception as e:
        await session.rollback() 
        logger.error(f"Ошибка обновления способа коммуникации: {e}")
        await callback.answer("Ошибка сохранения", show_alert=True)
    finally:
        await callback.answer()

@router.callback_query(F.data.startswith('tz_'))
async def process_timezone_setting(callback: CallbackQuery, session: AsyncSession):
    tz_map = {
        'tz_msk_minus1': 'MSK-1 (UTC+2)',
        'tz_msk_plus0': 'MSK+0 (UTC+3)',
        'tz_msk_plus1': 'MSK+1 (UTC+4)',
        'tz_msk_plus2': 'MSK+2 (UTC+5)',
        'tz_msk_plus3': 'MSK+3 (UTC+6)',
        'tz_msk_plus4': 'MSK+4 (UTC+7)',
        'tz_msk_plus5': 'MSK+5 (UTC+8)',
        'tz_msk_plus6': 'MSK+6 (UTC+9)',
        'tz_msk_plus7': 'MSK+7 (UTC+10)',
        'tz_msk_plus8': 'MSK+8 (UTC+11)',
        'tz_msk_plus9': 'MSK+9 (UTC+12)',
        'tz_msk_plus10': 'MSK+10 (UTC+13)'
    }
    tz_value = tz_map.get(callback.data)
    
    if not tz_value:
        await callback.answer("Неизвестный часовой пояс")
        return

    try:
        # Явная загрузка пользователя
        user = await get_user_by_tg_id(session, callback.from_user.id)
        if not user:
            await callback.answer("Пользователь не найден")
            return

        # Принудительно обновляем состояние если оно уже загружено
        if user.state:
            await session.refresh(user.state)
        
        if not user.state:
            user.state = UserState(
                user_id=user.id,
                timezone=tz_value
            )
            session.add(user.state)
        else:
            user.state.timezone = tz_value
        
        await session.commit()
        
        # Проверяем заполненность профиля
        if is_profile_complete(user, user.state):
            await callback.message.edit_text(
                "✅ Все настройки профиля заполнены!",
                reply_markup=None
            )
            await callback.message.answer(
                "Главное меню:",
                reply_markup=kb.get_main_keyboard(user.is_vip)
            )
        else:
            await callback.message.edit_text(
                f"✅ Часовой пояс установлен: {tz_value}",
                reply_markup=kb.profile_settings(user.state)
            )
    except Exception as e:
        await session.rollback() 
        logger.error(f"Ошибка обновления часового пояса: {e}")
        await callback.answer("Ошибка сохранения", show_alert=True)
    finally:
        await callback.answer()

@router.callback_query(F.data == "vip_settings")
async def vip_settings(callback: CallbackQuery, session: AsyncSession):
    user = await get_user_by_tg_id(session, callback.from_user.id)
    if not user:
        await callback.answer("Пользователь не найден")
        return

    settings = user.settings
    if not settings:
        settings = UserSettings(user_id=user.id)
        session.add(settings)
        await session.commit()

    # Формируем текст с текущими настройками
    settings_text = (
        f"⚙️ Ваши текущие настройки поиска:\n"
        f"• Диапазон ELO: ±{settings.elo_range}\n"
        f"• Диапазон возраста: {settings.min_age}-{settings.max_age} лет"
    )

    await callback.message.edit_text(
        settings_text,
        reply_markup=kb.settings_keyboard()
    )

@router.callback_query(F.data == "set_elo_range")
async def set_elo_range(callback: CallbackQuery, session: AsyncSession):
    user = await get_user_by_tg_id(session, callback.from_user.id)
    if not user or not user.is_vip:
        await callback.answer("Эта функция доступна только VIP пользователям")
        return
    
    current_range = user.settings.elo_range if user and user.settings else 300
    await callback.message.edit_text(
        f"Текущий диапазон: ±{current_range}\nВыберите новый диапазон ELO:",
        reply_markup=kb.elo_range_keyboard()
    )

@router.callback_query(F.data.startswith("elo_"))
async def apply_elo_range(callback: CallbackQuery, session: AsyncSession):
    range_map = {
        "elo_50": 50,
        "elo_100": 100,
        "elo_200": 200,
        "elo_300": 300,
        "elo_400": 400
    }
    new_range = range_map[callback.data]
    
    user = await get_user_by_tg_id(session, callback.from_user.id)
    if not user or not user.is_vip:
        await callback.answer("Эта функция доступна только VIP пользователям", show_alert=True)
        return
    
    # Проверяем наличие настроек и создаем их при необходимости
    if not user.settings:
        user.settings = UserSettings(
            user_id=user.id,
            elo_range=300,
            min_age=12,
            max_age=60,
            notifications=True
        )
        session.add(user.settings)
        await session.commit()
    
    user.settings.elo_range = new_range
    await session.commit()
    
    # Остальной код без изменений
    await callback.message.edit_text(
        f"✅ Диапазон ELO изменен на ±{new_range}",
        reply_markup=kb.search_settings_keyboard()
    )
    await callback.answer()


@router.message(F.text == '📊 Мои данные')
async def handle_my_data(message: Message, session: AsyncSession):
    await track_activity(session, message.from_user.id, "my_data")
    try:
        user = await session.scalar(
            select(User)
            .options(joinedload(User.state), joinedload(User.settings))
            .where(User.tg_id == message.from_user.id)
        )
        
        if not user:
            await message.answer("Сначала зарегистрируйтесь с помощью /start")
            return
            
        invite_count = user.invite_count
        
        user_rating = await session.get(UserRating, user.id)
        rating = user_rating.nickname_rating if user_rating else 50
        
        vip_info = "❌ Неактивна"
        if user.is_vip:
            if user.vip_expires_at is None:
                vip_info = "✅ Навсегда"
            else:
                vip_info = f"✅ До {user.vip_expires_at.strftime('%d.%m.%Y')}"
        
        # Получаем значения из профиля
        comm_value = user.state.communication_method if user.state else "Не указан"
        tz_value = user.state.timezone if user.state else "Не указан"

        response = (
            f"📊 Ваши данные:\n\n"
            f"👤 Никнейм: {user.faceit_nickname}\n"
            f"⭐️ Рейтинг: {rating}\n"
            f"💬 Способ связи: {comm_value}\n"
            f"⏰ Часовой пояс: {tz_value}\n"
            f"📨 Отправлено приглашений: {invite_count}\n"
            f"💎 VIP подписка: {vip_info}"
        )
        
        await message.answer(response)
        
    except Exception as e:
        await session.rollback() 
        logger.error(f"Ошибка при получении данных пользователя: {e}")
        await message.answer("Произошла ошибка при получении данных")

@router.callback_query(F.data == 'elo_range_settings')
async def elo_range_settings(callback: CallbackQuery, session: AsyncSession):
    user = await session.get(User, callback.from_user.id)
    if not user or not user.is_vip:
        await callback.answer("Эта функция доступна только VIP пользователям", show_alert=True)
        return
    
    await callback.message.edit_text(
        'Выберите диапазон ELO для поиска:',
        reply_markup=kb.elo_range_keyboard()
    )
    await callback.answer()

@router.callback_query(F.data.startswith('elo_range_'))
async def set_elo_range(callback: CallbackQuery, session: AsyncSession):
    try:
        range_value = int(callback.data.split('_')[2])
        
        user = await session.scalar(
            select(User)
            .options(joinedload(User.settings))
            .where(User.tg_id == callback.from_user.id)
        )
        
        if not user or not user.is_vip:
            await callback.answer("Эта функция доступна только VIP пользователям", show_alert=True)
            return
        
        if not user.settings:
            user.settings = UserSettings(user_id=user.id)
            session.add(user.settings)
        
        user.settings.elo_range = range_value
        await session.commit()
        
        await callback.message.edit_text(
            f'✅ Диапазон ELO установлен: ±{range_value}',
            reply_markup=InlineKeyboardMarkup(inline_keyboard=[
                [InlineKeyboardButton(text='⬅️ Назад в меню', callback_data='back_to_main')]]
            )
        )
    except Exception as e:
        await session.rollback() 
        logger.error(f"Ошибка установки диапазона ELO: {e}")
        await callback.answer("Произошла ошибка", show_alert=True)
    finally:
        await callback.answer()

@router.callback_query(F.data == "set_age_range")
async def set_age_range(callback: CallbackQuery, session: AsyncSession):
    user = await get_user_by_tg_id(session, callback.from_user.id)
    if not user or not user.is_vip:
        await callback.answer("Эта функция доступна только VIP пользователям")
        return
    
    # Получаем текущие настройки
    settings = user.settings or UserSettings(user_id=user.id)
    current_range = f"{settings.min_age}-{settings.max_age} лет"
    
    await callback.message.edit_text(
        f"🔞 Текущий диапазон возраста: {current_range}\n"
        "Выберите новый диапазон для поиска игроков:",
        reply_markup=kb.age_range_keyboard()
    )

@router.callback_query(F.data == 'back_to_search_settings')
async def back_to_search_settings(callback: CallbackQuery):
    # Важно: используем edit_text с сохранением эмодзи
    await callback.message.edit_text(
        "⚙️ Настройки параметров поиска:",
        reply_markup=kb.search_settings_keyboard()
    )
    await callback.answer()

@router.callback_query(F.data.startswith("age_"))
async def apply_age_range(callback: CallbackQuery, session: AsyncSession):
    age_map = {
        "age_12_15": (12, 15),
        "age_15_20": (15, 20),
        "age_20_25": (20, 25),
        "age_25_30": (25, 30),
        "age_30_35": (30, 35),
        "age_12_60": (12, 60)
    }
    min_age, max_age = age_map[callback.data]
    
    user = await get_user_by_tg_id(session, callback.from_user.id)
    if not user or not user.is_vip:
        await callback.answer("Эта функция доступна только VIP пользователям")
        return
    
    # Проверяем наличие настроек и создаем их при необходимости
    if not user.settings:
        user.settings = UserSettings(
            user_id=user.id,
            elo_range=300,
            min_age=12,
            max_age=60,
            notifications=True
        )
        session.add(user.settings)
        await session.commit()
    
    user.settings.min_age = min_age
    user.settings.max_age = max_age
    await session.commit()
    
    # Остальной код без изменений
    await callback.message.edit_text(
        f"✅ Диапазон возраста изменен на {min_age}-{max_age} лет",
        reply_markup=kb.search_settings_keyboard()
    )
    await callback.answer()

@router.message(F.text == '🔒 Бан-лист')
async def ban_list_menu(message: Message, session: AsyncSession):
    try:
        user = await session.scalar(
            select(User)
            .where(User.tg_id == message.from_user.id)
        )
        if not user:
            logger.error("Пользователь не найден в БД")
            return await message.answer("Пользователь не найден")
        
        if not user.is_vip:
            return await message.answer("Эта функция доступна только VIP пользователям")
        
        bans = await session.scalars(
            select(BanList)
            .where(BanList.user_id == user.id)
            .order_by(BanList.id)
        )
        bans_list = bans.all()
        
        # Формируем текст сообщения
        if not bans_list:
            text = "Ваш бан-лист пуст"
        else:
            text = "🔒 Ваш бан-лист:\n\n" + "\n".join(
                f"{i+1}. {ban.banned_nickname}" for i, ban in enumerate(bans_list)
            )
        
        # Создаем клавиатуру с кнопками управления
        keyboard = InlineKeyboardBuilder()
        
        # Добавляем кнопки удаления для каждого игрока
        for ban in bans_list:
            keyboard.button(
                text=f"❌ Удалить {ban.banned_nickname}",
                callback_data=f"remove_ban_{ban.id}"
            )
        
        # Кнопка добавления (если есть свободные слоты)
        if len(bans_list) < 5:
            keyboard.button(
                text="➕ Добавить игрока",
                callback_data="add_to_ban_list"
            )
        
        # Кнопка возврата в главное меню
        keyboard.button(
            text="⬅️ Назад в меню",
            callback_data="back_to_main_menu"
        )
        
        # Распределяем кнопки по строкам
        keyboard.adjust(1)
        
        # Отправляем сообщение
        await message.answer(
            text,
            reply_markup=keyboard.as_markup()
        )
        
    except Exception as e:
        await session.rollback() 
        logger.error(f"Ошибка в ban_list_menu: {e}", exc_info=True)
        await message.answer(
            "Произошла ошибка при загрузке бан-листа",
            reply_markup=kb.get_main_keyboard(False)
        )

@router.callback_query(F.data == 'add_to_ban_list')
async def add_to_ban_list_handler(callback: CallbackQuery, state: FSMContext, session: AsyncSession):
    try:
        ban_count = await session.scalar(
            select(func.count(BanList.id))
            .where(BanList.user_id == callback.from_user.id)
        )
        if ban_count >= 5:
            await callback.answer("Достигнут лимит (5 игроков)", show_alert=True)
            return
            
        await callback.message.edit_text(
            "Введите никнейм игрока для добавления в бан-лист:",
            reply_markup=kb.cancel_ban_list_keyboard()
        )
        await state.set_state(SettingsStates.waiting_for_ban_nickname)
        await callback.answer()
    except Exception as e:
        await session.rollback() 
        logger.error(f"Ошибка в add_to_ban_list_handler: {e}")
        await callback.answer("Произошла ошибка", show_alert=True)

@router.callback_query(F.data == 'cancel_ban_list')
async def cancel_ban_list_action(callback: CallbackQuery, state: FSMContext, session: AsyncSession):
    try:
        await state.clear()
        await callback.message.delete()
        
        user = await session.scalar(
            select(User.is_vip)
            .where(User.tg_id == callback.from_user.id)
        )
        is_vip = user or False
        
        await callback.message.answer(
            "Действие отменено",
            reply_markup=kb.get_main_keyboard(is_vip))
        
        await callback.answer()
    except Exception as e:
        await session.rollback() 
        logger.error(f"Ошибка в cancel_ban_list_action: {e}")
        await callback.answer("Произошла ошибка", show_alert=True)

@router.message(SettingsStates.waiting_for_ban_nickname)
async def process_ban_nickname(
    message: Message, 
    state: FSMContext, 
    session: AsyncSession
):
    user = await get_user_by_tg_id(session, message.from_user.id)
    if not user:
        await message.answer("❌ Пользователь не найден")
        await state.clear()
        return

    nickname = message.text.strip()
    
    # Проверка на добавление себя в бан-лист
    if user.faceit_nickname and nickname.lower() == user.faceit_nickname.lower():
        await message.answer("❌ Вы не можете добавить себя в бан-лист!")
        await state.clear()
        
        # Возвращаем пользователя в меню бан-листа
        await ban_list_menu(message, session)
        return

    try:
        # Проверяем количество уже добавленных в бан-лист
        ban_count = await session.scalar(
            select(func.count(BanList.id))
            .where(BanList.user_id == user.id)
        )
        
        if ban_count >= 5:
            await message.answer("❌ Вы не можете добавить более 5 игроков в бан-лист")
            await state.clear()
            return

        # Добавляем в бан-лист
        await add_to_ban_list(session, user.id, nickname)
        await session.commit()
        
        # Получаем обновленный бан-лист
        bans = await session.scalars(
            select(BanList)
            .where(BanList.user_id == user.id)
            .order_by(BanList.id)
        )
        bans_list = bans.all()
        
        # Формируем текст сообщения
        if not bans_list:
            text = "Ваш бан-лист пуст"
        else:
            text = "🔒 Ваш бан-лист:\n\n" + "\n".join(
                f"{i+1}. {ban.banned_nickname}" for i, ban in enumerate(bans_list)
            )
        
        # Создаем клавиатуру с кнопками управления
        keyboard = InlineKeyboardBuilder()
        
        # Добавляем кнопки удаления для каждого игрока
        for ban in bans_list:
            keyboard.button(
                text=f"❌ Удалить {ban.banned_nickname}",
                callback_data=f"remove_ban_{ban.id}"
            )
        
        # Кнопка добавления (если есть свободные слоты)
        if len(bans_list) < 5:
            keyboard.button(
                text="➕ Добавить игрока",
                callback_data="add_to_ban_list"
            )
        
        # Кнопка возврата в главное меню
        keyboard.button(
            text="⬅️ Назад в меню",
            callback_data="back_to_main_menu"
        )
        
        # Распределяем кнопки по строкам
        keyboard.adjust(1)
        
        await message.answer(
            f"✅ Игрок {nickname} добавлен в ваш бан-лист\n\n{text}",
            reply_markup=keyboard.as_markup()
        )
        
    except Exception as e:
        await session.rollback() 
        logger.error(f"Ошибка при добавлении в бан-лист: {e}", exc_info=True)
        # Восстанавливаем соответствующее меню
        reply_markup = kb.get_main_keyboard(user.is_vip)
        await message.answer(
            "⚠️ Произошла ошибка при добавлении в бан-лист",
            reply_markup=reply_markup
        )
    finally:
        await state.clear()

@router.callback_query(F.data.startswith('remove_ban_'))
async def remove_from_ban_list(callback: CallbackQuery, session: AsyncSession):
    try:
        ban_id = int(callback.data.split('_')[-1])
        ban = await session.get(BanList, ban_id)
        
        if not ban:
            await callback.answer("Запись не найдена", show_alert=True)
            return
            
        nickname = ban.banned_nickname
        await session.delete(ban)
        await session.commit()
        
        # После удаления обновляем сообщение с бан-листом
        user = await session.scalar(
            select(User).where(User.tg_id == callback.from_user.id))
        
        bans = await session.scalars(
            select(BanList).where(BanList.user_id == user.id)
        )
        bans_list = bans.all()
        
        # Формируем новый текст
        if not bans_list:
            text = "Ваш бан-лист пуст"
        else:
            text = "🔒 Ваш бан-лист:\n\n" + "\n".join(
                f"{i+1}. {b.banned_nickname}" for i, b in enumerate(bans_list)
            )
        
        # Создаем новую клавиатуру
        keyboard = InlineKeyboardBuilder()
        
        for ban in bans_list:
            keyboard.button(
                text=f"❌ Удалить {ban.banned_nickname}",
                callback_data=f"remove_ban_{ban.id}"
            )
        
        if len(bans_list) < 5:
            keyboard.button(
                text="➕ Добавить игрока",
                callback_data="add_to_ban_list"
            )
        
        keyboard.button(
            text="⬅️ Назад в меню",
            callback_data="back_to_main_menu"
        )
        
        keyboard.adjust(1)
        
        # Обновляем сообщение
        await callback.message.edit_text(
            text,
            reply_markup=keyboard.as_markup()
        )
        
        await callback.answer(f"Игрок {nickname} удален из бан-листа")
    except Exception as e:
        await session.rollback() 
        logger.error(f"Ошибка в remove_from_ban_list: {e}")
        await callback.answer("Произошла ошибка", show_alert=True)

@router.callback_query(F.data == 'cancel_ban')
async def cancel_ban(callback: CallbackQuery, state: FSMContext):
    await state.clear()
    await callback.message.edit_text(
        "Действие отменено",
        reply_markup=kb.get_main_keyboard(True)
    )
    await callback.answer()

async def handle_back_to_ban_list(callback: CallbackQuery, session: AsyncSession):
    try:
        user = await session.scalar(
            select(User).where(User.tg_id == callback.from_user.id)
        )
        if not user or not user.is_vip:
            await callback.answer("Эта функция доступна только VIP пользователям")
            return
            
        bans = await session.scalars(
            select(BanList)
            .where(BanList.user_id == user.id)
            .order_by(BanList.id)
        )
        bans_list = bans.all()
        
        text = "Ваш бан-лист:\n\n" + "\n".join(
            f"{i+1}. {ban.banned_nickname}" for i, ban in enumerate(bans_list)
        ) if bans_list else "Ваш бан-лист пуст"
        
        # Используем новую клавиатуру без кнопки "Очистить бан-лист"
        await callback.message.edit_text(
            text,
            reply_markup=kb.ban_list_management_keyboard(bans_list)
        )
        await callback.answer()
    except Exception as e:
        await session.rollback() 
        logger.error(f"Ошибка в handle_back_to_ban_list: {e}", exc_info=True)
        await callback.answer("Произошла ошибка", show_alert=True)

@router.message(F.text == '📊 Диапазон ELO')
async def handle_elo_range(message: Message, session: AsyncSession):
    user = await session.scalar(
        select(User)
        .where(User.tg_id == message.from_user.id)
    )
    
    if not user or not user.is_vip:
        await message.answer("Эта функция доступна только VIP пользователям")
        return
    
    user = await session.scalar(
        select(User)
        .options(joinedload(User.settings))
        .where(User.tg_id == message.from_user.id)
    )
    
    current_range = user.settings.elo_range if user and user.settings else 200
    
    await message.answer(
        f"Текущий диапазон поиска: ±{current_range} ELO\n"
        "Выберите новый диапазон:",
        reply_markup=kb.elo_range_keyboard()
    )

@router.callback_query(F.data == "profile_settings")
async def profile_settings_menu(callback: CallbackQuery, session: AsyncSession):
    try:
        user = await get_user_by_tg_id(session, callback.from_user.id)
        if not user or not user.state:
            await callback.answer("Ошибка: состояние не найдено")
            return
        
        await callback.message.edit_text(
            "Выберите настройку:",
            reply_markup=kb.profile_settings(user.state)  # Передаем состояние
        )
    except Exception as e:
        logger.error(f"Ошибка в меню настроек: {e}")
        await callback.answer("Произошла ошибка", show_alert=True)
    finally:
        await callback.answer()

@router.callback_query(F.data == "search_status_settings")
async def handle_search_button(callback: CallbackQuery):
    await callback.message.edit_text(
        'Статус поиска команды:\n\n'
        '"Да" - другие игроки будут видеть вас в поиске\n'
        '"Нет" - вас не будут видеть в поиске\n\n'
        'Выберите вариант:',
        reply_markup=kb.search_status_settings()
    )
    await callback.answer()

@router.callback_query(or_f(F.data == 'yes_status', F.data == 'no_status'))
async def process_search_status(callback: CallbackQuery, session: AsyncSession):
    try:
        search_status = callback.data == "yes_status"
        
        # Явная загрузка пользователя и состояния
        user = await get_user_by_tg_id(session, callback.from_user.id)
        if not user:
            logger.warning(f"Пользователь не найден: {callback.from_user.id}")
            await callback.answer("Пользователь не найден", show_alert=True)
            return
        
        # Создаем состояние если его нет
        if not user.state:
            user.state = UserState(
                user_id=user.id,
                search_team=search_status
            )
            session.add(user.state)
        else:
            user.state.search_team = search_status
        
        await session.commit()
        
        # Проверяем заполненность профиля
        if is_profile_complete(user, user.state):
            await callback.message.edit_text(
                "✅ Все настройки профиля заполнены!",
                reply_markup=None
            )
            await callback.message.answer(
                "Главное меню:",
                reply_markup=kb.get_main_keyboard(user.is_vip)
            )
        else:
            await callback.message.edit_text(
                f'Статус поиска: {"✅ Активен" if search_status else "❌ Не активен"}',
                reply_markup=kb.profile_settings(user.state)
            )
    except Exception as e:
        await session.rollback() 
        logger.error(f"Ошибка в process_search_status: {e}", exc_info=True)
        await callback.answer("Произошла ошибка", show_alert=True)
    finally:
        await callback.answer()

@router.callback_query(F.data == "team_role_settings")
async def role_menu(callback: CallbackQuery):
    await callback.message.edit_text(
        "Выберите вашу роль:",
        reply_markup=kb.team_role_settings()
    )
    await callback.answer()

@router.callback_query(F.data == "back_to_settings")
async def back_to_settings(callback: CallbackQuery, session: AsyncSession):
    try:
        user = await get_user_by_tg_id(session, callback.from_user.id)
        if not user or not user.state:
            await callback.answer("Ошибка: состояние не найдено")
            return
        
        # Проверяем заполненность профиля
        if is_profile_complete(user, user.state):
            await callback.message.edit_text(
                "✅ Все настройки профиля заполнены!",
                reply_markup=None
            )
            await callback.message.answer(
                "Главное меню:",
                reply_markup=kb.get_main_keyboard(user.is_vip)
            )
        else:
            await callback.message.edit_text(
                "Настройки профиля:",
                reply_markup=kb.profile_settings(user.state)  # Передаем состояние
            )
    except Exception as e:
        logger.error(f"Ошибка при возврате в настройки: {e}")
        await callback.answer("Произошла ошибка", show_alert=True)
    finally:
        await callback.answer()

@router.callback_query(F.data == 'back_to_main_menu')
async def back_to_main_menu_handler(callback: CallbackQuery, session: AsyncSession):
    try:
        user = await session.scalar(
            select(User.is_vip).where(User.tg_id == callback.from_user.id))
        is_vip = user or False
        
        # Удаляем текущее сообщение
        await callback.message.delete()
        
        # Отправляем новое сообщение с главным меню
        await callback.message.answer(
            "Главное меню:",
            reply_markup=kb.get_main_keyboard(is_vip))
        
        await callback.answer()
    except Exception as e:
        await session.rollback() 
        logger.error(f"Ошибка при возврате в главное меню: {e}")
        await callback.answer("Произошла ошибка", show_alert=True)

@router.callback_query(F.data == "show_main_menu")
async def show_main_menu(callback: CallbackQuery):
    await callback.message.answer(
        "Главное меню:",
        reply_markup=kb.get_main_keyboard()
    )
    await callback.answer()

@router.callback_query(F.data.startswith('role_'))
async def process_team_role(callback: CallbackQuery, session: AsyncSession):
    role_mapping = {
        'role_igl': 'in-Game Leader (IGL)',
        'role_support': 'Опорник',
        'role_support_lurker': 'Support/Lurker',
        'role_awper': 'AWPer',
        'role_entry': 'Entry Fragger'
    }
    
    role = role_mapping.get(callback.data)
    if not role:
        await callback.answer("Неизвестная роль", show_alert=True)
        return
    
    try:
        user = await get_user_by_tg_id(session, callback.from_user.id)
        
        if user:
            # Обновляем роль
            if not user.state:
                user.state = UserState(user_id=user.id)
                session.add(user.state)
            
            user.state.role = role
            await session.commit()
            
            # Проверяем заполненность профиля
            if is_profile_complete(user, user.state):
                await callback.message.edit_text(
                    "✅ Все настройки профиля заполнены!",
                    reply_markup=None
                )
                await callback.message.answer(
                    "Главное меню:",
                    reply_markup=kb.get_main_keyboard(user.is_vip)
                )
            else:
                await callback.message.edit_text(
                    f'✅ Ваша роль установлена: {role}',
                    reply_markup=kb.profile_settings(user.state)
                )
    except Exception as e:
        await session.rollback() 
        logger.error(f"Error in process_team_role: {e}")
        await callback.answer("Произошла ошибка", show_alert=True)
    finally:
        await callback.answer()

@router.callback_query(F.data == "verification_status")
async def verification_menu(callback: CallbackQuery):
    await callback.message.edit_text(
        "Ваш статус верификации:",
        reply_markup=kb.verification_status_keyboard()
    )
    await callback.answer()

@router.callback_query(or_f(F.data == 'verification_yes', F.data == 'verification_no'))
async def process_verification_status(callback: CallbackQuery, session: AsyncSession):
    try:
        is_verified = callback.data == 'verification_yes'
        
        # Явная загрузка пользователя и состояния
        user = await get_user_by_tg_id(session, callback.from_user.id)
        if not user:
            logger.warning(f"Пользователь не найден: {callback.from_user.id}")
            await callback.answer("Пользователь не найден", show_alert=True)
            return
        
        logger.info(f"Изменение верификации для {user.tg_id}: {is_verified}")
        
        # Создаем состояние если его нет
        if not user.state:
            logger.info("Создание нового состояния для пользователя")
            user.state = UserState(
                user_id=user.id,
                is_verified=is_verified
            )
            session.add(user.state)
        else:
            logger.info(f"Текущий статус верификации: {user.state.is_verified}")
            user.state.is_verified = is_verified
        
        await session.commit()
        
        # Проверяем заполненность профиля
        if is_profile_complete(user, user.state):
            await callback.message.edit_text(
                "✅ Все настройки профиля заполнены!",
                reply_markup=None
            )
            await callback.message.answer(
                "Главное меню:",
                reply_markup=kb.get_main_keyboard(user.is_vip)
            )
        else:
            await callback.message.edit_text(
                f'Статус верификации: {"✅ Подтверждена" if is_verified else "❌ Не подтверждена"}',
                reply_markup=kb.profile_settings(user.state)
            )
    except Exception as e:
        await session.rollback()
        logger.error(f"Ошибка в process_verification_status: {e}", exc_info=True)
        await callback.answer("Произошла ошибка", show_alert=True)
    finally:
        await callback.answer()

@router.message(F.text == '⭐️ Оценить игрока')
async def start_unified_rating(message: Message, state: FSMContext):
    await message.answer(
        "Введите никнейм игрока:",
        reply_markup=kb.cancel_unified_rating_keyboard()  # Используем новую клавиатуру
    )
    await state.set_state(UnifiedRatingStates.waiting_for_nickname)

@router.message(UnifiedRatingStates.waiting_for_nickname)
async def process_unified_nickname(message: Message, state: FSMContext, session: AsyncSession):
    try:
        nickname = message.text.strip()
        logger.info(f"Обработка никнейма для оценки: {nickname}")
        
        # Проверяем, не пытается ли пользователь оценить себя
        current_user = await get_user_by_tg_id(session, message.from_user.id)
        if current_user and current_user.faceit_nickname and current_user.faceit_nickname.lower() == nickname.lower():
            await message.answer("Вы не можете оценить самого себя!")
            await state.clear()
            return
        
        # Ищем пользователя по никнейму
        result = await session.execute(
            select(User).where(func.lower(User.faceit_nickname) == nickname.lower())
        )
        user = result.scalars().first()
        
        if not user:
            logger.warning(f"Игрок с никнеймом {nickname} не найден")
            await message.answer(
                "Игрок с таким никнеймом не найден. Попробуйте еще раз:",
                reply_markup=kb.cancel_unified_rating_keyboard()
            )
            return
        
        # Сохраняем данные для следующего шага
        await state.update_data(
            target_nickname=nickname, 
            target_user_id=user.id
        )
        
        # Переходим к выбору действия
        await message.answer(
            f"Выберите действие для игрока {nickname}:",
            reply_markup=kb.unified_rating_options()
        )
        await state.set_state(UnifiedRatingStates.waiting_for_action)
        
    except Exception as e:
        await session.rollback() 
        logger.error(f"Ошибка обработки никнейма: {e}", exc_info=True)
        await message.answer(
            "Произошла ошибка при обработке никнейма. Попробуйте снова.",
            reply_markup=kb.get_main_keyboard()
        )
        await state.clear()

@router.callback_query(UnifiedRatingStates.waiting_for_action, F.data.startswith('unified_'))
async def handle_unified_action(callback: CallbackQuery, state: FSMContext, session: AsyncSession):
    action = callback.data.split('_')[1]
    
    if action == 'report':
        await callback.message.edit_text(
            "Выберите причину жалобы:",
            reply_markup=kb.report_reasons_keyboard()
        )
        await state.set_state(UnifiedRatingStates.waiting_for_report_reason)
    elif action == 'praise':
        await callback.message.edit_text(
            "Выберите причину похвалы:",
            reply_markup=kb.praise_reasons_keyboard()
        )
        await state.set_state(UnifiedRatingStates.waiting_for_praise_reason)
    elif action == 'cancel':
        await handle_unified_cancel(callback, state, session)
    
    await callback.answer()

@router.callback_query(F.data == 'back_to_main_menu')
async def back_to_main_menu_handler(callback: CallbackQuery, session: AsyncSession):
    try:
        # Получаем информацию о пользователе
        user = await get_user_by_tg_id(session, callback.from_user.id)
        is_vip = user.is_vip if user else False
        
        # Удаляем текущее сообщение
        await callback.message.delete()
        
        # Отправляем новое сообщение с главным меню
        await callback.message.answer(
            "Главное меню:",
            reply_markup=kb.get_main_keyboard(is_vip))
        
        await callback.answer()
    except Exception as e:
        await session.rollback() 
        logger.error(f"Ошибка при возврате в главное меню: {e}")
        await callback.answer("Произошла ошибка", show_alert=True)

@router.callback_query(F.data == 'back_to_main')
async def handle_legacy_back(callback: CallbackQuery, session: AsyncSession):
    # Просто перенаправляем на новый обработчик
    await back_to_main_menu_handler(callback, session)

@router.callback_query(
    UnifiedRatingStates.waiting_for_report_reason, 
    F.data.startswith('report_reason:'))
async def process_report_reason(callback: CallbackQuery, state: FSMContext, session: AsyncSession, bot: Bot):
    value = int(callback.data.split(':')[1])
    await process_rating(callback, state, session, bot, is_positive=False, value=value)

@router.callback_query(F.data.startswith('rate:'))
async def handle_rate_choice(callback: CallbackQuery, state: FSMContext):
    action = callback.data.split(':')[1]
    if action == 'report':
        await callback.message.edit_text(
            "Выберите причину жалобы:",
            reply_markup=kb.report_reasons_keyboard()
        )
    elif action == 'praise':
        await callback.message.edit_text(
            "Выберите причину похвалы:",
            reply_markup=kb.praise_reasons_keyboard()
        )
    await callback.answer()

@router.callback_query(
    UnifiedRatingStates.waiting_for_praise_reason, 
    F.data.startswith('praise_reason:'))
async def process_praise_reason(callback: CallbackQuery, state: FSMContext, session: AsyncSession, bot: Bot):
    value = int(callback.data.split(':')[1])
    await process_rating(callback, state, session, bot, is_positive=True, value=value)

async def process_rating(callback, state, session, bot, is_positive, value):
    data = await state.get_data()
    target_user_id = data['target_user_id']
    target_nickname = data['target_nickname']
    
    try:
        reporter = await session.scalar(
            select(User).where(User.tg_id == callback.from_user.id)
        )
        
        if not reporter:
            await callback.answer("Ошибка: пользователь не найден", show_alert=True)
            await state.clear()
            return

        # Получаем рейтинг целевого игрока (а не репортера!)
        user_rating = await session.scalar(
            select(UserRating)
            .where(UserRating.user_id == target_user_id)
        )
        
        if not user_rating:
            # Создаем рейтинг для целевого игрока, если его нет
            user_rating = UserRating(
                user_id=target_user_id,
                faceit_nickname=target_nickname,
                nickname_rating=50 + (value if is_positive else -value)
            )
            session.add(user_rating)
        else:
            # Изменяем рейтинг целевого игрока
            if is_positive:
                user_rating.nickname_rating += value
            else:
                user_rating.nickname_rating -= value
        
        # Создаем запись о репутации
        new_reputation = UserReputation(
            reporter_id=reporter.id,
            reported_user_id=target_user_id,
            is_positive=is_positive
        )
        session.add(new_reputation)
        
        await session.commit()
        
        await callback.message.edit_text(
            f"Вы {'повысили' if is_positive else 'понизили'} репутацию игрока {target_nickname}",
            reply_markup=None
        )
        
        await bot.send_message(
            chat_id=callback.from_user.id,
            text="Выберите действие:",
            reply_markup=kb.get_main_keyboard(reporter.is_vip))
        
    except Exception as e:
        await session.rollback() 
        logger.error(f"Ошибка при оценке игрока: {e}", exc_info=True)
        await callback.answer("Произошла ошибка", show_alert=True)
    finally:
        await state.clear()
        await callback.answer()

@router.callback_query(F.data.startswith('rate:'))
async def handle_rate_choice(callback: CallbackQuery, state: FSMContext, session: AsyncSession):
    action = callback.data.split(':')[1]
    if action == 'report':
        await callback.message.edit_text(
            "Выберите причину жалобы:",
            reply_markup=kb.report_reasons_keyboard()
        )
    elif action == 'praise':
        await callback.message.edit_text(
            "Выберите причину похвалы:",
            reply_markup=kb.praise_reasons_keyboard()
        )
    elif action == 'cancel':
        await handle_unified_cancel(callback, state, session)
    await callback.answer()

@router.callback_query(F.data == 'cancel_rating')
async def cancel_rating(callback: CallbackQuery, state: FSMContext, bot: Bot, session: AsyncSession):
    await state.clear()
    await callback.message.edit_text(
        "Оценка игрока отменена",
        reply_markup=None
    )
    
    user = await session.scalar(
        select(User.is_vip).where(User.tg_id == callback.from_user.id))
    is_vip = user or False
    
    await bot.send_message(
        chat_id=callback.from_user.id,
        text="Выберите действие:",
        reply_markup=kb.get_main_keyboard(is_vip))
    
    await callback.answer()

@router.callback_query(F.data == 'report_user_player')
async def start_report(callback: CallbackQuery, state: FSMContext):
    await callback.message.edit_text(
        "Введите никнейм игрока, которого хотите репортнуть:",
        reply_markup=kb.report_user_player()
    )
    await state.set_state(ReportStates.waiting_for_nickname)
    await callback.answer()

@router.callback_query(F.data == 'input_faceit_nickname')
async def input_faceit_nickname(callback: CallbackQuery, state: FSMContext):
    await callback.message.edit_text(
        "Отправьте мне никнейм игрока из Faceit:",
        reply_markup=kb.cancel_report()
    )
    await state.set_state(ReportStates.waiting_for_nickname)
    await callback.answer()

@router.message(Register.faceit_nickname)
async def process_faceit_nickname(message: Message, session: AsyncSession, state: FSMContext):
    faceit_nickname = message.text.strip()
    
    try:
        # Проверяем существование аккаунта Faceit
        if not await faceit_api.check_account_exists(faceit_nickname):
            await message.answer(
                "Аккаунт Faceit с таким никнеймом не найден. Пожалуйста, введите корректный никнейм:",
                reply_markup=kb.cancel_registration_keyboard()
            )
            return
    except Exception as e:
        logger.error(f"Ошибка при проверке Faceit аккаунта: {e}")
        await message.answer(
            "Произошла ошибка при проверке Faceit аккаунта. Пожалуйста, попробуйте позже.",
            reply_markup=kb.cancel_registration_keyboard()
        )
        return
    
    # Проверяем, не зарегистрирован ли уже этот никнейм
    existing_user = await session.execute(
        select(User).where(User.faceit_nickname == faceit_nickname)
    )
    if existing_user.scalar():
        await message.answer(
            "Этот Faceit аккаунт уже зарегистрирован. Пожалуйста, используйте другой никнейм.",
            reply_markup=kb.cancel_registration_keyboard()
        )
        return
    
    await state.update_data(faceit_nickname=faceit_nickname)
    await message.answer(
        "Отлично! Теперь введите ваш возраст:",
        reply_markup=kb.cancel_registration_keyboard()
    )
    await state.set_state(Registration.waiting_for_age)

@router.callback_query(F.data.startswith('report_reason_'), ReportStates.waiting_for_reason)
async def process_report_reason(callback: CallbackQuery, state: FSMContext, session: AsyncSession):
    reason = int(callback.data.split('_')[-1])
    data = await state.get_data()
    faceit_nickname = data['faceit_nickname']
    
    reporter = await session.scalar(
        select(User).where(User.tg_id == callback.from_user.id))
    reported_user = await session.scalar(
        select(User).where(func.lower(User.faceit_nickname) == faceit_nickname.lower()))
    
    if not reporter or not reported_user:
        await callback.answer("Ошибка: пользователь не найден", show_alert=True)
        await state.clear()
        return
    
    # Убеждаемся, что жалоба на другого игрока
    if reporter.id == reported_user.id:
        await callback.answer("Вы не можете пожаловаться на самого себя!", show_alert=True)
        await state.clear()
        return
    
    # Получаем рейтинг жалобуемого игрока
    reported_rating = await session.scalar(
        select(UserRating)
        .where(UserRating.user_id == reported_user.id)
    )
    
    if not reported_rating:
        reported_rating = UserRating(
            user_id=reported_user.id,
            faceit_nickname=reported_user.faceit_nickname,
            nickname_rating=50 - reason  # Начальный рейтинг минус штраф
        )
        session.add(reported_rating)
    else:
        reported_rating.nickname_rating -= reason
    
    # Создаем запись о жалобе
    new_report = UserReport(
        reporter_id=reporter.id,
        reported_user_id=reported_user.id,
        faceit_nickname=reported_user.faceit_nickname,
        reason=reason
    )
    session.add(new_report)
    
    # Проверяем, не достиг ли рейтинг порога бана
    if reported_rating.nickname_rating <= 0 and not reported_rating.is_banned:
        reported_rating.is_banned = True
        try:
            await callback.bot.send_message(
                chat_id=reported_user.tg_id,
                text="⚠️ Вы получили бан из-за низкого рейтинга!",
                reply_markup=kb.ban_notification("Низкий рейтинг")
            )
        except Exception as e:
            logger.error(f"Не удалось уведомить о бане: {e}")
    
    await session.commit()
    
    await callback.message.edit_text(
        f"✅ Жалоба на игрока {reported_user.faceit_nickname} отправлена!",
        reply_markup=None
    )
    await state.clear()
    await callback.answer()

@router.callback_query(F.data == 'cancel_report', ReportStates.waiting_for_reason)
async def cancel_report_handler(callback: CallbackQuery, state: FSMContext, bot: Bot, session: AsyncSession):
    await state.clear()
    await callback.message.edit_text(
        "Отправка репорта отменена",
        reply_markup=None
    )
    
    user = await session.scalar(
        select(User.is_vip).where(User.tg_id == callback.from_user.id))
    is_vip = user or False
    
    await bot.send_message(
        chat_id=callback.from_user.id,
        text="Выберите действие:",
        reply_markup=kb.get_main_keyboard(is_vip))
    
    await callback.answer()

@router.callback_query(F.data == 'cancel_report', ReportStates.waiting_for_nickname)
async def cancel_report_nickname_handler(callback: CallbackQuery, state: FSMContext, bot: Bot):
    await state.clear()
    await callback.message.edit_text(
        "Отправка репорта отменена",
        reply_markup=None
    )
    await bot.send_message(
        chat_id=callback.from_user.id,
        text="Выберите действие:",
        reply_markup=kb.get_main_keyboard()
    )
    await callback.answer()

@router.callback_query(F.data == 'cancel_unified_rating')
async def handle_cancel_unified_rating(callback: CallbackQuery, state: FSMContext, session: AsyncSession):
    await state.clear()
    user = await get_user_by_tg_id(session, callback.from_user.id)
    is_vip = user.is_vip if user else False
    
    await callback.message.edit_text(
        "❌ Оценка игрока отменена",
        reply_markup=None
    )
    
    await callback.message.answer(
        "Выберите действие:",
        reply_markup=kb.get_main_keyboard(is_vip)
    )
    await callback.answer()

@router.callback_query(F.data == 'appeal_ban')
async def start_appeal_process(callback: CallbackQuery, state: FSMContext):
    await callback.message.edit_text(
        "Вы начали процесс обжалования бана. Пожалуйста, укажите дату получения бана в формате ДД.ММ.ГГГГ:",
        reply_markup=kb.cancel_appeal()
    )
    await state.set_state(AppealStates.waiting_for_date)
    await callback.answer()

@router.message(AppealStates.waiting_for_date)
async def process_appeal_date(message: Message, state: FSMContext):
    try:
        date_obj = datetime.strptime(message.text, "%d.%m.%Y")
        await state.update_data(date_of_receipt=message.text)
        await message.answer(
            "Теперь опишите причину вашего несогласия с баном:",
            reply_markup=kb.cancel_appeal()
        )
        await state.set_state(AppealStates.waiting_for_description)
    except ValueError:
        await session.rollback() 
        await message.answer(
            "Неверный формат даты. Пожалуйста, укажите дату в формате ДД.ММ.ГГГГ (например, 15.05.2023)")

@router.message(AppealStates.waiting_for_description)
async def process_appeal_description(message: Message, state: FSMContext, session: AsyncSession):
    data = await state.get_data()
    
    try:
        new_appeal = Appeal(
            tg_id=message.from_user.id,
            date_of_receipt=data['date_of_receipt'],
            description=message.text,
            status='pending'
        )
        session.add(new_appeal)
        await session.commit()
        
        await message.answer(
            "✅ Ваше обжалование отправлено на рассмотрение. Мы свяжемся с вами в ближайшее время.",
            reply_markup=kb.get_main_keyboard()
        )
    except Exception as e:
        await session.rollback()
        logger.error(f"Ошибка при сохранении обжалования: {e}")
        await message.answer(
            "⚠️ Произошла ошибка при отправке обжалования. Попробуйте позже.",
            reply_markup=kb.get_main_keyboard()
        )
    finally:
        await state.clear()

@router.callback_query(F.data == 'cancel_appeal')
async def cancel_appeal_process(callback: CallbackQuery, state: FSMContext):
    await state.clear()
    await callback.message.edit_text(
        "Процесс обжалования отменен",
        reply_markup=None
    )
    await callback.answer()

@router.callback_query(F.data == 'ban_info')
async def show_ban_info(callback: CallbackQuery, session: AsyncSession):
    user = await session.scalar(
        select(UserRating)
        .join(User, User.id == UserRating.user_id)
        .where(User.tg_id == callback.from_user.id)
    )
    user_rating = user
    
    if user_rating and user_rating.is_banned:
        await callback.message.edit_text(
            "ℹ️ Информация о вашем бане:\n\n"
            f"• Текущий рейтинг: {user_rating.nickname_rating}\n"
            "• Причина: Низкий рейтинг (много жалоб от других игроков)\n\n"
            "Вы можете обжаловать это решение, если считаете его несправедливым.",
            reply_markup=kb.ban_notification("Низкий рейтинг")
        )
    else:
        await callback.answer("У вас нет активных банов", show_alert=True)
    await callback.answer()

@router.message(AppealStates.waiting_for_date)
async def process_appeal_date(message: Message, state: FSMContext):
    try:
        day, month, year = map(int, message.text.split('.'))
        if not (1 <= day <= 31 and 1 <= month <= 12 and 1900 <= year <= 2100):
            raise ValueError
        
        try:
            datetime.strptime(message.text, "%d.%m.%Y")
        except ValueError:
            raise ValueError("Несуществующая дата")
        
        await state.update_data(date_of_receipt=message.text)
        await message.answer(
            "Теперь опишите причину вашего несогласия с баном:",
            reply_markup=kb.cancel_appeal()
        )
        await state.set_state(AppealStates.waiting_for_description)
    except (ValueError, AttributeError, IndexError):
        await session.rollback() 
        await message.answer(
            "Неверный формат даты. Пожалуйста, укажите дату в формате ДД.ММ.ГГГГ (например, 15.05.2023)"
        )

async def check_vip_access(tg_id: int, session: AsyncSession) -> bool:
    result = await session.scalar(
        select(User.is_vip).where(User.tg_id == tg_id))
    return result or False

@router.message(F.text == '💎 Приобрести VIP')
async def handle_vip_command(message: Message, session: AsyncSession):
    user = await get_user_by_tg_id(session, message.from_user.id)
    if not user:
        await message.answer(
            "Сначала зарегистрируйтесь с помощью /start",
            reply_markup=kb.get_default_main_keyboard()
        )
        return
    
    await message.answer(
        "💎 VIP подписка предоставляет дополнительные возможности.\n\n"
        "Нажмите кнопку: ℹ️ Подробнее о VIP, чтобы узнать подробности.\n\n"
        "Выберите вариант подписки:",
        reply_markup=kb.vip_menu(user.is_vip)
    )

@router.message(F.text == '💎 VIP возможности')
async def show_vip_features(message: Message, session: AsyncSession):
    try:
        user = await get_user_by_tg_id(session, message.from_user.id)
        
        if not user:
            await message.answer("Сначала зарегистрируйтесь с помощью /start")
            return
            
        logging.info(f"VIP features requested for {message.from_user.id}: is_vip={user.is_vip}")
            
        vip_features = [
            "🎮 Преимущества VIP статуса:",
            "",
            "🔒 Бан-лист - исключайте нежелательных игроков из поиска",
            "📊 Диапазон ELO - настраивайте точность подбора тиммейтов",
            "🎂 Диапазон возраста - настройте подходящий возраст будущих тиммейтов", 
            "🔔 Приоритетная поддержка",
            "💎 Специальный значок в профиле"
        ]
    
        if user.is_vip:
            if user.vip_expires_at is None:
                vip_status = "Ваш VIP статус действует НАВСЕГДА 🎉"
            else:
                vip_status = f"Действует до: {user.vip_expires_at.strftime('%d.%m.%Y')}"
            
            vip_features.extend(["", vip_status])
        
        await message.answer(
            "\n".join(vip_features),
            reply_markup=kb.vip_menu(user.is_vip))
        
    except Exception as e:
        await session.rollback() 
        logging.error(f"Error in show_vip_features: {e}")
        await message.answer(
            "Произошла ошибка. Попробуйте позже.",
            reply_markup=kb.get_main_keyboard()
        )
    
@router.callback_query(F.data.startswith("confirm_payment_"))
async def check_payment(callback: CallbackQuery, session: AsyncSession, bot: Bot):
    payment_id = callback.data.split("_")[2]
    
    try:
        payment = await session.get(Payment, payment_id)
        if not payment:
            await callback.answer("Платеж не найден", show_alert=True)
            return
        
        async with httpx.AsyncClient(auth=(YOOMONEY_SHOP_ID, YOOMONEY_SECRET_KEY)) as client:
            response = await client.get(f"https://api.yookassa.ru/v3/payments/{payment.id}")
            data = response.json()
            
            if data['status'] == 'succeeded':
                user = await get_user_by_tg_id(session, payment.user_id)
                if user:
                    success = await activate_vip_subscription(
                        session=session,
                        user_id=user.id,
                        sub_type=payment.subscription_type
                    )
                    
                    if success:
                        await callback.message.edit_text(
                            "✅ VIP-статус успешно активирован!\n\nЕсли дополнительные пункты меню не отобразились, просьба прописать команду /start",
                            reply_markup=kb.back_to_main()
                        )
                        
                        await bot.send_message(
                            chat_id=callback.from_user.id,
                            text="Теперь вам доступны VIP-функции:",
                            reply_markup=kb.get_main_keyboard(is_vip=True))
                        
                    else:
                        await callback.answer("Ошибка активации VIP", show_alert=True)
            else:
                await callback.answer("Платеж не подтвержден", show_alert=True)
    except Exception as e:
        await session.rollback() 
        logger.error(f"Ошибка проверки платежа: {e}")
        await callback.answer("Ошибка при проверке платежа", show_alert=True)

@router.callback_query(F.data == "vip:info")
async def show_vip_info(callback: CallbackQuery, session: AsyncSession):
    user = await get_user_by_tg_id(session, callback.from_user.id)
    
    text = (
        "💎 VIP подписка предоставляет дополнительные возможности:\n\n"
        "• Ваш никнейм будет выделяться в поиске (💎 перед ником)\n"
        "• Настройка диапазона ELO для поиска (от ±50 до ±400)\n"
        "• Бан-лист до 5 игроков (исключает их из вашего поиска)\n"
        "• Настройка диапазона возраста для поиска (от 12 до 60)\n"
        "• Приоритетная поддержка\n\n"
    )
    
    if user and user.is_vip:
        if user.vip_expires_at is None:
            text += "✅ Ваша VIP подписка активна НАВСЕГДА!\n"
        else:
            text += f"✅ Ваша VIP подписка активна до {user.vip_expires_at.strftime('%d.%m.%Y')}\n"
    else:
        text += "Выберите вариант подписки:"
    
    await callback.message.edit_text(
        text,
        reply_markup=kb.vip_menu(user.is_vip if user else False)
    )
    await callback.answer()

@router.callback_query(F.data.startswith("vip:"))
async def handle_vip_purchase(callback: CallbackQuery, session: AsyncSession, bot: Bot):
    try:
        subscription_type = callback.data.split(":")[1]
        
        if not YOOMONEY_PROVIDER_TOKEN:
            await callback.answer("Платежи временно недоступны", show_alert=True)
            return
            
        # Получаем пользователя с явной загрузкой настроек
        user = await get_user_by_tg_id(session, callback.from_user.id)
        if not user:
            await callback.answer("Пользователь не найден", show_alert=True)
            return
        
        # Проверяем, что пользователь еще не VIP
        if user.is_vip:
            if user.vip_expires_at and user.vip_expires_at > datetime.utcnow():
                await callback.answer("У вас уже активна VIP подписка", show_alert=True)
                return
        
        # Получаем цену и готовим данные для чека
        amount = VIP_PRICES[subscription_type] * 100  # Переводим в копейки
        provider_data = PAYMENT_PROVIDER_DATA.copy()
        provider_data["receipt"]["items"][0]["amount"]["value"] = f"{VIP_PRICES[subscription_type]:.2f}"
        provider_data["receipt"]["items"][0]["description"] = f"VIP подписка ({subscription_type})"
        
        # Формируем инвойс
        title = f"💎 VIP подписка ({subscription_type})"
        description = "Доступ к премиум функциям бота"
        payload = f"{user.id}_{subscription_type}_{uuid.uuid4()}"
        
        # Если тестовый режим - сообщаем тестовые данные карты
        if YOOMONEY_PROVIDER_TOKEN.split(':')[1] == 'TEST':
            await callback.message.answer(
                "ⓘ Тестовый режим платежей\n"
                "Используйте тестовую карту:\n"
                "1111 1111 1111 1026\n"
                "12/22, CVC 000"
            )
        
        # Отправляем инвойс
        await bot.send_invoice(
            chat_id=callback.message.chat.id,
            title=title,
            description=description,
            payload=payload,
            provider_token=YOOMONEY_PROVIDER_TOKEN,
            currency=PAYMENT_CURRENCY,
            prices=[LabeledPrice(label=title, amount=amount)],
            provider_data=json.dumps(provider_data),
            need_email=True,
            send_email_to_provider=True,
            start_parameter=subscription_type
        )
        
    except KeyError:
        await session.rollback() 
        await callback.answer("Неизвестный тип подписки", show_alert=True)
    except Exception as e:
        logger.error(f"Ошибка создания платежа: {e}", exc_info=True)
        await callback.answer("Произошла ошибка при создании платежа", show_alert=True)

# Обработка предварительного запроса
@router.pre_checkout_query()
async def process_pre_checkout(pre_checkout_query: PreCheckoutQuery, bot: Bot):
    try:
        await bot.answer_pre_checkout_query(
            pre_checkout_query_id=pre_checkout_query.id,
            ok=True
        )
    except Exception as e:
        await session.rollback() 
        logger.error(f"Ошибка обработки pre_checkout: {e}")

# Обработка успешного платежа
@router.message(F.successful_payment)
async def process_successful_payment(message: Message, session: AsyncSession):
    try:
        payment = message.successful_payment
        logger.info(f"Получен успешный платеж: {payment}")
        
        # Разбираем payload: user_id_sub_type_uuid
        payload_parts = payment.invoice_payload.split('_')
        if len(payload_parts) < 3:
            logger.error(f"Неверный формат payload: {payment.invoice_payload}")
            await message.answer("⚠️ Ошибка обработки платежа. Обратитесь в поддержку.")
            return
            
        user_id = int(payload_parts[0])
        sub_type = payload_parts[1]
        
        logger.info(f"Активация VIP для user_id={user_id}, тип={sub_type}")
        
        # Активируем VIP
        success = await activate_vip_subscription(
            session=session,
            user_id=user_id,
            sub_type=sub_type
        )
        
        if success:
            # Обновляем данные пользователя для получения актуального состояния
            user_result = await session.execute(
                select(User)
                .where(User.id == user_id)
            )
            user = user_result.scalars().first()
            
            if user:
                await message.answer(
                    "✅ VIP подписка успешно активирована!\n\n"
                    "Теперь вам доступны все VIP-функции!",
                    reply_markup=kb.get_main_keyboard(is_vip=True)
                )
            else:
                await message.answer(
                    "✅ Платеж получен! VIP статус активирован.",
                    reply_markup=kb.get_main_keyboard(is_vip=True)
                )
        else:
            await message.answer(
                "⚠️ Ошибка активации VIP. Обратитесь в поддержку.",
                reply_markup=kb.get_main_keyboard()
            )
            
    except Exception as e:
        await session.rollback() 
        logger.error(f"Ошибка обработки успешного платежа: {e}", exc_info=True)
        await message.answer(
            "⚠️ Произошла ошибка при обработке платежа. Обратитесь в поддержку.",
            reply_markup=kb.get_main_keyboard()
        )

@router.callback_query(F.data.startswith("check_payment:"))
async def check_payment_status(callback: CallbackQuery, session: AsyncSession, bot: Bot):
    payment_id = callback.data.split(":")[1]
    try:
        # Находим платеж в базе
        payment = await session.get(Payment, payment_id)
        if not payment:
            await callback.answer("Платеж не найден", show_alert=True)
            return

        # Проверяем статус в ЮKassa
        async with httpx.AsyncClient(auth=(YOOMONEY_SHOP_ID, YOOMONEY_SECRET_KEY)) as client:
            response = await client.get(f"https://api.yookassa.ru/v3/payments/{payment_id}")
            data = response.json()

            if data['status'] == 'succeeded':
                # Активируем VIP
                user = await get_user_by_tg_id(session, callback.from_user.id)
                if not user:
                    await callback.answer("Пользователь не найден", show_alert=True)
                    return

                success = await activate_vip_subscription(
                    session=session,
                    user_id=user.id,
                    sub_type=payment.subscription_type
                )

                if success:
                    # Обновляем сообщение
                    await callback.message.edit_text(
                        "✅ VIP-статус успешно активирован!",
                        reply_markup=kb.back_to_main()
                    )
                    # Отправляем главное меню с VIP
                    await bot.send_message(
                        chat_id=callback.from_user.id,
                        text="Теперь вам доступны VIP-функции:",
                        reply_markup=kb.get_main_keyboard(is_vip=True))
                else:
                    await callback.answer("Ошибка активации VIP", show_alert=True)
            else:
                await callback.answer("Платеж не подтвержден", show_alert=True)
    except Exception as e:
        await session.rollback() 
        logger.error(f"Ошибка проверки платежа: {e}")
        await callback.answer("Ошибка при проверке платежа", show_alert=True)

@router.callback_query(F.data == 'back_to_vip')
async def back_to_vip_handler(callback: CallbackQuery):
    await callback.message.edit_text(
        "💎 VIP подписка предоставляет дополнительные возможности:\n\n"
        "• Ваш никнейм будет выделяться в поиске\n"
        "• Вы сможете задавать диапазон elo для поиска тиммейтов, через настройки профиля.\n"
        "• Вам будет доступен бан-лист (до 5-х играков).\n"
        "• Настройка диапазона возраста для поиска (от 12 до 60)\n"
        "• Приоритетная поддержка\n\n"
        "Выберите вариант подписки:",
        reply_markup=kb.vip_menu()
    )
    await callback.answer()

@router.message(F.text == '❓ Сообщить об ошибке')
async def report_error_start(message: Message, state: FSMContext):
    await message.answer(
        "Опишите проблему, с которой вы столкнулись:",
        reply_markup=kb.cancel_report_error()
    )
    await state.set_state(ErrorStates.waiting_for_error_description)

@router.message(ErrorStates.waiting_for_error_description)
async def process_error_report(message: Message, state: FSMContext, session: AsyncSession):
    logger.info(f"Handler 'process_error_report' triggered by user {message.from_user.id}")
    
    if message.text == '❌ Отменить':
        await state.clear()
        user = await get_user_by_tg_id(session, message.from_user.id)
        is_vip = user.is_vip if user else False
        await message.answer(
            "Сообщение об ошибке отменено",
            reply_markup=kb.get_main_keyboard(is_vip))
        
        return
    
    try:
        user = await session.scalar(
            select(User).where(User.tg_id == message.from_user.id))
        
        logger.debug(f"User found: {bool(user)}")
        
        if not user:
            await message.answer(
                "Сначала зарегистрируйтесь с помощью /start",
                reply_markup=kb.get_main_keyboard()
            )
            await state.clear()
            return
        
        new_error = UserError(
            tg_id=message.from_user.id,
            error=message.text[:500]
        )
        session.add(new_error)
        await session.commit()
        logger.info("Error report saved to database")
        
        await notify_admin(message.text, message.from_user.id, message.bot)
        
        await message.answer(
            "✅ Сообщение отправлено администратору. В ближайшие время поправим.",
            reply_markup=kb.get_main_keyboard(user.is_vip))
        
    except Exception as e:
        await session.rollback() 
        logger.error(f"Ошибка при сохранении отчета: {e}", exc_info=True)
        await session.rollback()
        is_vip = user.is_vip if user else False
        await message.answer(
            "⚠️ Произошла ошибка при отправке сообщения",
            reply_markup=kb.get_main_keyboard(is_vip))
        
    finally:
        await state.clear()
        logger.info("State cleared")

@router.message(F.text == '⚙️ Настройки поиска')
async def handle_search_settings(message: Message, session: AsyncSession):
    user = await get_user_by_tg_id(session, message.from_user.id)
    if not user or not user.is_vip:
        await message.answer("Эта функция доступна только VIP пользователям")
        return
    
    await message.answer(
        "⚙️ Настройки параметров поиска:",
        reply_markup=kb.search_settings_keyboard()
    )

@router.message(F.text == 'ℹ️ О нас')
async def about_us(message: Message):
    await message.answer('По вопросам сотрудничества обращаться по почте: faceit.team.bot.tg@gmail.com\n\nНаш тг канал: https://t.me/+ALI6nCGkpSgxNjgy')

@router.message(Command('admin'))
async def admin_panel(message: Message, session: AsyncSession):
    if message.from_user.id not in ADMINS:
        await message.answer("Доступ запрещен")
        return
    
    await message.answer(
        "⚙️ Панель администратора:",
        reply_markup=kb.admin_panel_keyboard()
    )

# Обработчики кнопок админ-панели
@router.callback_query(F.data == "create_broadcast")
async def start_broadcast(callback: CallbackQuery, state: FSMContext):
    if callback.from_user.id not in ADMINS:
        await callback.answer("Доступ запрещен")
        return
    
    await callback.message.edit_text(
        "Введите текст для рассылки:",
        reply_markup=kb.cancel_broadcast()
    )
    await state.set_state(AdminStates.waiting_for_broadcast_message)

@router.callback_query(F.data == "api_stats")
async def handle_api_stats_callback(callback: CallbackQuery, faceit_service: FaceitService):
    if callback.from_user.id not in ADMINS:
        await callback.answer("Доступ запрещен")
        return
    
    # Показываем статистику API
    stats = faceit_service.get_stats()
    
    response = (
        "📊 Статистика Faceit API:\n"
        f"• Всего запросов: {stats['total_requests']}\n"
        f"• Ошибок: {stats['error_count']}\n"
        f"• Ключей API: {stats['api_keys']}\n"
        f"• Размер кеша: {stats['cache_size']}\n"
        f"• Попаданий в кеш: {stats.get('cache_hits', 'N/A')}\n"
        f"• Промахов кеша: {stats.get('cache_misses', 'N/A')}\n"
        f"• Процент ошибок: {stats['error_count'] / stats['total_requests'] * 100 if stats['total_requests'] > 0 else 0:.2f}%\n\n"
        "ℹ️ Статистика сохраняется между перезапусками бота"
    )
    
    builder = InlineKeyboardBuilder()
    builder.button(text="🔄 Обновить", callback_data="refresh_api_stats")
    builder.button(text="🧹 Очистить кеш", callback_data="clear_api_cache")
    builder.button(text="📊 Детали", callback_data="api_stats_details")
    
    # Отправляем ответ в том же сообщении
    await callback.message.edit_text(
        response,
        reply_markup=builder.as_markup()
    )
    await callback.answer()

@router.callback_query(F.data == "send_to_user")
async def start_send_to_user(callback: CallbackQuery, state: FSMContext):
    if callback.from_user.id not in ADMINS:
        await callback.answer("Доступ запрещен")
        return
    
    await callback.message.edit_text(
        "📩 Отправьте сообщение пользователю в формате:\n\n"
        "<code>user_id текст сообщения</code>\n\n"
        "Пример:\n"
        "<code>123456789 Привет! Это тестовое сообщение от админа</code>",
        reply_markup=kb.cancel_broadcast(),
        parse_mode="HTML"
    )
    await state.set_state(AdminStates.waiting_for_user_message)  # Исправлено состояние
    await callback.answer()

# Обработка сообщения для пользователя
@router.message(AdminStates.waiting_for_user_message)
async def send_to_user_finish(message: Message, state: FSMContext, bot: Bot, session: AsyncSession = Depends(get_session)):
    try:
        # Удаляем возможные пробелы в начале
        clean_text = message.text.strip()
        
        # Разбиваем на части
        parts = clean_text.split(maxsplit=1)
        if len(parts) < 2:
            raise ValueError("Недостаточно частей сообщения")
            
        user_id = int(parts[0])
        text = parts[1]
        
        # Отправляем сообщение
        await bot.send_message(user_id, text)
        
        # Подтверждение админу
        await message.answer(
            f"✅ Сообщение отправлено пользователю {user_id}!\n\n"
            f"Текст: {text}",
            reply_markup=kb.admin_panel_keyboard()
        )
    except ValueError as e:
        if "Недостаточно частей" in str(e):
            await message.answer(
                "❌ Неверный формат. Используйте:\n\n"
                "<code>tg_id текст сообщения</code>\n\n"
                "Пример:\n"
                "<code>123456789 Привет!</code>",
                parse_mode="HTML",
                reply_markup=kb.admin_panel_keyboard()
            )
        else:
            await message.answer(
                f"❌ Ошибка в формате ID: {str(e)}",
                reply_markup=kb.admin_panel_keyboard()
            )
    except Exception as e:
        await message.answer(
            f"❌ Ошибка отправки: {str(e)}",
            reply_markup=kb.admin_panel_keyboard()
        )
    finally:
        await state.clear()

# Обработчики для статистики API
@router.callback_query(F.data == "api_stats")
async def handle_api_stats_callback(callback: CallbackQuery, faceit_service: FaceitService):
    if callback.from_user.id not in ADMINS:
        await callback.answer("Доступ запрещен")
        return
    
    # Показываем статистику API
    stats = faceit_service.get_stats()
    
    response = (
        "📊 Статистика Faceit API:\n"
        f"• Всего запросов: {stats['total_requests']}\n"
        f"• Ошибок: {stats['error_count']}\n"
        f"• Ключей API: {stats['api_keys']}\n"
        f"• Размер кеша: {stats['cache_size']}\n"
        f"• Попаданий в кеш: {stats.get('cache_hits', 'N/A')}\n"
        f"• Промахов кеша: {stats.get('cache_misses', 'N/A')}\n"
        f"• Процент ошибок: {stats['error_count'] / stats['total_requests'] * 100 if stats['total_requests'] > 0 else 0:.2f}%"
    )
    
    builder = InlineKeyboardBuilder()
    builder.button(text="🔄 Обновить", callback_data="refresh_api_stats")
    builder.button(text="🧹 Очистить кеш", callback_data="clear_api_cache")
    builder.button(text="📊 Детали", callback_data="api_stats_details")
    
    # Отправляем ответ в том же сообщении
    await callback.message.edit_text(
        response,
        reply_markup=builder.as_markup()
    )
    await callback.answer()

@router.callback_query(F.data == "refresh_api_stats")
async def refresh_api_stats(callback: CallbackQuery, faceit_service: FaceitService):
    await show_api_stats(callback.message, faceit_service)
    await callback.answer("Статистика обновлена")

@router.callback_query(F.data == "clear_api_cache")
async def clear_api_cache(callback: CallbackQuery, faceit_service: FaceitService):
    if hasattr(faceit_service, 'cache'):
        faceit_service.cache.clear()
    await callback.answer("Кеш очищен ✅")

@router.callback_query(F.data == "api_stats_details")
async def api_stats_details(callback: CallbackQuery, faceit_service: FaceitService):
    stats = faceit_service.get_stats()
    details = "🔍 Детальная статистика:\n"
    details += f"• Используемые ключи: {', '.join(faceit_service.api_keys[:3])}...\n"
    details += f"• Последние ошибки: {stats.get('last_errors', 'N/A')}"
    
    await callback.message.answer(details)
    await callback.answer()

# Обработчики рассылки
@router.message(AdminStates.waiting_for_broadcast_message)
async def process_broadcast_text(message: Message, state: FSMContext, session: AsyncSession, bot: Bot):
    try:
        await state.update_data(broadcast_text=message.text)
        await state.update_data(broadcast_text=message.text)
        await message.answer(
            f"✉️ Подтвердите рассылку:\n\n{message.text}",
            reply_markup=kb.confirm_broadcast_keyboard()
        )
    except Exception as e:
        logger.error(f"Ошибка подготовки рассылки: {e}", exc_info=True)
        await message.answer(
            "⚠️ Произошла ошибка при подготовке рассылки",
            reply_markup=kb.admin_panel_keyboard()
        )
        await state.clear()

@router.callback_query(F.data == "confirm_broadcast")
async def execute_broadcast(callback: CallbackQuery, state: FSMContext, session: AsyncSession, bot: Bot):
    data = await state.get_data()
    text = data['broadcast_text']
    
    users = await session.scalars(select(User))
    count = 0
    errors = 0
    failed_users = []
    
    for user in users:
        try:
            await bot.send_message(chat_id=user.tg_id, text=text)
            count += 1
        except Exception as e:
            errors += 1
            failed_users.append(user.tg_id)
            logger.error(f"Ошибка рассылки для {user.tg_id}: {e}")
    
    result_message = (
        f"✅ Рассылка завершена!\n\n"
        f"Доставлено: {count} пользователей\n"
        f"Ошибок: {errors}"
    )
    
    if errors > 0:
        result_message += f"\n\nНе удалось отправить: {', '.join(map(str, failed_users))}"
    
    await callback.message.edit_text(result_message)
    await state.clear()

@router.callback_query(F.data == "cancel_broadcast")
async def cancel_broadcast(callback: CallbackQuery, state: FSMContext):
    await state.clear()
    await callback.message.edit_text(
        "❌ Рассылка отменена",
        reply_markup=kb.admin_panel_keyboard()
    )

@router.callback_query(F.data == "user_stats")
async def show_user_stats(callback: CallbackQuery, session: AsyncSession):
    if callback.from_user.id not in ADMINS:
        await callback.answer("Доступ запрещен")
        return
    
    try:
        # Общее количество пользователей
        total_users = await session.scalar(select(func.count(User.id)))
        
        # Пользователи за последние 24 часа
        time_24h_ago = datetime.utcnow() - timedelta(hours=24)
        
        # Активные пользователи (кто выполнял любые действия) за последние 24 часа
        active_users_24h = await session.scalar(
            select(func.count(distinct(UserActivity.user_id)))
            .where(UserActivity.activity_time >= time_24h_ago)
        )
        
        # Новые пользователи за последние 24 часа
        new_users_24h = await session.scalar(
            select(func.count(User.id))
            .where(User.created_at >= time_24h_ago))
        
        # Пользователи, которые ищут команду прямо сейчас
        searching_users = await session.scalar(
            select(func.count(UserState.user_id))
            .where(UserState.search_team == True))
        
        # Самые частые действия
        popular_actions = await session.execute(
            select(
                UserActivity.activity_type,
                func.count(UserActivity.id).label('count')
            )
            .where(UserActivity.activity_time >= time_24h_ago)
            .group_by(UserActivity.activity_type)
            .order_by(desc('count'))
            .limit(5)
        )
        
        response = (
            "👥 Статистика пользователей:\n\n"
            f"• Всего пользователей: {total_users}\n"
            f"• Новых за 24 часа: {new_users_24h}\n"
            f"• Активных за 24 часа: {active_users_24h}\n"
            f"• Ищут команду прямо сейчас: {searching_users}\n\n"
            "🔥 Самые популярные действия:\n"
        )
        
        for i, (action, count) in enumerate(popular_actions, 1):
            response += f"{i}. {action}: {count}\n"
        
        # Добавляем временную метку, чтобы сообщение всегда было уникальным
        timestamp = datetime.now().strftime("%H:%M:%S")
        response += f"\n🕒 Обновлено: {timestamp}"
        
        await callback.message.edit_text(
            response,
            reply_markup=kb.admin_panel_keyboard()
        )
        
    except Exception as e:
        await session.rollback() 
        logger.error(f"Ошибка получения статистики пользователей: {e}", exc_info=True)
        await callback.answer("Ошибка при получении статистики", show_alert=True)

@router.callback_query(F.data == "detailed_user_stats")
async def show_detailed_user_stats(callback: CallbackQuery, session: AsyncSession):
    if callback.from_user.id not in ADMINS:
        await callback.answer("Доступ запрещен")
        return
    
    try:
        # Статистика по типам активности
        time_24h_ago = datetime.utcnow() - timedelta(hours=24)
        activity_stats = await session.execute(
            select(
                UserActivity.activity_type,
                func.count(UserActivity.id)
            )
            .where(UserActivity.activity_time >= time_24h_ago)
            .group_by(UserActivity.activity_type)
        )
        
        response = "📊 Детальная статистика активности:\n\n"
        for activity_type, count in activity_stats:
            response += f"• {activity_type}: {count}\n"
        
        # Самые активные пользователи
        top_active = await session.execute(
            select(
                User.faceit_nickname,
                func.count(UserActivity.id).label('activity_count')
            )
            .join(UserActivity, User.id == UserActivity.user_id)
            .where(UserActivity.activity_time >= time_24h_ago)
            .group_by(User.id)
            .order_by(desc('activity_count'))
            .limit(10)
        )
        
        response += "\n🏆 Топ активных пользователей:\n"
        for i, (nickname, count) in enumerate(top_active, 1):
            response += f"{i}. {nickname}: {count} действий\n"
        
        await callback.message.edit_text(
            response,
            reply_markup=kb.admin_panel_keyboard()
        )
    except Exception as e:
        await session.rollback() 
        logger.error(f"Ошибка детальной статистики: {e}")
        await callback.message.edit_text(
            "❌ Ошибка при получении детальной статистики",
            reply_markup=kb.admin_panel_keyboard()
        )
    finally:
        await callback.answer()

@router.callback_query(F.data == "api_history")
async def show_api_history(callback: CallbackQuery, session: AsyncSession):
    # Получаем последние 10 записей статистики
    result = await session.execute(
        select(APIServiceStats)
        .order_by(APIServiceStats.recorded_at.desc())
        .limit(10)
    )
    stats_list = result.scalars().all()
    
    response = "📊 История статистики API:\n\n"
    for stat in stats_list:
        response += (
            f"📅 {stat.recorded_at.strftime('%Y-%m-%d %H:%M')}\n"
            f"• Запросы: {stat.total_requests}\n"
            f"• Ошибки: {stat.error_count}\n"
            f"• Кеш: {stat.cache_size} (попаданий: {stat.cache_hits})\n\n"
        )
    
    await callback.message.edit_text(response)
    await callback.answer()

@router.message(Command("id"))
async def get_user_id(message: Message):
    await message.answer(
        f"👤 Ваш Telegram ID: `{message.from_user.id}`\n\n"
        "Этот номер может понадобиться администратору для оказания помощи",
        parse_mode="Markdown"
    )

async def start_scheduler():
    """Функция для запуска планировщика задач"""
    # Запускаем каждый день в 3:00
    scheduler.add_job(
        cleanup_task,
        trigger="cron",
        hour=3,
        minute=0,
        id="daily_cleanup"
    )
    if not scheduler.running:
        scheduler.start()
        logger.info("Планировщик задач запущен")

async def cleanup_task():
    """Задача для очистки, которая будет запускаться по расписанию"""
    try:
        logger.info("Запуск ежедневной очистки неактивных пользователей...")
        async with async_session_maker() as session:
            deleted = await delete_unfinished_users(session)
            logger.info(f"Очистка завершена. Удалено: {len(deleted)} пользователей")
    except Exception as e:
        logger.error(f"Ошибка в задаче очистки: {e}", exc_info=True)

# Инициализация планировщика
async def initialize():
    await start_scheduler()

# Создаем функцию для запуска инициализации, которая будет вызвана из main.py
def setup_handlers():
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    loop.run_until_complete(initialize())
    loop.close()

@router.message()
async def catch_all(message: Message):
    logger.info(f"Получено необработанное сообщение: '{message.text}' | chat: {message.chat.id}")
    await message.answer("Пожалуйста, используйте кнопки меню для навигации")