"""Typed configuration, loaded from the environment.

Java analogue: ``@ConfigurationProperties`` + Bean Validation. Nested settings
use a ``__`` delimiter, so ``TRIPPLAN_DB__HOST`` populates ``settings.db.host``.
Reading config through this module rather than ``os.environ`` means a typo is a
startup failure instead of a runtime ``None``.
"""

from __future__ import annotations

from functools import lru_cache
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, Field, SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict

LlmBackend = Literal["none", "hosted", "ollama", "claude"]
RoutingProviderName = Literal["static", "osrm"]

# Repo root, derived from this file's location: src/tripplan/config.py -> api/ -> root
API_DIR = Path(__file__).resolve().parents[2]
REPO_ROOT = API_DIR.parent


class DbSettings(BaseModel):
    host: str = "localhost"
    port: int = 5434
    user: str = "tripplan"
    password: SecretStr = SecretStr("tripplan_dev_only")
    database: str = "tripplan"
    min_pool_size: int = 1
    max_pool_size: int = 10

    # A whole connection string, which is what every managed Postgres hands you
    # (Neon, Supabase, Render). When set it WINS over the parts above: splitting a
    # provider's URL into five env vars is five chances to typo a password.
    #
    # Kept as SecretStr so it cannot be logged by accident — the URL contains the
    # password, which is exactly why `safe_dsn` masks it below.
    url: SecretStr = SecretStr("")

    def dsn(self) -> str:
        if self.url.get_secret_value():
            return self.url.get_secret_value()
        return (
            f"postgresql://{self.user}:{self.password.get_secret_value()}"
            f"@{self.host}:{self.port}/{self.database}"
        )

    def safe_dsn(self) -> str:
        """DSN with the password masked — safe to log."""
        raw = self.url.get_secret_value()
        if raw:
            # Show host and database, hide the credentials.
            without_scheme = raw.split("://", 1)[-1]
            if "@" in without_scheme:
                creds, rest = without_scheme.split("@", 1)
                user = creds.split(":", 1)[0]
                return f"postgresql://{user}:***@{rest}"
            return "postgresql://***"
        return f"postgresql://{self.user}:***@{self.host}:{self.port}/{self.database}"


class RetrievalSettings(BaseModel):
    """Stage 1 candidate caps.

    These bound the prompt size and the model's choice space. Raising them
    costs tokens and tends to make composition less decisive, not more.
    """

    max_places: int = Field(default=30, ge=1)
    max_stays: int = Field(default=12, ge=1)
    max_activities: int = Field(default=20, ge=1)


class RoutingSettings(BaseModel):
    """Stage 4. ``static`` is the placeholder; ``osrm`` measures the real road network.

    `road_factor` and `avg_speed_kmh` still matter under ``osrm``: they configure the
    fallback used for any pair OSRM could not answer, so a partly-degraded plan is
    still complete and still says which legs were guessed.
    """

    provider: RoutingProviderName = "osrm"
    road_factor: float = Field(default=1.35, gt=1.0)
    avg_speed_kmh: float = Field(default=28.0, gt=0)
    max_travel_minutes_per_day: int = Field(default=300, ge=30)
    # The public demo server. Point this at your own OSRM if you have one — the
    # demo asks for light use and offers no availability guarantee, which is
    # exactly why an unreachable router degrades instead of failing.
    osrm_base_url: str = "https://router.project-osrm.org"
    osrm_timeout_seconds: float = Field(default=20.0, gt=0)


class PlanningSettings(BaseModel):
    day_activity_minutes: int = Field(default=480, ge=60)
    day_start_time: str = "09:00"


class LlmSettings(BaseModel):
    """Stage 2. ``backend='none'`` runs the deterministic composer only.

    An unset API key is not an error: the engine degrades to the deterministic
    composer and records that in ``itineraries.composer``. That keeps steps 1-5
    runnable on a machine with no keys and no network.
    """

    backend: LlmBackend = "none"
    model: str = ""
    base_url: str = ""
    api_key: SecretStr = SecretStr("")
    timeout_seconds: float = 90.0
    max_repair_attempts: int = Field(default=1, ge=0, le=3)

    def is_enabled(self) -> bool:
        if self.backend == "none":
            return False
        # Ollama is local and needs no credential; the hosted backends do.
        if self.backend == "ollama":
            return True
        return bool(self.api_key.get_secret_value())


class PhotoSettings(BaseModel):
    """Where sourced photographs are saved, and the URL they are served from.

    Images are downloaded once by `tripplan fetch-photos` and served from the
    frontend's own static directory. Hotlinking Wikimedia at request time gets
    rate-limited (their CDN returns 429 under even mild bursts), leans on a free
    service for every page view, and makes page loads depend on an external host.
    """

    dir: Path = REPO_ROOT / "web" / "public" / "photos"
    # The URL prefix the frontend serves `dir` from. `/photos` because the
    # directory sits inside web/public.
    public_prefix: str = "/photos"
    # Commons thumbnail width to request. 1280 is enough for a full-bleed hero on
    # a 2x display without pulling multi-megabyte originals.
    width: int = 1280


class WorkerSettings(BaseModel):
    poll_interval_seconds: float = Field(default=2.0, gt=0)
    # A job whose lock is older than this is considered abandoned and may be
    # re-claimed. Must exceed the worst-case stage runtime.
    lock_timeout_seconds: int = Field(default=300, ge=30)
    # Run the worker loop INSIDE the API process.
    #
    # A separate process is the right design and stays the default: composition
    # takes tens of seconds and a crash in it should not take HTTP down. But every
    # free hosting tier gives you one process, and an itinerary that never gets
    # built is worse than one built next to the web server. The queue mechanics are
    # unchanged — the same SKIP LOCKED claim, the same lock timeout — so adding a
    # real worker later needs no migration and no code change.
    in_process: bool = False


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_prefix="TRIPPLAN_",
        env_nested_delimiter="__",
        env_file=(REPO_ROOT / ".env"),
        env_file_encoding="utf-8",
        extra="ignore",
    )

    db: DbSettings = DbSettings()
    retrieval: RetrievalSettings = RetrievalSettings()
    routing: RoutingSettings = RoutingSettings()
    planning: PlanningSettings = PlanningSettings()
    llm: LlmSettings = LlmSettings()
    photos: PhotoSettings = PhotoSettings()
    worker: WorkerSettings = WorkerSettings()

    log_level: str = "INFO"
    log_json: bool = False

    # Browser origins allowed to call the API. Local dev by default; a deployment
    # sets its own (TRIPPLAN_CORS_ORIGINS='https://foo.vercel.app'). Comma
    # separated, and never "*": these endpoints echo a session token, so a
    # wildcard would let any page read another site's requests.
    cors_origins: str = "http://localhost:3000,http://127.0.0.1:3000"

    # Apply pending migrations when the API starts. Off locally, where `make
    # migrate` is explicit and a surprise schema change during a test run would be
    # its own bug. On for a managed host, where there is no shell to run it from.
    migrate_on_start: bool = False

    @property
    def cors_origin_list(self) -> list[str]:
        return [o.strip() for o in self.cors_origins.split(",") if o.strip()]

    @property
    def migrations_dir(self) -> Path:
        return API_DIR / "migrations"

    @property
    def seeds_dir(self) -> Path:
        return API_DIR / "seeds"


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    """Process-wide settings singleton."""
    return Settings()
