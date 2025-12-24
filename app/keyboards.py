from aiogram.types import (ReplyKeyboardMarkup, KeyboardButton, 
                           InlineKeyboardMarkup, InlineKeyboardButton)

from aiogram.utils.keyboard import ReplyKeyboardBuilder, InlineKeyboardBuilder
from database.models import UserState 

import logging

logger = logging.getLogger(__name__)

def get_main_keyboard(is_vip: bool = False) -> ReplyKeyboardMarkup:
    builder = ReplyKeyboardBuilder()
    
    builder.row(
        KeyboardButton(text='🔍 Поиск тиммейтов'),
        KeyboardButton(text='⭐️ Оценить игрока')
    )
    
    # VIP-специфичные кнопки
    if is_vip:
        logging.info(f"Creating VIP keyboard for user")
        builder.row(
            KeyboardButton(text='🔒 Бан-лист'),
            KeyboardButton(text='⚙️ Настройки поиска')  # Новая кнопка
        )   
    
    # Остальные кнопки без изменений...
    builder.row(KeyboardButton(text='📊 Мои данные'))
    builder.row(KeyboardButton(text='⚙️ Настройки профиля'))
    builder.row(KeyboardButton(text='💎 VIP возможности'))
    builder.row(
        KeyboardButton(text='❓ Сообщить об ошибке'),
        KeyboardButton(text='ℹ️ О нас')
    )
    
    return builder.as_markup(
        resize_keyboard=True,
        input_field_placeholder='Выберите пункт меню...'
    )

def get_default_main_keyboard() -> ReplyKeyboardMarkup:
    """Клавиатура по умолчанию (без VIP функций)"""
    return get_main_keyboard(is_vip=False)


def cancel_registration() -> ReplyKeyboardMarkup:
    builder = ReplyKeyboardBuilder()
    builder.button(text="❌ Отменить регистрацию")
    return builder.as_markup(resize_keyboard=True, one_time_keyboard=True)


def search_results():
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text='📨 Пригласить всех', callback_data='invite_all')],
            [InlineKeyboardButton(text='🔄 Новый поиск', callback_data='new_search')]
        ])

def profile_settings(user_state: UserState) -> InlineKeyboardMarkup:
    verification_text = "✅ Статус верификации" if user_state.is_verified is not None else "❌ Статус верификации"
    role_text = "✅ Моя роль в команде" if user_state.role else "❌ Моя роль в команде"
    search_text = "✅ Статус поиска" if user_state.search_team is not None else "❌ Статус поиска"
    comm_text = "✅ Способ коммуникации" if user_state.communication_method else "❌ Способ коммуникации"
    tz_text = "✅ Часовой пояс" if user_state.timezone else "❌ Часовой пояс"
    
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text=verification_text, callback_data="verification_status")],
        [InlineKeyboardButton(text=role_text, callback_data="team_role_settings")],
        [InlineKeyboardButton(text=search_text, callback_data="search_status_settings")],
        [InlineKeyboardButton(text=comm_text, callback_data="communication_settings")],
        [InlineKeyboardButton(text=tz_text, callback_data="timezone_settings")],
        [InlineKeyboardButton(text="⬅️ Назад в меню", callback_data="back_to_main_menu")]
    ])

def team_role_settings():
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text='in-Game Leader (IGL)', callback_data='role_igl')],
            [InlineKeyboardButton(text='Опорник', callback_data='role_support')],
            [InlineKeyboardButton(text='Support/Lurker', callback_data='role_support_lurker')],
            [InlineKeyboardButton(text='AWPer', callback_data='role_awper')],
            [InlineKeyboardButton(text='Entry Fragger', callback_data='role_entry')],
            [InlineKeyboardButton(text='⬅️ Назад', callback_data='profile_settings')]])


def search_status_settings():
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text='✅ Да', callback_data='yes_status')],
            [InlineKeyboardButton(text='❌ Нет', callback_data='no_status')],
            [InlineKeyboardButton(text='⬅️ Назад', callback_data='profile_settings')]])


def verification_status_keyboard():
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text='✅ Да', callback_data='verification_yes')],
            [InlineKeyboardButton(text='❌ Нет', callback_data='verification_no')],
            [InlineKeyboardButton(text='⬅️ Назад', callback_data='profile_settings')]])


def help_report_an_error():
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text='Описание ошибки', callback_data='error_description')]])


def report_user_player():
    """Клавиатура для начала процесса репорта"""
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text='📝 Ввести никнейм', callback_data='input_faceit_nickname')],
            [InlineKeyboardButton(text='❌ Отмена', callback_data='cancel_report')]])

def cancel_report():
    """Клавиатура только с кнопкой отмены (используется при вводе никнейма)"""
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text='❌ Отмена', callback_data='cancel_report')]])


def back_to_report_menu():
    """Клавиатура для возврата в меню репорта (если потребуется)"""
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text='⬅️ Назад', callback_data='report_user_player')],
            [InlineKeyboardButton(text='❌ Отмена', callback_data='cancel_report')]])


