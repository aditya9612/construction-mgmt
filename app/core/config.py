from pydantic_settings import BaseSettings, SettingsConfigDict
from urllib.parse import quote_plus

class Settings(BaseSettings):
 
    APP_NAME: str = "Construction Management System with AI"
    APP_ENV: str = "development"  # development | staging | production
    APP_HOST: str = "0.0.0.0"
    APP_PORT: int = 8000

    DB_NAME: str = "construction_management"
    DB_USER: str = "infraDb"
    DB_PASSWORD: str  # ❗ required from .env
    DB_HOST: str = "localhost"
    DB_PORT: int = 3306

    SQL_ECHO: bool = False


    JWT_SECRET: str  # ❗ required
    JWT_ALGORITHM: str = "HS256"
    JWT_ACCESS_TOKEN_EXPIRE_MINUTES: int = 60
  
    REDIS_URL: str = "redis://localhost:6379/0"
    REDIS_CACHE_TTL_SECONDS: int = 300

    OTP_LENGTH: int = 6
    OTP_EXPIRE_SECONDS: int = 300
    OTP_PROVIDER: str = "mock"  # mock | twilio

    TWILIO_ACCOUNT_SID: str = ""
    TWILIO_AUTH_TOKEN: str = ""
    TWILIO_PHONE_NUMBER: str = ""

    RATE_LIMIT_TIMES: int = 60
    RATE_LIMIT_SECONDS: int = 60

    LOG_LEVEL: str = "INFO"  # DEBUG | INFO

    model_config = SettingsConfigDict(
        env_file=".env",
        extra="ignore",
    )



    @property
    def DATABASE_URL_ASYNC(self) -> str:
        password = quote_plus(self.DB_PASSWORD)
        return (
            f"mysql+asyncmy://{self.DB_USER}:{password}@{self.DB_HOST}:{self.DB_PORT}/"
            f"{self.DB_NAME}?charset=utf8mb4"
        )

    @property
    def DATABASE_URL_SYNC(self) -> str:
        password = quote_plus(self.DB_PASSWORD)
        return (
            f"mysql+pymysql://{self.DB_USER}:{password}@{self.DB_HOST}:{self.DB_PORT}/"
            f"{self.DB_NAME}?charset=utf8mb4"
        )


settings = Settings()