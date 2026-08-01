from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="MYVIDCRAFT_", env_file=".env", extra="ignore")

    database_url: str = "sqlite:///./data/myvidcraft.sqlite3"
    artifacts_dir: str = "./data/artifacts"
    redis_url: str = "redis://localhost:6379/0"

    # --- HTTP ---
    # Comma-separated allowed browser origins, not a JSON list: this is read from
    # an env var far more often than from code, and `a,b` is what a person types.
    #
    # The default is the local web UI and nothing else. It is deliberately not
    # `*`: the CORS spec forbids pairing a wildcard origin with credentials, so
    # that combination is invalid rather than merely permissive, and browsers
    # reject it. `cors_origin_list` downgrades a wildcard instead of shipping the
    # invalid pair — see the note there. Add real deployment origins here.
    cors_origins: str = "http://localhost:3000,http://127.0.0.1:3000"

    @property
    def cors_origin_list(self) -> list[str]:
        """Parsed `cors_origins`. Empty entries are dropped so a trailing comma is harmless."""
        return [origin.strip() for origin in self.cors_origins.split(",") if origin.strip()]

    @property
    def cors_allow_credentials(self) -> bool:
        """
        False when any origin is `*`.

        Credentials plus a wildcard is the one combination the spec rules out,
        and Starlette resolves it by quietly echoing the request origin back —
        which is strictly worse than a wildcard, because it makes *every* site a
        permitted credentialed origin. Dropping credentials keeps the wildcard
        meaning what whoever set it thought it meant.
        """
        return "*" not in self.cors_origin_list

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

    # --- Speech synthesis ---
    # Name registered in `workers.media.tts.registry`. An unknown name raises at
    # the first synthesis attempt rather than silently using a default.
    #
    # The default is a cloud engine rather than a local one because the local
    # option (SAPI) could not be deployed: Windows-only, and serialised behind a
    # process-wide lock. An HTTP backend has neither property.
    tts_engine: str = "openai"
    openai_api_key: str = ""
    # tts-1 is the model that honours `speed`, which is how `pace_wpm` from the
    # dialogue schema reaches the audio. tts-1-hd trades cost for fidelity.
    tts_model: str = "tts-1"
    # Retries belong to the SDK so a blip re-sends one turn instead of re-running
    # the whole voice stage — see the note in `openai_tts.py`.
    tts_max_retries: int = 2
    tts_timeout_s: float = 60.0

    # --- Video composition ---
    # Name registered in `workers.media.video.registry`. The default compositor
    # draws frames in pure Python and needs only FFmpeg to mux; a GPU avatar
    # backend registers under its own name.
    video_engine: str = "slideshow"
    # 16:9 by default. The 9:16 shorts variants are rendered by the Phase 4
    # export stage, not here.
    video_width: int = 1280
    video_height: int = 720
    video_fps: int = 25


settings = Settings()