def ban_notification(reason: str):
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text='📝 Обжаловать', callback_data='appeal_ban')],
            [InlineKeyboardButton(text='ℹ️ Подробнее', callback_data='ban_info')]])


def cancel_appeal():
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text='❌ Отменить обжалование', callback_data='cancel_appeal')]])


def vip_payment_keyboard(payment_id: str) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text="🔗 Перейти к оплате", url=f"https://yoomoney.ru/checkout/payments/v2/contract/{payment_id}")],
            [InlineKeyboardButton(text="✅ Я оплатил", callback_data=f"confirm_payment_{payment_id}")],  # Изменено имя callback
            [InlineKeyboardButton(text="⬅️ Назад", callback_data="back_to_vip")]])

def vip_menu(is_vip: bool = False) -> InlineKeyboardMarkup:
    buttons = []
    
    if not is_vip:
        buttons.extend([
            [InlineKeyboardButton(text="💎 1 месяц - 149₽", callback_data="vip:month")],
            [InlineKeyboardButton(text="💎 3 месяца - 399₽", callback_data="vip:3month")],
            [InlineKeyboardButton(text="💎 12 месяцев - 999₽", callback_data="vip:year")],
            [InlineKeyboardButton(text="💎 Навсегда - 4990₽", callback_data="vip:permanent")]
        ])
    
    buttons.append([InlineKeyboardButton(text="ℹ️ Подробнее о VIP", callback_data="vip:info")])
    buttons.append([InlineKeyboardButton(text="⬅️ Назад в меню", callback_data="back_to_main_menu")])
    
    return InlineKeyboardMarkup(inline_keyboard=buttons)


def back_to_main():
    return ReplyKeyboardMarkup(
        keyboard=[[KeyboardButton(text='Главное меню')]],
        resize_keyboard=True)


def back_to_vip():
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text="⬅️ Назад к VIP", callback_data="vip_info")]])

def settings_keyboard():
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="Диапазон elo", callback_data="set_elo_range")],
        [InlineKeyboardButton(text="Диапазон возраста", callback_data="set_age_range")],
        [InlineKeyboardButton(text="⬅️ Назад в меню", callback_data="back_to_vip_menu")]
    ])

def age_range_keyboard():
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="🎂 12-15 лет", callback_data="age_12_15")],
        [InlineKeyboardButton(text="🎂 15-20 лет", callback_data="age_15_20")],
        [InlineKeyboardButton(text="🎂 20-25 лет", callback_data="age_20_25")],
        [InlineKeyboardButton(text="🎂 25-30 лет", callback_data="age_25_30")],
        [InlineKeyboardButton(text="🎂 30-35 лет", callback_data="age_30_35")],
        [InlineKeyboardButton(text="🎂 12-60 лет (обычный)", callback_data="age_12_60")],
        [InlineKeyboardButton(text="⬅️ Назад", callback_data="back_to_search_settings")]
    ])

def elo_range_keyboard():
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="📊 ±50", callback_data="elo_50")],
        [InlineKeyboardButton(text="📊 ±100", callback_data="elo_100")],
        [InlineKeyboardButton(text="📊 ±200", callback_data="elo_200")],
        [InlineKeyboardButton(text="📊 ±300", callback_data="elo_300")],
        [InlineKeyboardButton(text="📊 ±400", callback_data="elo_400")],
        [InlineKeyboardButton(text="⬅️ Назад", callback_data="back_to_search_settings")]
    ])

def communication_settings_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text='DS', callback_data='comm_ds')],
        [InlineKeyboardButton(text='TS', callback_data='comm_ts')],
        [InlineKeyboardButton(text='DS/TS', callback_data='comm_ds_ts')],
        [InlineKeyboardButton(text='В игре', callback_data='comm_ingame')],
        [InlineKeyboardButton(text='⬅️ Назад', callback_data='profile_settings')]
    ])

def timezone_settings_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text='MSK-1 (UTC+2)', callback_data='tz_msk_minus1')],
        [InlineKeyboardButton(text='MSK+0 (UTC+3)', callback_data='tz_msk_plus0')], 
        [InlineKeyboardButton(text='MSK+1 (UTC+4)', callback_data='tz_msk_plus1')],
        [InlineKeyboardButton(text='MSK+2 (UTC+5)', callback_data='tz_msk_plus2')],
        [InlineKeyboardButton(text='MSK+3 (UTC+6)', callback_data='tz_msk_plus3')],
        [InlineKeyboardButton(text='MSK+4 (UTC+7)', callback_data='tz_msk_plus4')],
        [InlineKeyboardButton(text='MSK+5 (UTC+8)', callback_data='tz_msk_plus5')],
        [InlineKeyboardButton(text='MSK+6 (UTC+9)', callback_data='tz_msk_plus6')],
        [InlineKeyboardButton(text='MSK+7 (UTC+10)', callback_data='tz_msk_plus7')],
        [InlineKeyboardButton(text='MSK+8 (UTC+11)', callback_data='tz_msk_plus8')],
        [InlineKeyboardButton(text='MSK+9 (UTC+12)', callback_data='tz_msk_plus9')],
        [InlineKeyboardButton(text='MSK+10 (UTC+13)', callback_data='tz_msk_plus10')],
        [InlineKeyboardButton(text='⬅️ Назад', callback_data='profile_settings')]
    ])

