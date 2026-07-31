from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="MYVIDCRAFT_", env_file=".env", extra="ignore")

    database_url: str = "sqlite:///./data/myvidcraft.sqlite3"
    artifacts_dir: str = "./data/artifacts"
    redis_url: str = "redis://localhost:6379/0"

    # --- LLM (Phase 1 script/brain engines) ---
    # Empty api key => engines fall back to the deterministic template generator,
    # so a run always completes even with no credentials configured.
    anthropic_api_key: str = ""
    llm_model: str = "claude-opus-5"
    llm_max_tokens: int = 16000
    llm_max_retries: int = 3
    # Seconds. Adaptive thinking is on by default on claude-opus-5, so allow headroom.
    llm_timeout_s: float = 240.0
    # Set to False to force the offline template path even when a key is present.
    llm_enabled: bool = True


settings = Settings()
