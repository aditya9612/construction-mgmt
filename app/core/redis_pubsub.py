import asyncio
import json

class RedisPubSub:
    def __init__(self, redis):
        self.redis = redis

    async def publish(self, project_id: int, message: dict):
        await self.redis.publish(f"project:{project_id}", json.dumps(message))

    async def subscribe(self, project_id: int, manager):
        pubsub = self.redis.pubsub()
        await pubsub.subscribe(f"project:{project_id}")

        try:
            async for msg in pubsub.listen():
                if msg["type"] == "message":
                    data = json.loads(msg["data"])
                    await manager.broadcast(project_id, data)
        except Exception:
            pass