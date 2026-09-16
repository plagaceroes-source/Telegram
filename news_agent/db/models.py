"""SQLAlchemy models — mirrors the data model in ТЗ section 4."""
from __future__ import annotations

import datetime as dt

from sqlalchemy import JSON, BigInteger, DateTime, ForeignKey, Index, String, UniqueConstraint
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship


class Base(DeclarativeBase):
    pass


def utcnow() -> dt.datetime:
    return dt.datetime.now(dt.timezone.utc)


# utcnow() returns tz-aware datetimes; every timestamp column must be TIMESTAMPTZ
# or Postgres/asyncpg rejects the insert (works on SQLite, which doesn't enforce this).
_TZ_NOW = {"type_": DateTime(timezone=True), "default": utcnow}
_TZ_NULLABLE = {"type_": DateTime(timezone=True), "nullable": True}


# --- account/source management (2.1.2) ---------------------------------

class UserbotAccount(Base):
    """A Telethon account used to monitor a subset of sources."""

    __tablename__ = "userbot_accounts"

    id: Mapped[int] = mapped_column(primary_key=True)
    phone_masked: Mapped[str] = mapped_column(String(32))
    label: Mapped[str] = mapped_column(String(128))
    session_name: Mapped[str] = mapped_column(String(64), unique=True)
    status: Mapped[str] = mapped_column(String(16), default="active")  # active/limited/disabled
    created_at: Mapped[dt.datetime] = mapped_column(**_TZ_NOW)

    sources: Mapped[list["Source"]] = relationship(back_populates="userbot_account")

    @property
    def sources_count(self) -> int:
        return len(self.sources)


class Source(Base):
    """A channel/group to monitor for new posts."""

    __tablename__ = "sources"

    id: Mapped[int] = mapped_column(primary_key=True)
    username: Mapped[str] = mapped_column(String(255))
    title: Mapped[str] = mapped_column(String(255), default="")
    tg_chat_id: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    lang: Mapped[str] = mapped_column(String(8), default="auto")  # es/ru/uk/... or "auto"
    mode: Mapped[str] = mapped_column(String(16), default="rewrite")  # rewrite / as_is
    active: Mapped[bool] = mapped_column(default=True)
    userbot_account_id: Mapped[int | None] = mapped_column(ForeignKey("userbot_accounts.id"), nullable=True)
    added_at: Mapped[dt.datetime] = mapped_column(**_TZ_NOW)
    added_by: Mapped[str] = mapped_column(String(128), default="")
    joined: Mapped[bool] = mapped_column(default=False)  # userbot has completed join_channel

    userbot_account: Mapped[UserbotAccount | None] = relationship(back_populates="sources")

    __table_args__ = (UniqueConstraint("username", name="uq_sources_username"),)


class TargetChannel(Base):
    """A channel in the publishing network."""

    __tablename__ = "target_channels"

    id: Mapped[int] = mapped_column(primary_key=True)
    username: Mapped[str] = mapped_column(String(255))
    title: Mapped[str] = mapped_column(String(255), default="")
    tg_chat_id: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    network_tag: Mapped[str] = mapped_column(String(64), default="")
    lang: Mapped[str] = mapped_column(String(8), default="ru")
    active: Mapped[bool] = mapped_column(default=True)
    added_at: Mapped[dt.datetime] = mapped_column(**_TZ_NOW)

    __table_args__ = (UniqueConstraint("username", name="uq_target_channels_username"),)


# --- content pipeline (2.1 - 2.4) ---------------------------------------

class RawPost(Base):
    """A raw post collected by the userbot, awaiting processing."""

    __tablename__ = "raw_posts"

    id: Mapped[int] = mapped_column(primary_key=True)
    source_id: Mapped[int] = mapped_column(ForeignKey("sources.id"))
    tg_message_id: Mapped[int | None] = mapped_column(nullable=True)
    text: Mapped[str] = mapped_column(default="")
    detected_lang: Mapped[str] = mapped_column(String(8), default="")
    media_paths: Mapped[list[str]] = mapped_column(JSON, default=list)
    collected_at: Mapped[dt.datetime] = mapped_column(**_TZ_NOW)
    status: Mapped[str] = mapped_column(String(16), default="pending")  # pending/processing/done/error

    source: Mapped[Source] = relationship()

    __table_args__ = (Index("ix_raw_posts_status", "status"),)


