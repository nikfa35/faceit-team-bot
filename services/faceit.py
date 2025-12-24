import aiohttp
import asyncio
import logging
import time
import os
import json
from typing import Optional, Dict, Any, List, Tuple
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, delete, or_, and_
from cachetools import TTLCache
from collections import defaultdict
from datetime import datetime

from database.models import APIServiceStats, User, UserState, UserRating, UserActivity

logger = logging.getLogger(__name__)

class FaceitService:
    def __init__(self, session_pool, api_keys: Optional[List[str]] = None, cache_ttl: int = 3600, maxsize: int = 1000):
        self.session_pool = session_pool
        self.session = None 
        # Загружаем ключи из переменной окружения, если не переданы явно
        if api_keys is None:
            env_keys = os.getenv("FACEIT_API_KEYS", "")
            api_keys = [key.strip() for key in env_keys.split(',') if key.strip()]
        
        self.api_keys = api_keys
        
        if not self.api_keys:
            logger.warning("No Faceit API keys provided in environment variables!")
            self.api_keys = [""]  # Защита от пустого списка
            
        # Статистика использования ключей
        self.key_usage = {key: {"requests": 0, "errors": 0, "last_used": 0} for key in self.api_keys}
        self.current_key_index = 0
        
        # Кеширование
        self.cache = TTLCache(maxsize=maxsize, ttl=cache_ttl)
        self.cache_stats = defaultdict(int)
        
        # Общая статистика
        self.total_requests = 0
        self.error_count = 0
        self.last_errors = []
        self.request_timestamps = []
        self.request_lock = asyncio.Lock()

        # Атрибуты для загрузки/сохранения статистики
        self.requests_last_hour = 0
        self.avg_response_time = 0.0
        self.last_error = None
        self.cache_hits = 0
        self.cache_misses = 0
        self.cache_size = 0
        self.cache_hit_rate = 0.0
    
    async def initialize(self):
        """Инициализирует сервис, загружая статистику из БД"""
        async with self.session_pool() as session:
            await self.load_stats(session)

    async def load_stats(self, session: AsyncSession):
        """Загружает статистику из базы данных"""
        try:
            # Получаем последнюю запись статистики
            result = await session.execute(
                select(APIServiceStats)
                .order_by(APIServiceStats.recorded_at.desc())
                .limit(1)
            )
            stats_record = result.scalar_one_or_none()
        
            if not stats_record:
                logger.info("Статистика не найдена, используется состояние по умолчанию")
                return
            
            # Десериализуем данные
            stats = {
                'total_requests': stats_record.total_requests,
                'error_count': stats_record.error_count,
                'cache_size': stats_record.cache_size,
                'cache_hits': stats_record.cache_hits,
                'cache_misses': stats_record.cache_misses,
                'cache_hit_rate': stats_record.cache_hit_rate,
                'requests_last_hour': stats_record.requests_last_hour,
                'avg_response_time': stats_record.avg_response_time,
                'last_error': stats_record.last_error
            }
        
            # Обрабатываем key_stats как список словарей
            key_stats = []
            if stats_record.key_stats:
                try:
                    key_stats = json.loads(stats_record.key_stats)
                    if not isinstance(key_stats, list):
                        key_stats = []
                except json.JSONDecodeError:
                    logger.error("Ошибка декодирования key_stats")
                    key_stats = []
        
            stats['key_stats'] = key_stats
        
            # Обновляем состояние сервиса
            self.total_requests = stats['total_requests']
            self.error_count = stats['error_count']
            self.cache_size = stats['cache_size']
            self.cache_hits = stats['cache_hits']
            self.cache_misses = stats['cache_misses']
            self.cache_hit_rate = stats['cache_hit_rate']
            self.requests_last_hour = stats['requests_last_hour']
            self.avg_response_time = stats['avg_response_time']
            self.last_error = stats['last_error']
            self.key_stats = stats['key_stats']
        
            logger.info(f"Статистика загружена: {stats}")
        except Exception as e:
            logger.error(f"Error loading API stats: {e}", exc_info=True)

    async def save_stats(self, session: AsyncSession):
        """Сохраняет статистику в базу данных"""
        try:
            # Рассчитываем процент попаданий в кеш
            total_cache = self.cache_hits + self.cache_misses
            cache_hit_rate = self.cache_hits / total_cache if total_cache > 0 else 0.0
            
            # Подготавливаем статистику по ключам
            key_stats = []
            for key, usage in self.key_usage.items():
                key_stats.append({
                    'key': key,
                    'requests': usage['requests'],
                    'errors': usage['errors'],
                    'last_used': datetime.fromtimestamp(usage['last_used']).isoformat() if usage['last_used'] else None
                })
            
            # Сериализуем key_stats в JSON
            key_stats_json = json.dumps(key_stats)
            
            # Создаем новую запись статистики
            new_stats = APIServiceStats(
                total_requests=self.total_requests,
                error_count=self.error_count,
                cache_size=len(self.cache),
                cache_hits=self.cache_stats['hits'],
                cache_misses=self.cache_stats['misses'],
                cache_hit_rate=cache_hit_rate,
                requests_last_hour=self.requests_last_hour,
                avg_response_time=self.avg_response_time,
                last_error=self.last_error,
                key_stats=key_stats_json
            )
            
            session.add(new_stats)
            await session.commit()
            logger.info(f"Статистика сохранена: {new_stats}")
        except Exception as e:
            logger.error(f"Error saving API stats: {e}", exc_info=True)
            try:
                await session.rollback()
            except:
                pass
    
    def get_stats(self) -> Dict[str, Any]:
        """Возвращает текущую статистику сервиса"""
        now = time.time()
        
        # Статистика за последний час
        last_hour_requests = [
            t for t in self.request_timestamps 
            if t[0] > now - 3600
        ]
        
        # Расчет времени ответа
        avg_response_time = (
            sum(t[1] for t in last_hour_requests) / len(last_hour_requests) 
            if last_hour_requests else 0
        )
        
        # Расчет процента попаданий в кеш с защитой
        cache_hits = self.cache_stats.get('hits', 0)
        cache_misses = self.cache_stats.get('misses', 0)
        total_cache = cache_hits + cache_misses
        cache_hit_rate = (
            cache_hits / total_cache 
            if total_cache > 0 else 0
        )
        
        # Статистика по ключам с защитой
        key_stats = []
        for key, data in self.key_usage.items():
            key_stats.append({
                "key": f"{key[:5]}...{key[-5:]}",
                "requests": data.get("requests", 0),
                "errors": data.get("errors", 0),
                "last_used": time.strftime("%H:%M:%S", time.localtime(data.get("last_used", 0)))
            })
        
        # Формирование статистики с защитой от отсутствующих ключей
        return {
            "total_requests": self.total_requests,
            "error_count": self.error_count,
            "api_keys": len(self.api_keys),
            "cache_size": len(self.cache),
            "cache_hits": cache_hits,
            "cache_misses": cache_misses,
            "cache_hit_rate": cache_hit_rate,
            "requests_last_hour": len(last_hour_requests),
            "avg_response_time": avg_response_time,
            "last_error": self.last_errors[-1] if self.last_errors else None,
            "key_stats": key_stats
        }
    
    async def close(self):
        try:
            if self.session_pool:
                async with self.session_pool() as session:
                    await self.save_stats(session)
                
            if self.session is not None and not self.session.closed:
                await self.session.close()
                
        except Exception as e:
            logger.error(f"Error closing FaceitService: {e}")
    
    def _get_best_key(self) -> str:
        """Выбирает лучший доступный ключ с интеллектуальной ротацией"""
        if len(self.api_keys) == 1:
            return self.api_keys[0]
            
        now = time.time()
        
        # Ищем ключ с наименьшим количеством запросов в последний час
        least_used = min(
            self.key_usage.items(),
            key=lambda x: x[1]["requests"]
        )[0]
        
        # Если есть ключ, который не использовался более 5 минут - выбираем его
        for key, data in self.key_usage.items():
            if now - data["last_used"] > 300:  # 5 минут
                return key
                
        return least_used
    
    def _update_key_stats(self, key: str, success: bool = True):
        """Обновляет статистику использования ключа"""
        if key not in self.key_usage:
            self.key_usage[key] = {"requests": 0, "errors": 0, "last_used": 0}
            
        self.key_usage[key]["requests"] += 1
        self.key_usage[key]["last_used"] = time.time()
        
        if not success:
            self.key_usage[key]["errors"] += 1
    
    async def _make_request(self, url: str) -> Dict[str, Any]:
        """Выполняет HTTP-запрос к Faceit API"""
        start_time = time.time()
        
        if self.session is None or (hasattr(self.session, 'closed')) and self.session.closed:
            self.session = aiohttp.ClientSession()
        
        selected_key = self._get_best_key()
        headers = {
            "Authorization": f"Bearer {selected_key}",
            "Accept": "application/json"
        }
        
        try:
            async with self.session.get(url, headers=headers) as response:
                self.total_requests += 1
                
                if response.status == 429:
                    logger.warning(f"Rate limit exceeded for key {selected_key[:5]}...{selected_key[-5:]}")
                    self._update_key_stats(selected_key, success=False)
                    
                    for retry_key in self.api_keys:
                        if retry_key == selected_key:
                            continue
                            
                        headers["Authorization"] = f"Bearer {retry_key}"
                        async with self.session.get(url, headers=headers) as retry_response:
                            if retry_response.status != 429:
                                self._update_key_stats(retry_key)
                                retry_response.raise_for_status()
                                return await retry_response.json()
                    
                    raise Exception("All API keys rate limited")
                
                response.raise_for_status()
                self._update_key_stats(selected_key)
                return await response.json()
                
        except Exception as e:
            self.error_count += 1
            error_msg = f"Request to {url} failed: {str(e)}"
            self.last_errors.append(error_msg)
            self._update_key_stats(selected_key, success=False)
            
            if len(self.last_errors) > 10:
                self.last_errors.pop(0)
                
            logger.error(error_msg, exc_info=True)
            return {}
        finally:
            duration = time.time() - start_time
            self.request_timestamps.append((start_time, duration))
            
            if len(self.request_timestamps) > 1000:
                self.request_timestamps.pop(0)

    # Новые методы для работы с пользователями и проверки аккаунтов
    
    async def check_account_exists(self, nickname: str) -> bool:
        """Проверяет существование аккаунта Faceit"""
        try:
            url = f"https://open.faceit.com/data/v4/players?nickname={nickname}"
            async with self.request_lock:
                response = await self._make_request(url)
            
            return 'player_id' in response
        except Exception as e:
            logger.error(f"Error checking Faceit account: {e}")
            return False
    
    async def delete_user_completely(self, session: AsyncSession, user_id: int) -> bool:
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
    
    async def cleanup_incomplete_users(self, session: AsyncSession) -> int:
        """Очистка незавершенных регистраций"""
        try:
            incomplete_users = await session.execute(
                select(User).where(
                    or_(
                        User.faceit_nickname == None,
                        User.age == None,
                        User.tg_id == None
                    )
                )
            )
            
            deleted_count = 0
            for user in incomplete_users.scalars():
                if await self.delete_user_completely(session, user.id):
                    deleted_count += 1
            
            await session.commit()
            logger.info(f"Удалено {deleted_count} незавершенных регистраций")
            return deleted_count
        except Exception as e:
            await session.rollback()
            logger.error(f"Ошибка при очистке незавершенных регистраций: {e}")
            return 0
    
    async def search_teammates(self, session: AsyncSession, tg_id: int) -> List[Tuple[User, UserState, UserRating]]:
        """Поиск команд с проверкой на валидность пользователей"""
        current_user = await session.execute(
            select(User).where(User.tg_id == tg_id)
        )
        current_user = current_user.scalar()
        
        if not current_user:
            return []
        
        teammates = await session.execute(
            select(User, UserState, UserRating)
            .join(UserState, User.id == UserState.user_id)
            .join(UserRating, User.id == UserRating.user_id)
            .where(
                and_(
                    User.id != current_user.id,
                    User.faceit_nickname != None,
                    User.age != None,
                    UserState.elo != None,
                )
            )
            .order_by(func.random())
            .limit(20)
        )
        
        return teammates.all()

    # Существующие методы API
    
    async def get_player_stats(self, nickname: str) -> Dict[str, Any]:
        """Получает статистику игрока по никнейму"""
        if nickname in self.cache:
            self.cache_stats['hits'] += 1
            return self.cache[nickname]
        else:
            self.cache_stats['misses'] += 1
        
        player_url = f"https://open.faceit.com/data/v4/players?nickname={nickname}"
        
        async with self.request_lock:
            player_data = await self._make_request(player_url)
        
        if not player_data or 'player_id' not in player_data:
            logger.error(f"Failed to get player data for {nickname}")
            return {}
        
        player_id = player_data['player_id']
        
        stats_url = f"https://open.faceit.com/data/v4/players/{player_id}/stats/cs2"
        
        async with self.request_lock:
            stats_data = await self._make_request(stats_url)
        
        result = {
            **player_data,
            "player_id": player_id,
            "cs2_stats": stats_data.get('lifetime', {}) if stats_data else {}
        }
        
        if 'games' in player_data and 'cs2' in player_data['games']:
            result['faceit_elo'] = player_data['games']['cs2'].get('faceit_elo')
        elif 'games' in player_data and 'csgo' in player_data['games']:
            result['faceit_elo'] = player_data['games']['csgo'].get('faceit_elo')
        
        self.cache[nickname] = result
        
        return result

    async def get_player_info(self, player_id: str) -> Dict[str, Any]:
        """Получает основную информацию об игроке по ID"""
        url = f"https://open.faceit.com/data/v4/players/{player_id}"
        async with self.request_lock:
            return await self._make_request(url)

    async def get_player_history(self, player_id: str, limit: int = 20) -> Dict[str, Any]:
        """Получает историю матчей игрока"""
        url = f"https://open.faceit.com/data/v4/players/{player_id}/history?game=cs2&limit={limit}"
        async with self.request_lock:
            return await self._make_request(url)

    async def get_match_stats(self, match_id: str) -> Dict[str, Any]:
        """Получает статистику матча"""
        url = f"https://open.faceit.com/data/v4/matches/{match_id}/stats"
        async with self.request_lock:
            return await self._make_request(url)
    
    async def refresh_cache(self):
        """Очищает кеш сервиса"""
        self.cache.clear()
        self.cache_stats = defaultdict(int)
        logger.info("FaceitService cache cleared")