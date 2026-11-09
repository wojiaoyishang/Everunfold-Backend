"""Example uses the same project's dedicated test channel, never shared credentials in payloads."""

from redis.asyncio import Redis

from app.plugin_sdk import ResidentContext


async def listen(context: ResidentContext) -> None:
    client = Redis.from_url(
        context.config.redis.url.get_secret_value(), socket_timeout=5, socket_connect_timeout=2
    )
    try:
        async with client.pubsub() as subscription:
            await subscription.subscribe(context.config.redis_prefix + "runtime:example")
            context.connected()
            checkpoint = await context.checkpoint()
            count = int(checkpoint.get("messages", 0))
            while not context.stopping.is_set():
                message = await subscription.get_message(ignore_subscribe_messages=True, timeout=1)
                if message:
                    context.received()
                    count += 1
                    await context.checkpoint({"messages": count})
    finally:
        await client.aclose()
