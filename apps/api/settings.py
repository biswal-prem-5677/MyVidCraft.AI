from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="MYVIDCRAFT_", env_file=".env", extra="ignore")

    database_url: str = "sqlite:///./data/myvidcraft.sqlite3"
    artifacts_dir: str = "./data/artifacts"
    redis_url: str = "redis://localhost:6379/0"


settings = Settings()

