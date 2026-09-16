"""Scripted stand-ins for google-genai's live session and connect factory."""

from __future__ import annotations

import asyncio
from contextlib import asynccontextmanager


class FakeLiveSession:
    """Scripted stand-in for google-genai's AsyncSession."""

    def __init__(self, script, gap=0.02, tail=0.15):
        self.script = list(script)
        self.gap = gap
        self.tail = tail
        self.tool_responses = []
        self.realtime = []
        self.client_content = []
        self.closed = False

    async def _sleep(self, seconds):
        end = asyncio.get_running_loop().time() + seconds
        while not self.closed and asyncio.get_running_loop().time() < end:
            await asyncio.sleep(0.01)

    async def receive(self):
        for msg in self.script:
            if self.closed:
                return
            yield msg
            await self._sleep(self.gap)
        await self._sleep(self.tail)

    async def send_tool_response(self, *, function_responses):
        self.tool_responses.extend(function_responses)

    async def send_realtime_input(self, **kw):
        self.realtime.append(kw)

    async def send_client_content(self, **kw):
        self.client_content.append(kw)

    async def close(self):
        self.closed = True


class FakeConnector:
    """connect(config=...) -> async context manager yielding the next scripted session."""

    def __init__(self, sessions):
        self.sessions = list(sessions)
        self.configs = []

    def __call__(self, *, config):
        self.configs.append(config)
        session = self.sessions.pop(0)

        @asynccontextmanager
        async def cm():
            yield session

        return cm()
