from fastapi import WebSocket
from typing import Dict, List

class ConnectionManager:
    def __init__(self):
        self.active_connections: Dict[int, List[WebSocket]] = {}

    async def connect(self, project_id: int, websocket: WebSocket):
        await websocket.accept()
        self.active_connections.setdefault(project_id, []).append(websocket)

    def disconnect(self, project_id: int, websocket: WebSocket):
        self.active_connections[project_id].remove(websocket)

    async def broadcast(self, project_id: int, message: dict):
        for connection in self.active_connections.get(project_id, []):
            await connection.send_json(message)


manager = ConnectionManager()