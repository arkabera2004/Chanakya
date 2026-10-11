"""Configuration loading.

All behaviour is driven by a YAML file (default ``config/chanakya.yaml``) plus
environment variables for secrets. Secrets are never stored in the YAML file:
the YAML only names the environment variable that holds them.
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Literal

import yaml
from pydantic import BaseModel, ConfigDict, Field, field_validator

from .obs import register_secret

DEFAULT_CONFIG_PATHS = ("config/chanakya.yaml", "config/chanakya.example.yaml")


class _Strict(BaseModel):
    """Unknown keys are errors: a mistyped budget or threshold must not be silently ignored."""

    model_config = ConfigDict(extra="forbid")


class AppSettings(_Strict):
    timezone: str = "Asia/Kolkata"
    data_dir: Path = Path("./data")
    database_url: str = "sqlite:///./data/chanakya.db"
    capabilities_file: Path = Path("./config/wayam_capabilities.yaml")
    reports_dir: Path = Path("./data/reports")
    report_retention: int = 30
    inbox_dir: Path = Path("./data/inbox")
    documents_dir: Path = Path("./data/documents")
    log_level: str = "INFO"
    log_format: Literal["text", "json"] = "text"  # env CHANAKYA_LOG_FORMAT overrides (see obs.py)


class HttpSettings(_Strict):
    user_agent: str = "ChanakyaBot/0.1 (procurement monitoring; contact: set-in-config)"
    timeout_s: float = 30.0
    max_retries: int = 3
    backoff_base_s: float = 2.0
    min_interval_s: float = 2.0
    respect_robots_txt: bool = True
    allowed_domains: list[str] = Field(
        default_factory=lambda: ["eprocure.gov.in", "etenders.gov.in"]
    )
    max_download_mb: int = 25
    max_redirects: int = 3
    circuit_breaker_failures: int = 5
    circuit_breaker_cooldown_s: int = 900


class SourceConfig(_Strict):
    id: str
    type: Literal["cppp", "gepnic", "manual", "fixture"]
    name: str = ""
    enabled: bool = True
    base_url: str | None = None
    feed: str | None = None  # cppp: cpppdata | mmpdata | gemdata
    max_pages: int = 30
    overlap_hours: int = 12
    fetch_detail: bool = False
    path: Path | None = None  # manual / fixture input directory
    platform_label: str | None = None
    verification_status: str = "candidate"  # verified_working | verified_restricted | candidate | unsupported


class FilteringSettings(_Strict):
    skip_expired: bool = True
    backfill_days: int = 14
    reject_threshold: float = 0.10
    candidate_threshold: float = 0.20
    strong_threshold: float = 0.50
    high_recall_categories: list[str] = Field(
        default_factory=lambda: [
            "information technology", "it", "computer software", "software", "consultancy",
            "computer data processing", "network", "surveillance", "security system",
            "info. tech. services", "scanning, digitisation services",
        ]
    )
    high_recall_types: list[str] = Field(default_factory=lambda: ["eoi", "rfp", "empanelment", "rfi"])


class SemanticSettings(_Strict):
    enabled: bool = True
    backend: Literal["lexical", "ollama"] = "lexical"
    embed_model: str = "embeddinggemma"
    candidate_threshold: float = 0.12


class LLMSettings(_Strict):
    mode: Literal["cloud", "local", "disabled", "fake"] = "disabled"
    cloud_base_url: str = "https://ollama.com"
    local_base_url: str = "http://localhost:11434"
    api_key_env: str = "OLLAMA_API_KEY"
    fast_model: str = "gpt-oss:20b"
    deep_model: str = "gpt-oss:120b"
    think: str | bool | None = "low"
    temperature: float = 0.0
    request_timeout_s: float = 120.0
    max_output_tokens_fast: int = 300
    max_output_tokens_deep: int = 1200
    max_retries: int = 2
    send_format_schema: Literal["auto", "always", "never"] = "auto"
    prompt_version: str = "v1"
    triage_band: tuple[float, float] = (0.20, 0.50)
    deep_min_score: float = 0.45


class BudgetSettings(_Strict):
    max_cost_per_run_usd: float = 0.50
    max_cost_per_day_usd: float = 2.00
    max_calls_per_run: int = 60
    max_input_tokens_per_call: int = 6000
    max_documents_per_tender: int = 3
    max_pages_per_document: int = 80
    max_deep_analyses_per_run: int = 15


class ModelPrice(_Strict):
    input: float
    cached_input: float | None = None
    output: float


class ScoringSettings(_Strict):
    weights: dict[str, float] = Field(
        default_factory=lambda: {
            "capability": 35, "eligibility": 20, "technical": 15,
            "deadline": 10, "commercial": 10, "strategic": 10,
        }
    )
    priority_thresholds: dict[str, float] = Field(default_factory=lambda: {"P1": 70, "P2": 50, "P3": 30})
    closing_soon_days: int = 7
    min_days_to_prepare: int = 5
    strategic_org_keywords: list[str] = Field(
        default_factory=lambda: [
            "smart city", "nicsi", "national informatics", "meity", "c-dac", "railway", "metro",
            "health", "aiims", "medical", "ntpc", "seci", "solar", "renewable", "coal india",
            "power grid", "police", "municipal corporation", "nhai", "airports authority", "becil",
            "telecommunications consultants",
        ]
    )
    # Win history (roadmap Phase 7). Off by default: when on, each notice BD recorded as Won with the
    # same buyer adds bonus_per_win to the strategic component, capped at max_bonus. Losses never lower it.
    win_history_enabled: bool = False
    win_history_bonus_per_win: float = Field(default=0.1, ge=0, le=1)
    win_history_max_bonus: float = Field(default=0.2, ge=0, le=1)

    @field_validator("weights")
    @classmethod
    def _weights_positive(cls, v: dict[str, float]) -> dict[str, float]:
        if any(w < 0 for w in v.values()) or sum(v.values()) <= 0:
            raise ValueError("scoring weights must be non-negative and sum to > 0")
        return v


class CalibrationSettings(_Strict):
    """`chanakya calibrate`: a weight proposal from BD labels. Never applied automatically."""

    min_labels: int = Field(default=100, ge=2)        # refuse to propose below this many labelled notices
    min_labels_per_class: int = Field(default=10, ge=1)  # and below this many in either class
    l2: float = Field(default=1.0, gt=0)              # strength of the pull toward the current weights
    cv_folds: int = Field(default=5, ge=0)            # 0 or 1 disables the held-out check


class CompanyProfile(_Strict):
    """Wayam's internal credentials. Leave unknown values as null — Chanakya will
    then report eligibility as unknown instead of guessing."""

    name: str = "Wayam AI"
    annual_turnover_inr_crore: float | None = None
    average_turnover_3y_inr_crore: float | None = None
    years_in_operation: float | None = None
    certifications: list[str] = Field(default_factory=list)
    msme_registered: bool | None = None
    dpiit_startup: bool | None = None
    past_government_projects: int | None = None
    cert_in_empanelled: bool | None = None


class EmailSettings(_Strict):
    enabled: bool = False
    provider: Literal["smtp", "file"] = "file"
    smtp_host: str = ""
    smtp_port: int = 587
    smtp_security: Literal["starttls", "ssl", "none"] = "starttls"
    username_env: str = "CHANAKYA_SMTP_USERNAME"
    password_env: str = "CHANAKYA_SMTP_PASSWORD"
    sender: str = "chanakya@example.com"
    to: list[str] = Field(default_factory=list)
    cc: list[str] = Field(default_factory=list)
    min_score: float = 50
    closing_within_days: int | None = None
    attach_excel: bool = True
    max_attachment_mb: int = 10
    subject_template: str = "CHANAKYA | Wayam AI Government Opportunities | {date}"
    outbox_dir: Path = Path("./data/outbox")
    digest: Literal["daily", "weekly"] = "daily"
    alert_on_new_p1: bool = False
    alert_on_corrigendum: bool = False
    corrigendum_alert_window_days: int = Field(default=7, ge=1)
    alert_on_deadline: bool = False
    deadline_alert_days: list[int] = Field(default_factory=lambda: [7, 2])
    max_retries: int = 3


class ScheduleSettings(_Strict):
    daily_at: str = "07:30"
    weekdays_only: bool = False
    weekly_digest_day: Literal["mon", "tue", "wed", "thu", "fri", "sat", "sun"] = "mon"


class DocumentSettings(_Strict):
    ocr_enabled: bool = False
    ocr_languages: str = "eng"
    max_zip_members: int = 200
    max_zip_total_mb: int = 200
    max_zip_ratio: int = 100
    min_chars_per_page: int = 40
    tables_enabled: bool = True              # ruled PDF tables (BOQs) rendered as delimited rows
    max_table_rows_per_document: int = 300   # beyond this, table pages keep plain text extraction


class AwardsSettings(_Strict):
    """Award-notice monitoring (CPPP "Result of Tenders"). Its own block, not a
    ``sources`` entry: awards are outcomes and never enter the tender pipeline."""

    enabled: bool = False
    source_id: str = "cppp_awards"
    name: str = "GeM-CPPP: Results of Tenders (awards)"
    url: str = "https://eprocure.gov.in/cppp/resultoftendersnew/cpppdata"
    max_pages: int = 3
    verification_status: str = "candidate"  # verified_working | verified_restricted | candidate | unsupported


class Settings(_Strict):
    app: AppSettings = Field(default_factory=AppSettings)
    http: HttpSettings = Field(default_factory=HttpSettings)
    sources: list[SourceConfig] = Field(default_factory=list)
    filtering: FilteringSettings = Field(default_factory=FilteringSettings)
    semantic: SemanticSettings = Field(default_factory=SemanticSettings)
    llm: LLMSettings = Field(default_factory=LLMSettings)
    budget: BudgetSettings = Field(default_factory=BudgetSettings)
    pricing: dict[str, ModelPrice] = Field(default_factory=dict)
    scoring: ScoringSettings = Field(default_factory=ScoringSettings)
    calibration: CalibrationSettings = Field(default_factory=CalibrationSettings)
    company_profile: CompanyProfile = Field(default_factory=CompanyProfile)
    email: EmailSettings = Field(default_factory=EmailSettings)
    schedule: ScheduleSettings = Field(default_factory=ScheduleSettings)
    documents: DocumentSettings = Field(default_factory=DocumentSettings)
    awards: AwardsSettings = Field(default_factory=AwardsSettings)

    config_path: Path | None = None

    def source(self, source_id: str) -> SourceConfig:
        for s in self.sources:
            if s.id == source_id:
                return s
        raise KeyError(f"unknown source '{source_id}'")

    def secret(self, env_name: str) -> str | None:
        value = os.environ.get(env_name)
        value = value.strip() if value else None
        register_secret(value)  # every secret handed out is redacted from later log lines
        return value

    def resolve_paths(self, base: Path) -> None:
        """Make relative paths relative to the project root (config file's parent's parent)."""
        def fix(p: Path) -> Path:
            return p if p.is_absolute() else (base / p).resolve()

        a = self.app
        a.data_dir, a.capabilities_file = fix(a.data_dir), fix(a.capabilities_file)
        a.reports_dir, a.inbox_dir, a.documents_dir = fix(a.reports_dir), fix(a.inbox_dir), fix(a.documents_dir)
        self.email.outbox_dir = fix(self.email.outbox_dir)
        if a.database_url.startswith("sqlite:///") and not a.database_url.startswith("sqlite:////"):
            rel = a.database_url.removeprefix("sqlite:///")
            if rel != ":memory:":
                a.database_url = f"sqlite:///{fix(Path(rel))}"
        for s in self.sources:
            if s.path is not None:
                s.path = fix(s.path)


def _load_dotenv(path: Path) -> None:
    """Load KEY=VALUE lines from a .env file. Existing environment variables win."""
    if not path.is_file():
        return
    for raw in path.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        key = key.strip()
        if not key or key in os.environ:
            continue
        value = value.strip()
        if len(value) >= 2 and value[0] == value[-1] and value[0] in "\"'":
            value = value[1:-1]
        os.environ[key] = value


def load_settings(path: str | Path | None = None) -> Settings:
    """Load settings from YAML. ``CHANAKYA_CONFIG`` overrides the default path.

    A project ``.env`` is loaded first so local runs see the same secrets Docker
    Compose injects via ``env_file``. Variables already set in the process win.
    """
    _load_dotenv(Path.cwd() / ".env")
    candidates: list[Path] = []
    if path:
        candidates.append(Path(path))
    elif os.environ.get("CHANAKYA_CONFIG"):
        candidates.append(Path(os.environ["CHANAKYA_CONFIG"]))
    else:
        candidates.extend(Path(p) for p in DEFAULT_CONFIG_PATHS)
    for c in candidates:
        if c.exists():
            data = yaml.safe_load(c.read_text(encoding="utf-8")) or {}
            settings = Settings.model_validate(data)
            settings.config_path = c.resolve()
            settings.resolve_paths(c.resolve().parent.parent)
            _load_dotenv(c.resolve().parent.parent / ".env")
            _apply_env_overrides(settings)
            apply_profile_overlay(settings)
            return settings
    if path or os.environ.get("CHANAKYA_CONFIG"):
        # An explicitly requested config that is missing must not fall back to defaults
        # (a run with no sources would look successful).
        raise FileNotFoundError(f"config file not found: {path or os.environ['CHANAKYA_CONFIG']}")
    settings = Settings()
    _apply_env_overrides(settings)
    return settings


def profile_overlay_path(settings: Settings) -> Path:
    """Where the desk's Company profile page saves: ``<data_dir>/<config name>.profile.yaml``.

    The file is named after the config it overlays, so the demo config and the test
    configs never pick up the figures saved for ``chanakya.yaml``.
    """
    stem = settings.config_path.stem if settings.config_path else "chanakya"
    return Path(settings.app.data_dir) / f"{stem}.profile.yaml"


def read_profile_overlay(settings: Settings) -> dict:
    """The saved overlay file as a mapping, or ``{}`` when nothing has been saved."""
    path = profile_overlay_path(settings)
    if not path.is_file():
        return {}
    try:
        data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    except yaml.YAMLError as exc:
        raise ValueError(f"saved company profile is not valid YAML: {path} ({exc})") from exc
    if not isinstance(data, dict) or not isinstance(data.get("company_profile") or {}, dict):
        raise ValueError(f"saved company profile has the wrong shape: {path}")
    return data


def apply_profile_overlay(settings: Settings) -> Settings:
    """Lay the profile saved from the desk over ``company_profile`` from the YAML.

    The YAML stays the base and is never rewritten, so its comments survive. A key in the
    saved file wins, and a saved null means "not stated" again. Unknown keys and wrong
    types are errors, the same as in the YAML.
    """
    saved = read_profile_overlay(settings).get("company_profile") or {}
    if saved:
        settings.company_profile = CompanyProfile.model_validate(
            {**settings.company_profile.model_dump(), **saved}
        )
    return settings


def _apply_env_overrides(settings: Settings) -> None:
    """A few operational overrides that are convenient in containers."""
    if v := os.environ.get("CHANAKYA_DATABASE_URL"):
        settings.app.database_url = v
    if v := os.environ.get("CHANAKYA_LLM_MODE"):
        settings.llm.mode = v  # type: ignore[assignment]
    if v := os.environ.get("CHANAKYA_EMAIL_TO"):
        settings.email.to = [x.strip() for x in v.split(",") if x.strip()]
