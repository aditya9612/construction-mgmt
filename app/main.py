from datetime import datetime
import logging
import time
import uuid
import os
from contextlib import asynccontextmanager

from fastapi import APIRouter, FastAPI, Request
from fastapi.responses import JSONResponse
from sqlalchemy.exc import SQLAlchemyError
from redis.exceptions import ConnectionError as RedisConnectionError
from fastapi.middleware.cors import CORSMiddleware
import asyncio
import json
from fastapi import WebSocket, WebSocketDisconnect

from jose import jwt, JWTError
from app.core.config import settings
from app.models.chat import ChatMessage, MessageStatus

from app.api.ai import router as ai_router
from app.api.auth import router as auth_router
from app.api.document import router as document_router
from app.api.equipment import router as equipment_router
from app.api.labour import router as labour_router
from app.api.material import router as material_router
from app.api.project import (
    router as project_router,
    dsr_router,
    issues_router,
    work_progress_router,
    qc_router,
    safety_router,
    checklist_router,
    site_photo_router,
    drawing_router,
    site_request_router,
)
from app.api.boq import router as boq_router
from app.api.user import router as user_router
from app.api.owner import router as owner_router
from app.api.master_data import router as master_router
from app.api.contractor import router as contractor_router
from app.api.expense import router as expense_router
from app.api.invoice import router as invoice_router
from app.api.final_measurement import router as final_measurement_router
from app.api.dashboard import router as dashboard_router
from app.api.billing import router as billing_router
from app.api.approval import router as approval_router
from app.api.work_order import router as work_order_router
from app.api.reports import router as reports_router
from app.api.cad import router as cad_router
from app.api.settings import router as settings_router
from app.api.alert import router as alert_router
from app.api.accountant import router as accountant_router
from app.api.chat import router as chats_router
from app.api.quotation import router as quotation_router
from app.api.agreement import router as agreement_router
from app.api.project_visualization import router as visualization_router
from app.api.attendance import router as attendance_router
from app.api.notification import router as notification_router
# from app.api.rbac import router as rbac_router
from app.cache.redis import create_redis_client
from app.core.config import settings
from app.core.db import AsyncSessionLocal
from app.middlewares.rate_limiter import init_rate_limiter
from app.middlewares.rate_limiter import default_rate_limiter_dependency
from app.utils.helpers import AppError
from app.core.logger import setup_logger
from fastapi.staticfiles import StaticFiles
from app.core.request_context import set_request_id
from app.core.logger import logger
from fastapi import WebSocket, WebSocketDisconnect
from app.core.websocket_manager import manager
from app.core.redis_pubsub import RedisPubSub
import json

SLOW_API_THRESHOLD = 500


@asynccontextmanager
async def lifespan(app: FastAPI):
    setup_logger()

    app.state.redis = await create_redis_client(settings.REDIS_URL)
    try:
        await init_rate_limiter(app, app.state.redis)
    except (RedisConnectionError, OSError) as exc:
        logger.warning(
            f"Redis unavailable; continuing without Redis rate limiting. error={exc}"
        )
        app.state.rate_limiter = None
        app.state.redis = None

    logger.info(
        "Application ready. Open in browser: http://localhost:%s  (docs: http://localhost:%s/docs)",
        settings.APP_PORT,
        settings.APP_PORT,
    )

    if settings.APP_HOST == "0.0.0.0":
        logger.info(
            "NOTE: Use http://localhost:%s (NOT http://0.0.0.0:%s)",
            settings.APP_PORT,
            settings.APP_PORT,
        )

    try:
        yield
    finally:
        redis = getattr(app.state, "redis", None)
        if redis is not None:
            await redis.close()