def ban_list_management_keyboard(bans: list) -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    
    for ban in bans:
        builder.button(
            text=f"❌ Удалить {ban.banned_nickname}",
            callback_data=f"remove_ban_{ban.id}"
        )
    
    if len(bans) < 5:
        builder.button(text='➕ Добавить игрока', callback_data='add_to_ban_list')
    
    # Изменено: кнопка "Назад в меню" вместо "Назад в бан-лист"
    builder.button(text='⬅️ Назад в меню', callback_data='back_to_main_menu')
    
    builder.adjust(1)
    return builder.as_markup()

def back_to_ban_list():
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text="⬅️ Назад к бан-листу", callback_data="back_to_ban_list")]
        ]
    )

def unified_rating_options() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text='⚠️ Пожаловаться', callback_data='unified_report')],
            [InlineKeyboardButton(text='👍 Похвалить', callback_data='unified_praise')],
            [InlineKeyboardButton(text='❌ Отмена', callback_data='unified_cancel')]
        ])

def report_reasons_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text='👻 Смурф', callback_data='report_reason:3')],
            [InlineKeyboardButton(text='🤬 Оскорбления', callback_data='report_reason:2')],
            [InlineKeyboardButton(text='😈 Грифинг', callback_data='report_reason:2')],
            [InlineKeyboardButton(text='❌ Отмена', callback_data='back_to_main_menu')]
        ])

def praise_reasons_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text='👥 За командную игру', callback_data='praise_reason:2')],
            [InlineKeyboardButton(text='🎯 За индивидуальный скилл', callback_data='praise_reason:2')],
            [InlineKeyboardButton(text='🤝 За дружественную атмосферу', callback_data='praise_reason:2')],
            [InlineKeyboardButton(text='❌ Отмена', callback_data='back_to_main_menu')]
        ])

def search_settings_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="📊 Диапазон ELO", callback_data="set_elo_range")],
        [InlineKeyboardButton(text="🎂 Диапазон возраста", callback_data="set_age_range")],
        [InlineKeyboardButton(text="⬅️ Назад в меню", callback_data="back_to_main_menu")]
    ])

def cancel_ban_list_keyboard() -> InlineKeyboardMarkup:
    """Клавиатура только с кнопкой отмены"""
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text='❌ Отмена', callback_data='cancel_ban_list')]
        ])

def cancel_ban_list_input() -> InlineKeyboardMarkup:
    """Клавиатура для отмены ввода в бан-лист"""
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text='❌ Отмена', callback_data='cancel_ban_list')]
        ])

def cancel_unified_rating_keyboard():
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text='❌ Отменить оценку', callback_data='cancel_unified_rating')]
    ])

def cancel_report_error() -> ReplyKeyboardMarkup:
    return ReplyKeyboardMarkup(
        keyboard=[[KeyboardButton(text='❌ Отменить')]],
        resize_keyboard=True,
        one_time_keyboard=True
    )

def invite_player_keyboard(player_id: int) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(text="✅ Принять", callback_data=f"accept_invite_{player_id}"),
                InlineKeyboardButton(text="❌ Отклонить", callback_data=f"decline_invite_{player_id}")
            ]
        ]
    )

def about_us():
    return InlineKeyboardMarkup(inline_keyboard=[])

def admin_panel_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="✉️ Создать рассылку", callback_data="create_broadcast")],
        [InlineKeyboardButton(text="⬅️ В главное меню", callback_data="back_to_main_menu")]
    ])

def cancel_broadcast() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="❌ Отменить", callback_data="cancel_broadcast")]
    ])

def confirm_broadcast_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [
            InlineKeyboardButton(text="✅ Подтвердить", callback_data="confirm_broadcast"),
            InlineKeyboardButton(text="❌ Отменить", callback_data="cancel_broadcast")
        ]
    ])

def consent_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="✅ Принять", callback_data="consent_accept")],
        [InlineKeyboardButton(text="❌ Отклонить", callback_data="consent_reject")]
    ])

def admin_panel_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="✉️ Создать рассылку", callback_data="create_broadcast")],
        [InlineKeyboardButton(text="📊 Статистика API", callback_data="api_stats")],
        [InlineKeyboardButton(text="✉️ Отправить сообщение", callback_data="send_to_user")],
        [InlineKeyboardButton(text="👥 Статистика пользователей", callback_data="user_stats")],  # Новая кнопка
        [InlineKeyboardButton(text="⬅️ В главное меню", callback_data="back_to_main_menu")]
    ])