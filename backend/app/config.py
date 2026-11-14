from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    # Anthropic
    anthropic_api_key: str = ""
    agent_model: str = "claude-opus-5-5"
    agent_effort: str = "high"
    agent_max_iterations: int = 40

    # Storage
    database_url: str = "sqlite+aiosqlite:///./data/reconhawk.db"
    artifacts_dir: str = "./data/artifacts"

    # Safety
    scope_enforced: bool = True
    # Comma-separated hosts that are ALWAYS blocked regardless of scope
    hard_blocklist: str = "localhost,127.0.0.1,0.0.0.0,169.254.169.254,metadata.google.internal"

    # Browser
    api_token: str = ''
    cors_origins: str = 'http://localhost:3000,http://127.0.0.1:3000'
    headless: bool = True
    nav_timeout_ms: int = 20000

    def blocklist(self) -> list[str]:
        return [h.strip().lower() for h in self.hard_blocklist.split(",") if h.strip()]


settings = Settings()
