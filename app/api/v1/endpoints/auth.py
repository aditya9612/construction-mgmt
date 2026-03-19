from fastapi import APIRouter, Depends
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.session import get_db_session
from app.schemas.token import AuthResponse
from app.schemas.user import UserCreate, UserLogin
from app.services.auth_service import AuthService
from app.middlewares.rate_limiter import default_rate_limiter_dependency


router = APIRouter(dependencies=[default_rate_limiter_dependency()])


@router.post("/signup", response_model=AuthResponse)
async def signup(payload: UserCreate, db: AsyncSession = Depends(get_db_session)):
    service = AuthService(db)
    return await service.signup(payload)


@router.post("/login", response_model=AuthResponse)
async def login(payload: UserLogin, db: AsyncSession = Depends(get_db_session)):
    service = AuthService(db)
    return await service.login(payload)

