"""Thread-to-asyncio event fan-out for WebSocket pushes.

Kernel threads publish from any thread; each connected WebSocket client owns
an ``asyncio.Queue`` that the ``/ws`` handler drains. Before the event loop
is captured (server still starting) publishing degrades to a no-op so the
kernel never blocks on the UI.
"""

import asyncio
import itertools
import threading
from typing import Any


class EventBus:
    def __init__(self) -> None:
        self._loop: asyncio.AbstractEventLoop | None = None
        self._queues: dict[int, asyncio.Queue] = {}
        self._lock = threading.Lock()
        self._client_ids = itertools.count(1)
        self._seq = itertools.count(1)

    def attach_loop(self, loop: asyncio.AbstractEventLoop) -> None:
        self._loop = loop

    def detach_loop(self) -> None:
        self._loop = None

    def subscribe(self) -> tuple[int, asyncio.Queue]:
        queue: asyncio.Queue = asyncio.Queue(maxsize=512)
        client_id = next(self._client_ids)
        with self._lock:
            self._queues[client_id] = queue
        return client_id, queue

    def unsubscribe(self, client_id: int) -> None:
        with self._lock:
            self._queues.pop(client_id, None)

    def publish(self, channel: str, payload: Any = None) -> None:
        loop = self._loop
        if loop is None or loop.is_closed():
            return
        message = {"type": str(channel), "seq": next(self._seq), "payload": payload}
        with self._lock:
            queues = list(self._queues.values())
        for queue in queues:
            def _put(q: asyncio.Queue = queue) -> None:
                try:
                    q.put_nowait(message)
                except asyncio.QueueFull:
                    pass
            try:
                loop.call_soon_threadsafe(_put)
            except RuntimeError:
                return