def create_app() -> FastAPI:
    application = FastAPI(title=settings.APP_NAME, version="0.1.0", lifespan=lifespan)

    #  CORS CONFIG (MAIN FIX)
    origins = [
        "http://localhost:3000",
        "http://127.0.0.1:3000",
        "http://localhost:5173",
        "http://127.0.0.1:5173",
        "http://localhost:4200",
        "https://infrapilot.in",
        "https://infra-pilot.netlify.app",
    ]

    application.add_middleware(
        CORSMiddleware,
        allow_origins=origins,  # using defined list (NO override)
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    os.makedirs("uploads", exist_ok=True)
    os.makedirs("uploads/profile", exist_ok=True)
    os.makedirs("uploads/qc", exist_ok=True)

    # chat uploads
    os.makedirs("uploads/chats/images", exist_ok=True)
    os.makedirs("uploads/chats/videos", exist_ok=True)
    os.makedirs("uploads/chats/files", exist_ok=True)
    os.makedirs("uploads/chats/voice", exist_ok=True)
    os.makedirs("uploads/chats/thumbnails", exist_ok=True)

    # voice task assignments
    os.makedirs("uploads/voice_instructions/raw", exist_ok=True)
    os.makedirs("uploads/voice_instructions/generated", exist_ok=True)
    os.makedirs("uploads/task_icons", exist_ok=True)

    application.mount("/uploads", StaticFiles(directory="uploads"), name="uploads")

    @application.middleware("http")
    async def log_requests(request: Request, call_next):
        request_id = str(uuid.uuid4())

        set_request_id(request_id)

        start_time = time.time()

        logger.info(f"START method={request.method} path={request.url.path}")

        try:
            response = await call_next(request)

            process_time = round((time.time() - start_time) * 1000, 2)

            if process_time > SLOW_API_THRESHOLD:
                logger.warning(
                    f"SLOW API method={request.method} path={request.url.path} "
                    f"status={response.status_code} time={process_time}ms"
                )
            else:
                logger.info(
                    f"END method={request.method} path={request.url.path} "
                    f"status={response.status_code} time={process_time}ms"
                )

            response.headers["X-Request-ID"] = request_id
            return response

        except Exception:
            process_time = round((time.time() - start_time) * 1000, 2)

            logger.exception(
                f"ERROR method={request.method} path={request.url.path} "
                f"time={process_time}ms"
            )
            raise

    @application.middleware("http")
    async def track_user_activity(request: Request, call_next):
        response = await call_next(request)

        try:
            redis = request.app.state.redis

            # decode user from JWT manually
            auth = request.headers.get("Authorization")

            if auth and auth.startswith("Bearer "):
                token = auth.split(" ")[1]

                try:
                    payload = jwt.decode(
                        token, settings.SECRET_KEY, algorithms=["HS256"]
                    )

                    user_id = int(payload.get("sub"))

                    if user_id and redis:
                        await redis.set(f"user:{user_id}:online", 1, ex=60)

                        await redis.set(
                            f"user:{user_id}:last_seen", datetime.utcnow().isoformat()
                        )

                except JWTError:
                    pass

        except Exception:
            pass

        return response

    @application.exception_handler(AppError)
    async def app_error_handler(request: Request, exc: AppError):
        logger.warning(
            f"AppError status={exc.status_code} message={exc.message} path={request.url.path}"
        )
        return JSONResponse(
            status_code=exc.status_code, content={"detail": exc.message}
        )

    @application.exception_handler(SQLAlchemyError)
    async def sqlalchemy_error_handler(request: Request, exc: SQLAlchemyError):
        logger.exception("database.error")
        return JSONResponse(status_code=500, content={"detail": "Database error"})

    @application.get("/health", tags=["health"])
    async def health():
        return {"status": "ok"}

    api_router = APIRouter(dependencies=[default_rate_limiter_dependency()])
    from app.api.project import qc_router, safety_router, checklist_router

    api_router.include_router(auth_router)
    api_router.include_router(user_router)
    # api_router.include_router(rbac_router)
    api_router.include_router(project_router)
    api_router.include_router(qc_router)
    api_router.include_router(safety_router)
    api_router.include_router(checklist_router)
    api_router.include_router(site_photo_router)
    api_router.include_router(drawing_router)
    api_router.include_router(boq_router)
    api_router.include_router(material_router)
    api_router.include_router(labour_router)
    api_router.include_router(master_router)
    api_router.include_router(equipment_router)
    api_router.include_router(document_router)
    api_router.include_router(site_request_router)
    api_router.include_router(chats_router)
    api_router.include_router(ai_router)
    api_router.include_router(owner_router)
    api_router.include_router(contractor_router)
    api_router.include_router(expense_router)
    api_router.include_router(invoice_router)
    api_router.include_router(final_measurement_router)
    api_router.include_router(dashboard_router)
    api_router.include_router(billing_router)
    api_router.include_router(dsr_router)
    api_router.include_router(issues_router)
    api_router.include_router(approval_router)
    api_router.include_router(accountant_router)
    api_router.include_router(work_order_router)
    api_router.include_router(work_progress_router)
    api_router.include_router(quotation_router)
    api_router.include_router(reports_router)
    api_router.include_router(alert_router)
    api_router.include_router(cad_router)
    api_router.include_router(settings_router)
    api_router.include_router(agreement_router)
    api_router.include_router(visualization_router)
    api_router.include_router(attendance_router)
    api_router.include_router(notification_router)

    application.include_router(api_router, prefix="/api/v1")

    return application


app = create_app()


@app.websocket("/ws/{chat_id}")
async def websocket_endpoint(websocket: WebSocket, chat_id: int):

    token = websocket.query_params.get("token")

    if not token:
        await websocket.close()
        return

    try:
        payload = jwt.decode(token, settings.SECRET_KEY, algorithms=["HS256"])
        user_id = int(payload.get("sub"))
    except JWTError:
        await websocket.close()
        return

    await manager.connect(chat_id, websocket)

    redis = app.state.redis

    if not redis:
        await websocket.close()
        return

    #  add to active users
    await redis.sadd(f"chat:{chat_id}:online_users", user_id)

    #  send full active users list on connect
    users = await redis.smembers(f"chat:{chat_id}:online_users")
    await websocket.send_json(
        {"type": "active_users", "users": [int(u) for u in users]}
    )

    #  mark online + last seen
    await redis.set(f"user:{user_id}:online", 1, ex=60)
    await redis.set(f"user:{user_id}:last_seen", datetime.utcnow().isoformat())

    #  broadcast presence (online)
    await redis.publish(
        f"chat:{chat_id}",
        json.dumps({"type": "presence", "user_id": user_id, "status": "online"}),
    )

    #  heartbeat
    async def heartbeat():
        while True:
            await redis.set(f"user:{user_id}:online", 1, ex=60)
            await redis.set(f"user:{user_id}:last_seen", datetime.utcnow().isoformat())
            await asyncio.sleep(30)

    heartbeat_task = asyncio.create_task(heartbeat())

    #  subscribe
    pubsub = redis.pubsub()
    await pubsub.subscribe(f"chat:{chat_id}", f"project:{chat_id}")

    try:
        # =========================
        #  DB SESSION (ADDED)
        # =========================
        async with AsyncSessionLocal() as db:

            async for message in pubsub.listen():

                if message["type"] != "message":
                    continue

                data = json.loads(message["data"])

                # =========================
                #  MARK DELIVERED (ADDED)
                # =========================
                if data.get("type") == "message":
                    msg_id = data.get("message_id")

                    if msg_id:
                        msg = await db.get(ChatMessage, msg_id)

                        if msg and msg.status == MessageStatus.SENT:
                            msg.status = MessageStatus.DELIVERED
                            await db.commit()

                        #  broadcast delivered event (NEW)
                        await redis.publish(
                            f"chat:{chat_id}",
                            json.dumps(
                                {
                                    "type": "delivered",
                                    "chat_id": chat_id,
                                    "message_id": msg_id,
                                }
                            ),
                        )

                #  broadcast ALL event types
                await manager.broadcast(chat_id, data)

    except WebSocketDisconnect:
        manager.disconnect(chat_id, websocket)

    except Exception:
        manager.disconnect(chat_id, websocket)

    finally:
        # stop heartbeat safely
        heartbeat_task.cancel()
        try:
            await heartbeat_task
        except:
            pass

        # remove from active users
        await redis.srem(f"chat:{chat_id}:online_users", user_id)

        # mark offline
        await redis.delete(f"user:{user_id}:online")

        # broadcast offline presence
        await redis.publish(
            f"chat:{chat_id}",
            json.dumps({"type": "presence", "user_id": user_id, "status": "offline"}),
        )

        # cleanup
        await pubsub.unsubscribe(f"chat:{chat_id}")
        await pubsub.close()
