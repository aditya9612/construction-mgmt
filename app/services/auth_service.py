from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.security import create_access_token, get_password_hash, verify_password
from app.models.user import User, UserRole
from app.schemas.token import Token
from app.schemas.user import UserCreate, UserLogin
from app.utils.helpers import AppError, ConflictError


class AuthService:
    def __init__(self, db: AsyncSession):
        self.db = db

    async def signup(self, payload: UserCreate) -> dict:
        existing = await self.db.scalar(select(User).where(User.email == payload.email))
        if existing is not None:
            raise ConflictError("Email already registered")

        role = UserRole.SITE_ENGINEER
        if payload.role:
            try:
                role = UserRole(payload.role)
            except Exception:
                raise AppError(status_code=422, message="Invalid role")

        user = User(
            email=payload.email,
            hashed_password=get_password_hash(payload.password),
            full_name=payload.full_name,
            role=role,
            is_active=True,
        )
        self.db.add(user)
        await self.db.flush()

        token = self._build_token(user)
        return {"token": token, "user_id": user.id}

    async def login(self, payload: UserLogin) -> dict:
        user = await self.db.scalar(select(User).where(User.email == payload.email))
        if user is None or not verify_password(payload.password, user.hashed_password):
            raise AppError(status_code=401, message="Invalid credentials")
        if not user.is_active:
            raise AppError(status_code=403, message="User is inactive")

        token = self._build_token(user)
        return {"token": token, "user_id": user.id}

    def _build_token(self, user: User) -> Token:
        access_token = create_access_token({"sub": str(user.id), "role": user.role.value})
        return Token(access_token=access_token)

