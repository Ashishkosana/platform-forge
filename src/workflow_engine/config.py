from __future__ import annotations

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    database_url: str = "postgresql://workflow:workflow@127.0.0.1:5432/workflow"
    lease_ttl_seconds: float = 15.0
    heartbeat_interval_seconds: float = 5.0
    poll_interval_seconds: float = 0.25
    worker_concurrency: int = 1
    base_backoff_seconds: float = 0.5
    max_backoff_seconds: float = 30.0
    api_host: str = "0.0.0.0"
    api_port: int = 43180
    demo_mode: bool = False
    allowed_http_hosts: str = "127.0.0.1,localhost"
    max_body_bytes: int = 1_000_000
    worker_metrics_port: int = 0
    log_level: str = "INFO"

    @property
    def allowed_hosts(self) -> frozenset[str]:
        return frozenset(h.strip().lower() for h in self.allowed_http_hosts.split(",") if h.strip())

    @property
    def lease_ttl_sql(self) -> str:
        return f"{self.lease_ttl_seconds} seconds"


def load_settings() -> Settings:
    return Settings()
