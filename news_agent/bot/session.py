"""Общий фабричный метод для aiogram Bot: уважает HTTPS_PROXY/HTTP_PROXY, если они
заданы (корпоративная сеть, локальная разработка за прокси), и опционально
доверяет дополнительному CA-бандлу через EXTRA_CA_BUNDLE. На проде (Render/Railway)
эти переменные обычно не заданы, так что поведение не меняется — используется
обычная прямая сеть со стандартной проверкой сертификатов.
"""
from __future__ import annotations

import os
import ssl
from typing import Any

import aiohttp
import certifi
from aiogram import Bot
from aiogram.client.session.aiohttp import AiohttpSession


class _ProxyAwareSession(AiohttpSession):
    """AiohttpSession, которая передаёт trust_env=True в aiohttp.ClientSession,
    иначе стандартные переменные окружения *_PROXY просто игнорируются."""

    async def create_session(self) -> aiohttp.ClientSession:
        if self._should_reset_connector:
            await self.close()
        if self._session is None or self._session.closed:
            self._session = aiohttp.ClientSession(
                connector=self._connector_type(**self._connector_init),
                trust_env=True,
            )
            self._should_reset_connector = False
        return self._session


def build_bot(token: str, **kwargs: Any) -> Bot:
    session = _ProxyAwareSession()
    extra_ca_bundle = os.getenv("EXTRA_CA_BUNDLE")
    session._connector_init["ssl"] = ssl.create_default_context(
        cafile=extra_ca_bundle or certifi.where()
    )
    return Bot(token=token, session=session, **kwargs)
