"""Application settings, loaded from environment / .env.

Every value has a safe local default so a fresh clone runs with zero config.
"""

import secrets
from functools import lru_cache
from pathlib import Path
from typing import Literal

from pydantic import AliasChoices, Field
from pydantic_settings import BaseSettings, SettingsConfigDict

BACKEND_DIR = Path(__file__).resolve().parent.parent
REPO_ROOT = BACKEND_DIR.parent

# Fallback HMAC key so a dev session works without extra config. Evidence URLs
# signed with it stop working when the process restarts (TTL is minutes anyway).
_PROCESS_SIGNING_SECRET = secrets.token_urlsafe(32)


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=(REPO_ROOT / ".env", BACKEND_DIR / ".env"),
        env_file_encoding="utf-8",
        extra="ignore",
    )

    # --- app ---
    app_name: str = "Rebuild API"
    environment: str = "development"  # development | staging | production
    debug: bool = True

    # CORS: Vite dev server by default.
    cors_origins: str = "http://localhost:5173,http://127.0.0.1:5173"

    # --- storage ---
    # Uploaded room photos / receipts live here. Gitignored.
    upload_dir: Path = BACKEND_DIR / "uploads"
    max_upload_bytes: int = 25 * 1024 * 1024  # 25 MB
    allowed_content_types: str = (
        "image/jpeg,image/png,image/webp,image/heic,application/pdf"
    )

    # --- CREAO agent (Rebuild Evidence Investigator) ---
    # "mock" = deterministic stub, never touches the network, always badged as
    # simulated. "live" = real app-scoped API Trigger calls. There is no
    # automatic fallback between them: an unconfigured live setup fails loudly
    # (503) instead of quietly returning fake evidence.
    creao_mode: Literal["mock", "live"] = "mock"

    # App-scoped API Trigger (the path Person 2 verified).
    # App ID of the saved, team-owned Rebuild Evidence Investigator.
    creao_app_id: str = "9819d9a5-89e0-4d62-a284-91bac67f8cd1"
    # App-scoped API Trigger key (`capi_…`) created from the agent detail page.
    # Server-side only — never expose to the browser.
    creao_app_api_key: str = ""
    creao_app_base_url: str = "https://agent.creao.ai/api/v1"

    # --- second agent: Rebuild Product Researcher (own app + credential) ---
    # Canonical names are CREAO_RESEARCH_*; the earlier CREAO_RESEARCHER_* names
    # are still accepted so an existing .env keeps working.
    creao_research_app_id: str = Field(
        default="a7260cbc-8253-4fa7-88d3-c644267e3221",
        validation_alias=AliasChoices("CREAO_RESEARCH_APP_ID", "CREAO_RESEARCHER_APP_ID"),
    )
    creao_research_api_key: str = Field(
        default="",
        validation_alias=AliasChoices("CREAO_RESEARCH_API_KEY", "CREAO_RESEARCHER_API_KEY"),
    )
    creao_research_poll_interval_seconds: float = 3.0
    creao_research_timeout_seconds: float = 240.0

    # How evidence reaches the agent. Person 2 verified that the app-scoped
    # trigger accepts **public HTTPS URLs** in `room_photos` / `receipt_files`.
    #   unset    → refuse to start a run (default; no wasted credits)
    #   url      → short-lived signed URLs served by this backend (real path)
    #   demo_url → Person 2's staged GitHub demo files (fixtures only)
    #   none     → text only, declared as carrying no evidence
    creao_evidence_transport: Literal["unset", "url", "demo_url", "none"] = "unset"

    # --- externally reachable evidence URLs ---
    # CREAO fetches the files from its own cloud, so this must be a public
    # origin (tunnel, deployed host, or a bucket/CDN in front of us).
    # Loopback/private hosts are rejected because CREAO cannot reach them.
    rebuild_public_base_url: str = ""
    # HMAC key for evidence URLs. Empty = random per-process key (fine for a
    # dev session, URLs stop working across restarts).
    rebuild_evidence_signing_secret: str = ""
    # How long an evidence URL stays valid. Short by design: receipts must not
    # live at a permanent public address.
    rebuild_evidence_url_ttl_seconds: int = 900
    # Allow http:// and private/loopback hosts. Tests only — CREAO cannot reach
    # a developer laptop.
    creao_allow_private_evidence_hosts: bool = False
    # Verify every evidence URL answers 200 with the expected content type
    # before a run is created.
    creao_verify_evidence_urls: bool = True
    # Staged demo evidence (Person 2's files on the agent branch). Demo only.
    creao_demo_evidence_base_url: str = (
        "https://raw.githubusercontent.com/anushkaboran/rebuild/agent/demo/"
    )

    # Run polling.
    creao_poll_interval_seconds: float = 3.0
    creao_run_timeout_seconds: float = 300.0
    creao_http_timeout_seconds: float = 60.0
    # Agent contract: at most 10 attached files combined across both file fields.
    creao_max_evidence_files: int = 10

    # --- persistence backend ---
    # "memory"    → in-process dicts only (tests, throwaway demos)
    # "spacetime" → SpacetimeDB is authoritative; the dicts act as a write-through
    #               cache hydrated at startup. Explicit — there is no silent
    #               fallback if the database is unreachable.
    store_backend: Literal["memory", "spacetime"] = "memory"
    # Optional shared secret for /api/admin/* (export/import). When unset the
    # routes are limited to loopback callers.
    admin_token: str = ""
    spacetimedb_url: str = "http://127.0.0.1:3000"
    spacetimedb_db: str = "rebuild"

    # --- SpacetimeDB (legacy mirror settings) ---
    spacetime_host: str = "http://127.0.0.1:3000"
    spacetime_db: str = "rebuild"
    spacetime_module: str = "rebuild"
    spacetime_enabled: bool = False

    # --- helpers ---
    @property
    def cors_origin_list(self) -> list[str]:
        return [o.strip() for o in self.cors_origins.split(",") if o.strip()]

    @property
    def allowed_content_type_set(self) -> set[str]:
        return {c.strip().lower() for c in self.allowed_content_types.split(",") if c.strip()}

    @property
    def creao_live(self) -> bool:
        return self.creao_mode == "live"

    @property
    def creao_configured(self) -> bool:
        """True only when live mode has everything it needs to call the API."""
        return bool(
            self.creao_live
            and self.creao_app_api_key
            and self.creao_app_id
            and self.creao_app_base_url
        )

    @property
    def creao_missing_settings(self) -> list[str]:
        if not self.creao_live:
            return []
        missing = []
        if not self.creao_app_api_key:
            missing.append("CREAO_APP_API_KEY")
        if not self.creao_app_id:
            missing.append("CREAO_APP_ID")
        if not self.creao_app_base_url:
            missing.append("CREAO_APP_BASE_URL")
        return missing

    @property
    def research_configured(self) -> bool:
        return bool(
            self.creao_live
            and self.creao_research_app_id
            and self.creao_research_api_key
            and self.creao_app_base_url
        )

    @property
    def research_missing_settings(self) -> list[str]:
        if not self.creao_live:
            return []
        missing = []
        if not self.creao_research_api_key:
            missing.append("CREAO_RESEARCH_API_KEY")
        if not self.creao_research_app_id:
            missing.append("CREAO_RESEARCH_APP_ID")
        return missing

    @property
    def creao_evidence_ready(self) -> bool:
        """True when the declared transport can actually deliver evidence."""
        transport = self.creao_evidence_transport
        if transport == "none":
            return True
        if transport == "url":
            return bool(self.rebuild_public_base_url)
        if transport == "demo_url":
            return bool(self.creao_demo_evidence_base_url)
        return False

    @property
    def evidence_signing_secret(self) -> str:
        return self.rebuild_evidence_signing_secret or _PROCESS_SIGNING_SECRET


@lru_cache
def get_settings() -> Settings:
    return Settings()


settings = get_settings()
