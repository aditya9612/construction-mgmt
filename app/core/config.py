from pydantic_settings import BaseSettings, SettingsConfigDict
from urllib.parse import quote_plus

class Settings(BaseSettings):
 
    APP_NAME: str = "Construction Management System with AI"
    APP_ENV: str = "development"  # development | staging | production
    APP_HOST: str = "0.0.0.0"
    APP_PORT: int = 8000

    DB_NAME: str = "construction_management"
    DB_USER: str = "infraDb"
    DB_PASSWORD: str = "Root@1234"
    DB_HOST: str = "localhost"
    DB_PORT: int = 3306


    SQL_ECHO: bool = False
    SLOW_SQL_THRESHOLD: float = 0.5
    GZIP_MINIMUM_SIZE: int = 1000


    JWT_SECRET: str = "supersecret"
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
    
    PAYMENT_PROVIDER: str = "mock"  # mock | razorpay
    RAZORPAY_KEY_ID: str = ""
    RAZORPAY_KEY_SECRET: str = ""
    RAZORPAY_WEBHOOK_SECRET: str = ""

    # ================= PLATFORM SAAS UPI =================
    SUPER_ADMIN_UPI_ID: str = "9960106127@ybl"
    SUPER_ADMIN_PAYEE_NAME: str = "InfraPilot"

    # ================= BILLING RECONCILIATION WORKER =================
    BILLING_RECONCILIATION_ENABLED: bool = False
    BILLING_RECONCILIATION_INTERVAL_MINUTES: int = 60
    BILLING_RECONCILIATION_BATCH_SIZE: int = 50
    BILLING_RECONCILIATION_LOCK_TTL_SECONDS: int = 600

    # ================= EMAIL (SMTP) =================

    # SMTP_HOST: str = "smtp.gmail.com"
    # SMTP_PORT: int = 587
    # SMTP_USERNAME: str = ""
    # SMTP_PASSWORD: str = ""
    # SMTP_FROM_EMAIL: str = ""

    # SHOULD_SEND_EMAIL: bool = True

    RATE_LIMIT_TIMES: int = 60
    RATE_LIMIT_SECONDS: int = 60

    LOG_LEVEL: str = "INFO"  # DEBUG | INFO

    # ================= WHATSAPP (META CLOUD API) =================
    WHATSAPP_ENABLED: bool = False
    WHATSAPP_ACCESS_TOKEN: str = ""
    WHATSAPP_PHONE_NUMBER_ID: str = ""
    WHATSAPP_VENDOR_BILL_TEMPLATE: str = "vendor_bill_notification"
    WHATSAPP_MATERIAL_APPROVAL_TEMPLATE: str = "material_approval_notification"

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