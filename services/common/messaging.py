"""Message queue broker abstraction for RabbitMQ with in-memory fallback."""

from __future__ import annotations

import asyncio
import json
import logging
import os
from collections.abc import Callable, Coroutine
from typing import Any

from services.common.metrics import SERVICE_QUEUE_LENGTH

logger = logging.getLogger("messaging")

QUEUE_NAME = "orders_queue"


class MessageBroker:
    """Manages asynchronous message publishing and consuming via RabbitMQ or in-memory queue."""

    def __init__(self, rabbitmq_url: str | None = None) -> None:
        self.rabbitmq_url = rabbitmq_url or os.getenv(
            "RABBITMQ_URL", "amqp://guest:guest@localhost:5672/"
        )
        self._rmq_connection = None
        self._rmq_channel = None
        self._in_memory_queue: asyncio.Queue[dict[str, Any]] = asyncio.Queue()
        self._using_rmq = False
        self._consumer_tasks: list[asyncio.Task[None]] = []
        self._active_consumer_count = 0
        self._handler: Callable[[dict[str, Any]], Coroutine[Any, Any, None]] | None = None

    async def connect(self) -> None:
        """Attempts connection to RabbitMQ; falls back to asyncio.Queue if unavailable."""
        try:
            import aio_pika

            self._rmq_connection = await aio_pika.connect_robust(self.rabbitmq_url, timeout=2.0)
            self._rmq_channel = await self._rmq_connection.channel()
            await self._rmq_channel.set_qos(prefetch_count=1)
            await self._rmq_channel.declare_queue(QUEUE_NAME, durable=True)
            self._using_rmq = True
            logger.info("Connected to RabbitMQ at %s", self.rabbitmq_url)
        except Exception as exc:
            self._using_rmq = False
            logger.info(
                "RabbitMQ unavailable (%s). Operating in-memory async message queue.",
                exc,
            )

    async def publish(self, queue_name: str, payload: dict[str, Any]) -> None:
        """Publishes an event payload to the target queue."""
        if self._using_rmq and self._rmq_channel:
            import aio_pika

            body = json.dumps(payload).encode("utf-8")
            message = aio_pika.Message(
                body=body,
                delivery_mode=aio_pika.DeliveryMode.PERSISTENT,
            )
            await self._rmq_channel.default_exchange.publish(message, routing_key=queue_name)
            await self.update_backlog_metric(queue_name)
        else:
            await self._in_memory_queue.put(payload)
            SERVICE_QUEUE_LENGTH.labels(service="order-service", queue_name=queue_name).set(
                self._in_memory_queue.qsize()
            )

    async def get_backlog(self, queue_name: str = QUEUE_NAME) -> int:
        """Queries the current queue backlog length."""
        if self._using_rmq and self._rmq_channel:
            queue = await self._rmq_channel.declare_queue(queue_name, passive=True)
            count = queue.declaration_result.message_count
            SERVICE_QUEUE_LENGTH.labels(service="order-service", queue_name=queue_name).set(count)
            return count
        else:
            count = self._in_memory_queue.qsize()
            SERVICE_QUEUE_LENGTH.labels(service="order-service", queue_name=queue_name).set(count)
            return count

    async def update_backlog_metric(self, queue_name: str = QUEUE_NAME) -> int:
        return await self.get_backlog(queue_name)

    async def set_consumer_concurrency(
        self,
        target_count: int,
        handler: Callable[[dict[str, Any]], Coroutine[Any, Any, None]],
    ) -> None:
        """Dynamically scales consumer workers up or down."""
        self._handler = handler
        current = len(self._consumer_tasks)

        if target_count < current:
            # Scale down: cancel excess consumers
            for _ in range(current - target_count):
                task = self._consumer_tasks.pop()
                task.cancel()
            logger.info("Scaled down consumers from %d to %d", current, target_count)
        elif target_count > current:
            # Scale up: spawn new consumers
            for i in range(current, target_count):
                task = asyncio.create_task(
                    self._worker_loop(i, handler), name=f"order-consumer-{i}"
                )
                self._consumer_tasks.append(task)
            logger.info("Scaled up consumers from %d to %d", current, target_count)

        self._active_consumer_count = target_count

    async def _worker_loop(
        self,
        worker_id: int,
        handler: Callable[[dict[str, Any]], Coroutine[Any, Any, None]],
    ) -> None:
        """Worker loop processing messages from RabbitMQ or in-memory queue."""
        logger.info("Consumer worker %d started", worker_id)
        try:
            if self._using_rmq and self._rmq_channel:
                queue = await self._rmq_channel.declare_queue(QUEUE_NAME, durable=True)
                async with queue.iterator() as queue_iter:
                    async for message in queue_iter:
                        async with message.process():
                            data = json.loads(message.body.decode("utf-8"))
                            await handler(data)
                            await self.update_backlog_metric(QUEUE_NAME)
            else:
                while True:
                    data = await self._in_memory_queue.get()
                    try:
                        await handler(data)
                    finally:
                        self._in_memory_queue.task_done()
                        SERVICE_QUEUE_LENGTH.labels(
                            service="order-service", queue_name=QUEUE_NAME
                        ).set(self._in_memory_queue.qsize())
        except asyncio.CancelledError:
            logger.info("Consumer worker %d stopped", worker_id)

    async def stop(self) -> None:
        """Stops all consumers and closes connections."""
        for task in self._consumer_tasks:
            task.cancel()
        self._consumer_tasks.clear()
        if self._rmq_connection:
            await self._rmq_connection.close()


# Shared broker instance
BROKER = MessageBroker()