class DraftPost(Base):
    """A processed draft awaiting/undergoing approval."""

    __tablename__ = "draft_posts"

    id: Mapped[int] = mapped_column(primary_key=True)
    raw_post_id: Mapped[int] = mapped_column(ForeignKey("raw_posts.id"))
    translated_text: Mapped[str] = mapped_column(default="")
    media_paths: Mapped[list[str]] = mapped_column(JSON, default=list)
    status: Mapped[str] = mapped_column(String(16), default="pending_approval")
    # pending_approval / approved / rejected / published
    target_channel_id: Mapped[int | None] = mapped_column(ForeignKey("target_channels.id"), nullable=True)
    approval_chat_id: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    approval_message_id: Mapped[int | None] = mapped_column(nullable=True)
    decided_by: Mapped[str] = mapped_column(String(128), default="")
    decided_at: Mapped[dt.datetime | None] = mapped_column(**_TZ_NULLABLE)
    created_at: Mapped[dt.datetime] = mapped_column(**_TZ_NOW)

    raw_post: Mapped[RawPost] = relationship()
    target_channel: Mapped[TargetChannel | None] = relationship()

    __table_args__ = (Index("ix_draft_posts_status", "status"),)


class PublishedPost(Base):
    """Record of a post published into a target channel."""

    __tablename__ = "published_posts"

    id: Mapped[int] = mapped_column(primary_key=True)
    draft_post_id: Mapped[int] = mapped_column(ForeignKey("draft_posts.id"))
    target_channel_id: Mapped[int] = mapped_column(ForeignKey("target_channels.id"))
    tg_message_id: Mapped[int] = mapped_column()
    published_at: Mapped[dt.datetime] = mapped_column(**_TZ_NOW)

    draft_post: Mapped[DraftPost] = relationship()
    target_channel: Mapped[TargetChannel] = relationship()


# --- stats / attribution (3.x) ------------------------------------------

class InviteLink(Base):
    """Named invite link created programmatically for source attribution (3.4)."""

    __tablename__ = "invite_links"

    id: Mapped[int] = mapped_column(primary_key=True)
    target_channel_id: Mapped[int] = mapped_column(ForeignKey("target_channels.id"))
    name: Mapped[str] = mapped_column(String(32))  # short code, Telegram's 32-char limit
    tg_invite_link: Mapped[str] = mapped_column(String(255))
    source_label: Mapped[str] = mapped_column(String(255), default="")
    campaign_tag: Mapped[str] = mapped_column(String(128), default="")
    revoked: Mapped[bool] = mapped_column(default=False)
    created_at: Mapped[dt.datetime] = mapped_column(**_TZ_NOW)
    created_by: Mapped[str] = mapped_column(String(128), default="")

    target_channel: Mapped[TargetChannel] = relationship()

    __table_args__ = (Index("ix_invite_links_name", "name"),)


class SubscriberEvent(Base):
    """join/leave events, optionally attributed to an invite link (3.4.3/3.4.4)."""

    __tablename__ = "subscriber_events"

    id: Mapped[int] = mapped_column(primary_key=True)
    target_channel_id: Mapped[int] = mapped_column(ForeignKey("target_channels.id"))
    tg_user_id: Mapped[int] = mapped_column()
    event_type: Mapped[str] = mapped_column(String(8))  # join / leave
    invite_link_id: Mapped[int | None] = mapped_column(ForeignKey("invite_links.id"), nullable=True)
    is_direct: Mapped[bool] = mapped_column(default=False)  # joined w/o a tracked invite link
    occurred_at: Mapped[dt.datetime] = mapped_column(**_TZ_NOW)

    target_channel: Mapped[TargetChannel] = relationship()
    invite_link: Mapped[InviteLink | None] = relationship()

    __table_args__ = (
        Index("ix_subscriber_events_channel_user", "target_channel_id", "tg_user_id"),
    )


class ChannelStatsDaily(Base):
    """Subscriber-count snapshots (3.2)."""

    __tablename__ = "channel_stats_daily"

    id: Mapped[int] = mapped_column(primary_key=True)
    target_channel_id: Mapped[int] = mapped_column(ForeignKey("target_channels.id"))
    snapshot_at: Mapped[dt.datetime] = mapped_column(**_TZ_NOW)
    subscriber_count: Mapped[int] = mapped_column()

    target_channel: Mapped[TargetChannel] = relationship()

    __table_args__ = (Index("ix_channel_stats_daily_channel_time", "target_channel_id", "snapshot_at"),)
