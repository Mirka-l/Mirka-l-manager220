import logging

import enum
from datetime import datetime
from typing import Sequence
from sqlalchemy import String, Text, func
from sqlalchemy import Integer
from sqlalchemy import DateTime
from sqlalchemy import Enum

from sqlalchemy import Select
from sqlalchemy import create_engine
from sqlalchemy import select
from sqlalchemy.orm import DeclarativeBase
from sqlalchemy.orm import Mapped
from sqlalchemy.orm import mapped_column
from sqlalchemy.orm import sessionmaker
from sqlalchemy.orm import relationship
from sqlalchemy.orm import foreign

from common.helpers import *
from db.db_base import SSDatabase

logging.debug("STARTED UP")


class Base(DeclarativeBase):
    pass


class ChangelogTypes(enum.Enum):
    FIX = 1
    WIP = 2
    TWEAK = 3
    SOUNDADD = 4
    SOUNDDEL = 5
    CODEADD = 6
    CODEDEL = 7
    IMAGEADD = 8
    IMAGEDEL = 9
    SPELLCHECK = 10
    EXPERIMENT = 11


class DBSchema:
    def __init__(self, engine, user, password, ip, port, dbname) -> None:
        self.engine = create_engine(
            f"{engine}://{user}:{password}@{ip}:{port}/{dbname}",
            pool_size=10,
            max_overflow=0,
            pool_recycle=1800,
            pool_pre_ping=True,
        )
        self.Session = sessionmaker(self.engine)
        # Base.metadata.create_all(self.engine)

    def execute_req(self, req: Select) -> Sequence:
        with self.Session() as session:
            return session.scalars(req).all()


