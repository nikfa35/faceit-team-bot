from sqlalchemy import Column, Index, Integer, BigInteger, String, DateTime, ForeignKey, Boolean, Float, Text, func
from sqlalchemy.orm import relationship
from sqlalchemy.ext.hybrid import hybrid_property
from datetime import datetime

# Импортируем единую Base из base.py
from database.base import Base

class User(Base):
    __tablename__ = 'users'
    __table_args__ = (
        Index('ix_users_faceit_nickname_lower', func.lower(func.trim('faceit_nickname'))),
    )
    
    id = Column(Integer, primary_key=True, autoincrement=True)
    tg_id = Column(BigInteger, unique=True, nullable=False)
    tg_username = Column(String(50), nullable=True)
    faceit_nickname = Column(String(50), unique=True)
    faceit_player_id = Column(String(50))
    age = Column(Integer)
    is_vip = Column(Boolean, default=False)
    vip_expires_at = Column(DateTime, nullable=True)
    created_at = Column(DateTime, server_default=func.now())
    invite_count = Column(Integer, default=0)
    consent_accepted = Column(Boolean, default=False)
    last_activity = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    state = relationship("UserState", back_populates="user", uselist=False)
    ratings = relationship("UserRating", back_populates="user")
    reports = relationship("UserReport", foreign_keys="[UserReport.reporter_id]", back_populates="reporter")
    payments = relationship("Payment", back_populates="user")
    settings = relationship("UserSettings", back_populates="user", uselist=False)
    bans = relationship("BanList", back_populates="user", cascade="all, delete-orphan")
    given_ratings = relationship("UserReputation", foreign_keys="[UserReputation.reporter_id]", back_populates="reporter")

class UserState(Base):
    __tablename__ = 'user_states'
    
    id = Column(Integer, primary_key=True, autoincrement=True)
    user_id = Column(Integer, ForeignKey('users.id'))
    elo = Column(Integer)
    is_verified = Column(Boolean)
    search_team = Column(Boolean)
    role = Column(String(50))
    # Новые обязательные поля (изначально None)
    communication_method = Column(String(20), nullable=True)
    timezone = Column(String(20), nullable=True)
    
    user = relationship("User", back_populates="state")

class UserReport(Base):
    __tablename__ = 'user_reports'
    
    id = Column(Integer, primary_key=True, autoincrement=True)
    reporter_id = Column(Integer, ForeignKey('users.id'))
    reported_user_id = Column(Integer, ForeignKey('users.id'))
    faceit_nickname = Column(String(50))
    reason = Column(Integer)
    created_at = Column(DateTime, default=datetime.now)  # Исправлено с datetime на datetime.now
    
    reporter = relationship("User", foreign_keys=[reporter_id], back_populates="reports")
    reported_user = relationship("User", foreign_keys=[reported_user_id])

class UserRating(Base):
    __tablename__ = 'user_ratings'
    
    id = Column(Integer, primary_key=True, autoincrement=True)
    user_id = Column(Integer, ForeignKey('users.id'))
    faceit_nickname = Column(String(50))
    nickname_rating = Column(Integer, default=50)
    is_banned = Column(Boolean, default=False)
    
    user = relationship("User", back_populates="ratings")

class UserReputation(Base):
    __tablename__ = 'user_reputations'
    
    id = Column(Integer, primary_key=True, autoincrement=True)
    reporter_id = Column(Integer, ForeignKey('users.id'))
    reported_user_id = Column(Integer, ForeignKey('users.id'))
    is_positive = Column(Boolean)  # True = +rep, False = -rep
    created_at = Column(DateTime, default=datetime.utcnow)  # Используем UTC
    
    reporter = relationship("User", foreign_keys=[reporter_id])
    reported_user = relationship("User", foreign_keys=[reported_user_id])   

class Appeal(Base):
    __tablename__ = 'appeals'
    
    id = Column(Integer, primary_key=True, autoincrement=True)
    tg_id = Column(BigInteger)
    date_of_receipt = Column(String(50))
    description = Column(Text)
    status = Column(String(20), default='pending')
    created_at = Column(DateTime, default=datetime.now) 

class Payment(Base):
    __tablename__ = 'payments'
    
    id = Column(String, primary_key=True)
    user_id = Column(Integer, ForeignKey('users.id'))
    amount = Column(Float)
    currency = Column(String, default="RUB")
    description = Column(String)
    status = Column(String)  # pending, succeeded, canceled
    subscription_type = Column(String)  # month, 3month, etc.
    created_at = Column(DateTime, default=datetime.utcnow)
    
    user = relationship("User", back_populates="payments")

class UserError(Base):
    __tablename__ = 'user_errors'
    
    id = Column(Integer, primary_key=True, autoincrement=True)
    tg_id = Column(BigInteger)
    error = Column(Text)
    created_at = Column(DateTime, default=datetime.now)

class BanList(Base):
    __tablename__ = 'ban_list'
    __table_args__ = (
        Index('ix_ban_list_user_nickname', 'user_id', 'banned_nickname'),
    )

    id = Column(Integer, primary_key=True, autoincrement=True)
    user_id = Column(Integer, ForeignKey('users.id'))
    banned_nickname = Column(String(50))
    reason = Column(Text)
    created_at = Column(DateTime, default=datetime.now)
    
    user = relationship("User", back_populates="bans")

    @hybrid_property
    def normalized_nickname(self):
        return self.banned_nickname.lower().strip() if self.banned_nickname else None

    @normalized_nickname.expression
    def normalized_nickname(cls):
        return func.lower(func.trim(cls.banned_nickname))

class UserSettings(Base):
    __tablename__ = 'user_settings'
    
    id = Column(Integer, primary_key=True, autoincrement=True)
    user_id = Column(Integer, ForeignKey('users.id'))
    elo_range = Column(Integer, default=300)
    min_age = Column(Integer, default=12)
    max_age = Column(Integer, default=60)
    notifications = Column(Boolean, default=True)
    
    user = relationship("User", back_populates="settings")

class Broadcast(Base):
    __tablename__ = 'broadcasts'
    
    id = Column(Integer, primary_key=True, autoincrement=True)
    admin_id = Column(Integer)
    text = Column(Text)
    sent_count = Column(Integer)
    errors_count = Column(Integer)
    created_at = Column(DateTime, default=datetime.now)

class UserActivity(Base):
    __tablename__ = 'user_activity'
    
    id = Column(Integer, primary_key=True)
    user_id = Column(Integer, ForeignKey('users.id', ondelete='CASCADE'), nullable=False)
    activity_time = Column(DateTime, default=datetime.utcnow)
    activity_type = Column(String(50))

class APIServiceStats(Base):
    __tablename__ = 'api_service_stats'
    
    id = Column(Integer, primary_key=True, autoincrement=True)
    total_requests = Column(Integer, default=0)
    error_count = Column(Integer, default=0)
    cache_size = Column(Integer, default=0)
    cache_hits = Column(Integer, default=0)
    cache_misses = Column(Integer, default=0)
    cache_hit_rate = Column(Float, default=0.0)
    requests_last_hour = Column(Integer, default=0)
    avg_response_time = Column(Float, default=0.0)
    last_error = Column(Text, nullable=True)
    key_stats = Column(Text, nullable=True)
    recorded_at = Column(DateTime, default=datetime.utcnow)