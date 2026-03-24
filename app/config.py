from pydantic_settings import BaseSettings, SettingsConfigDict

class Settings(BaseSettings):
    JWT_SECRET: str

    model_config = SettingsConfigDict(env_file=".env")

settings = Settings()