class Paradise(DBSchema, SSDatabase):
    class Player(Base):
        __tablename__ = "player"
        id: Mapped[int] = mapped_column(primary_key=True)
        ckey: Mapped[str] = mapped_column(String(32))
        firstseen: Mapped[datetime] = mapped_column(DateTime)
        lastseen: Mapped[datetime] = mapped_column(DateTime)
        ip: Mapped[str] = mapped_column(String(18))
        computerid: Mapped[str] = mapped_column(String(32))
        exp: Mapped[str] = mapped_column(Text)
        species_whitelist: Mapped[str] = mapped_column(Text)

        admin: Mapped["Paradise.Admin"] = relationship(
            "Admin",
            lazy="joined",
            innerjoin=False,
            primaryjoin=lambda: foreign(Paradise.Player.ckey) == Paradise.Admin.ckey,
            viewonly=True,
        )

        def last_admin_rank(self):
            if self.admin and self.admin.rank:
                return self.admin.display_rank or self.admin.rank.name
            return "Игрок"

        def __repr__(self) -> str:
            return f"Player(id={self.id!r}, ckey={self.ckey!r})"

    class Character(Base):
        __tablename__ = "characters"
        id: Mapped[int] = mapped_column(primary_key=True)
        ckey: Mapped[str] = mapped_column(String(32))
        slot: Mapped[int] = mapped_column(Integer())
        real_name: Mapped[str] = mapped_column(String(55))
        gender: Mapped[str] = mapped_column(String(11))
        age: Mapped[int] = mapped_column(Integer())
        species: Mapped[str] = mapped_column(String(45))

        def __repr__(self) -> str:
            return (
                f"Character(id={self.id!r},"
                f" ckey={self.ckey!r},"
                f" real_name={self.real_name!r})"
            )

    class Changelog(Base):
        __tablename__ = "changelog"
        id: Mapped[int] = mapped_column(primary_key=True)
        pr_number: Mapped[int] = mapped_column(Integer())
        date_merged: Mapped[DateTime] = mapped_column(
            DateTime, server_default=func.now())
        author: Mapped[str] = mapped_column(String(32))
        cl_type: Mapped[ChangelogTypes] = mapped_column(Enum(ChangelogTypes))
        cl_entry: Mapped[str] = mapped_column(Text)

    class Ban(Base):
        __tablename__ = "ban"
        id: Mapped[int] = mapped_column(primary_key=True)
        bantime: Mapped[DateTime] = mapped_column(DateTime)
        ban_round_id: Mapped[int] = mapped_column(Integer())
        serverip: Mapped[str] = mapped_column(String(32))
        server_id: Mapped[str] = mapped_column(String(50))
        bantype: Mapped[str] = mapped_column(String(32))
        reason: Mapped[str] = mapped_column(Text)
        job: Mapped[str] = mapped_column(String(32))
        duration: Mapped[int] = mapped_column(Integer())
        rounds: Mapped[int] = mapped_column(Integer())
        expiration_time: Mapped[DateTime] = mapped_column(DateTime)
        ckey: Mapped[str] = mapped_column(String(32))
        computerid: Mapped[str] = mapped_column(String(32))
        ip: Mapped[str] = mapped_column(String(32))
        a_ckey: Mapped[str] = mapped_column(String(32))
        unbanned: Mapped[int] = mapped_column(Integer())
        unbanned_ckey: Mapped[str] = mapped_column(String(32))
        exportable: Mapped[int] = mapped_column(Integer())

        def __repr__(self) -> str:
            return (
                f"Ban(id={self.id!r},"
                f" ckey={self.ckey!r},"
                f" reason={self.reason!r},"
                f" a_ckey={self.a_ckey!r})"
            )

    class Note(Base):
        __tablename__ = "notes"
        id: Mapped[int] = mapped_column(primary_key=True)
        ckey: Mapped[str] = mapped_column(String(32))
        notetext: Mapped[str] = mapped_column(Text)
        timestamp: Mapped[DateTime] = mapped_column(DateTime)
        round_id: Mapped[int] = mapped_column(Integer())
        adminckey: Mapped[str] = mapped_column(String(32))
        server: Mapped[str] = mapped_column(String(50))

        def __repr__(self) -> str:
            return (
                f"Note(id={self.id!r},"
                f" ckey={self.ckey!r},"
                f" reason={self.notetext!r},"
                f" a_ckey={self.adminckey!r})"
            )

    class Watch(Base):
        __tablename__ = "watch"
        ckey: Mapped[str] = mapped_column(String(32), primary_key=True)
        reason: Mapped[str] = mapped_column(Text)
        timestamp: Mapped[DateTime] = mapped_column(DateTime)
        adminckey: Mapped[str] = mapped_column(String(32))

        def __repr__(self) -> str:
            return (
                f"Watch(ckey={self.ckey!r},"
                f" reason={self.reason!r},"
                f" a_ckey={self.adminckey!r})"
            )

    class AdminRank(Base):
        __tablename__ = "admin_ranks"
        id: Mapped[int] = mapped_column(primary_key=True)
        name: Mapped[str] = mapped_column(String(32))
        default_permissions: Mapped[int] = mapped_column(Integer())

        def __repr__(self) -> str:
            return f"AdminRank(id={self.id!r}, name={self.name!r})"

    class Admin(Base):
        __tablename__ = "admin"
        id: Mapped[int] = mapped_column(Integer(), primary_key=True)
        ckey: Mapped[str] = mapped_column(String(32))
        display_rank: Mapped[str | None] = mapped_column(String(32), nullable=True)
        permissions_rank: Mapped[int | None] = mapped_column(Integer(), nullable=True)
        extra_permissions: Mapped[int] = mapped_column(Integer(), default=0)
        removed_permissions: Mapped[int] = mapped_column(Integer(), default=0)

        rank: Mapped["Paradise.AdminRank"] = relationship(
            "AdminRank",
            lazy="joined",
            innerjoin=False,
            primaryjoin=lambda: foreign(Paradise.Admin.permissions_rank) == Paradise.AdminRank.id,
            viewonly=True
        )

        def __repr__(self) -> str:
            return (
                f"Admin(id={self.id!r}, ckey={self.ckey!r}, display_rank={self.display_rank!r}, "
                f"permissions_rank={self.permissions_rank!r})"
            )
           
    class KudosHistory(Base):
        __tablename__ = "kudos_history"

        id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
        giver: Mapped[str] = mapped_column(String(32), nullable=False)
        receiver: Mapped[str] = mapped_column(String(32), nullable=False)
    # Здесь тоже Float, чтобы видеть, сколько именно пришло в логах
        points: Mapped[float] = mapped_column(default=1.0)
        round_id: Mapped[int] = mapped_column(Integer, nullable=False)
        timestamp: Mapped[datetime] = mapped_column(DateTime, default=func.now())

        def __repr__(self) -> str:
            return (f"KudosHistory(id={self.id!r}, giver={self.giver!r}, "
                    f"receiver={self.receiver!r}, points={self.points!r}, "
                    f"round={self.round_id!r})")
        
    class KudosTotals(Base):
        __tablename__ = "kudos_totals"
        receiver: Mapped[str] = mapped_column(String(32), primary_key=True)
    # ПРОВЕРЬ ТУТ: должно быть float или Float()
        total_score: Mapped[float] = mapped_column(default=0.0)

        def __repr__(self) -> str:
            return f"KudosTotals(receiver={self.receiver!r}, score={self.total_score!r})"
        
    def get_player(self, ckey: str) -> Player | None:
        ckey = sanitize_ckey(ckey)
        with self.Session() as session:
            result = session.query(self.Player).where(
                self.Player.ckey == ckey).first()
            return result or None

    def get_characters(self, ckey: str) -> Sequence[Character]:
        ckey = sanitize_ckey(ckey)
        req = select(self.Character).where(self.Character.ckey == ckey)
        return self.execute_req(req)

    def get_characters_by_name(self, name: str) -> Sequence[Character]:
        req = select(self.Character).where(
            self.Character.real_name.regexp_match(name))
        return self.execute_req(req)

    def get_recent_bans(self) -> Sequence[Ban]:
        req = select(self.Ban).where(self.Ban.exportable).order_by(
            self.Ban.id.desc()).limit(50)
        with self.Session() as session:
            session.expire_on_commit = False
            with session.begin():
                result = session.scalars(req).all()
                for ban in result:
                    ban.exportable = 0
        return result

    def get_bans(self, ckey: str) -> Sequence[Ban]:
        ckey = sanitize_ckey(ckey)
        req = select(self.Ban).where((self.Ban.ckey == ckey)
                                     | (self.Ban.a_ckey == ckey)).order_by(self.Ban.id.desc())
        return self.execute_req(req)

    def get_notes(self, ckey: str, amount: int) -> Sequence[Note]:
        ckey = sanitize_ckey(ckey)
        req = select(self.Note).where((self.Note.ckey == ckey)
                                      | (self.Note.adminckey == ckey)).order_by(self.Note.id.desc()).limit(amount)
        return self.execute_req(req)

    def get_player_species_whitelist(self, ckey: str) -> str:
        ckey = sanitize_ckey(ckey)
        species_whitelist_req = select(self.Player.species_whitelist).where(
            self.Player.ckey == ckey)
        species_whitelist = self.execute_req(species_whitelist_req)
        return species_whitelist

    def set_player_species_whitelist(self, ckey: str, species_whitelist: str) -> Player:
        ckey = sanitize_ckey(ckey)

        req = select(self.Player).where(
            self.Player.ckey == ckey)

        with self.Session() as session:
            session.expire_on_commit = False
            with session.begin():
                result = session.scalars(req).one_or_none()
                if not result:
                    return ERRORS.ERR_404
                result.species_whitelist = species_whitelist

        return result

    def push_changelog(self, cl: dict, number: int):
        with self.Session() as session:
            for change in cl["changes"]:
                change_db = Paradise.Changelog(
                    pr_number=number,
                    author=cl["author"],
                    cl_type=change["tag"],
                    cl_entry=change["message"]
                )
                session.add(change_db)
            session.commit()

    def get_kudos_rating(self, limit: int = 10):
        with self.Session() as session:
            query = (
                select(self.KudosTotals.receiver, self.KudosTotals.total_score)
                .order_by(self.KudosTotals.total_score.desc())
                .limit(limit)
            )
            return session.execute(query).all()

    def get_player_kudos_count(self, ckey: str) -> float:
        with self.Session() as session:
        # Прямой запрос без лишних преобразований
            query = select(self.KudosTotals.total_score).where(self.KudosTotals.receiver == ckey)
            result = session.scalar(query)
        
        # Если result пришел, возвращаем его как есть (float)
            if result is not None:
                return float(result) 
            return 0.0

    def get_player_position(self, score: float) -> int:
        try:
            with self.Session() as session:
                # ВАЖНО: используем строго '>', чтобы не считать самого себя
                query = select(func.count(self.KudosTotals.receiver)).where(self.KudosTotals.total_score > score)
                result = session.scalar(query)
                
                # Если база нашла 1 человека выше (тебя), то (1) + 1 вернет 2-е место.
                # return int(result or 0) + 1
                return result
        except Exception as e:
            print(f"Position Error: {e}")
            return 1

    def get_admin_kudos_info(self, target_ckey: str, limit: int = 20):
        with self.Session() as session:
            query = (
                select(self.KudosHistory)
                .where(self.KudosHistory.receiver == target_ckey)
                .order_by(self.KudosHistory.timestamp.desc())
                .limit(limit)
            )
            return session.scalars(query).all()
        
    def get_ckey_by_discord(self, discord_id: int) -> str | None:
        # Так как в таблице Player нет discord_id, просто пропускаем этот шаг
        return None
    
    def get_next_player_score(self, score: float) -> float | None:
        try:
            with self.Session() as session:
                # Ищем минимальный балл среди тех, кто выше
                query = select(func.min(self.KudosTotals.total_score)).where(self.KudosTotals.total_score > score)
                result = session.scalar(query)
                return float(result) if result is not None else None
        except Exception as e:
            print(f"Next Score Error: {e}")
            return None