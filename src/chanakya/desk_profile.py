"""Desk routes for the company profile eligibility is checked against.

``company_profile`` in the YAML config is the base and is never rewritten, so
its comments and layout survive. What a person saves on the desk goes to a
small overlay file, ``<data_dir>/<config name>.profile.yaml``, which
``load_settings`` lays over the base on every load. The CLI, a watch and the
desk therefore all assess against the same figures after the next start.

Nothing here supplies a value. A field nobody filled in is stored as null and
is assessed as unknown.
"""

from __future__ import annotations

import os
from datetime import datetime
from typing import Any

import yaml
from fastapi import APIRouter, Body, HTTPException
from pydantic import BaseModel, ConfigDict, Field, StrictBool, ValidationError, field_validator

from . import server
from .config import CompanyProfile, Settings, apply_profile_overlay, profile_overlay_path, read_profile_overlay

router = APIRouter()

HEADER = (
    "# Saved from the desk's Company profile page. It is laid over company_profile in the config.\n"
    "# null means not stated: eligibility for that clause is reported as unknown, never assumed.\n"
    "# Enter verified figures only.\n"
)


class ProfileBody(BaseModel):
    """What the form may send. Every field is optional. Absent and null both mean not stated."""

    model_config = ConfigDict(extra="forbid")

    name: str | None = Field(default=None, max_length=120)
    annual_turnover_inr_crore: float | None = Field(default=None, ge=0, le=1_000_000)
    average_turnover_3y_inr_crore: float | None = Field(default=None, ge=0, le=1_000_000)
    years_in_operation: float | None = Field(default=None, ge=0, le=200)
    certifications: list[str] = Field(default_factory=list, max_length=30)
    msme_registered: StrictBool | None = None
    dpiit_startup: StrictBool | None = None
    past_government_projects: int | None = Field(default=None, ge=0, le=100_000)
    cert_in_empanelled: StrictBool | None = None

    @field_validator("name")
    @classmethod
    def _name(cls, value: str | None) -> str | None:
        cleaned = " ".join("".join(ch for ch in (value or "") if ch.isprintable()).split())
        return cleaned or None

    @field_validator("certifications")
    @classmethod
    def _certifications(cls, value: list[str]) -> list[str]:
        out: list[str] = []
        seen: set[str] = set()
        for item in value:
            cleaned = " ".join("".join(ch for ch in str(item) if ch.isprintable()).split())
            if not cleaned:
                continue
            if len(cleaned) > 80:
                raise ValueError("a certificate name is at most 80 characters")
            key = cleaned.upper().replace(" ", "")
            if key not in seen:
                seen.add(key)
                out.append(cleaned)
        return out


def _payload(settings: Settings) -> dict[str, Any]:
    path = profile_overlay_path(settings)
    try:
        saved = read_profile_overlay(settings)
        # desk_settings() normally arrives with the overlay applied. Applying it to a copy
        # again changes nothing, and keeps this route right when it was handed a bare config.
        profile = apply_profile_overlay(settings.model_copy(deep=True)).company_profile
    except ValueError as exc:
        raise HTTPException(500, str(exc)) from exc
    config = settings.config_path
    return {
        "profile": profile.model_dump(),
        "saved": bool(saved),
        "saved_at": saved.get("saved_at"),
        "stored_in": f"{path.parent.name}/{path.name}",
        "config_file": f"{config.parent.name}/{config.name}" if config else None,
    }


@router.get("/api/profile")
def read_profile() -> dict[str, Any]:
    return _payload(server.desk_settings())


@router.put("/api/profile")
def save_profile(body: dict[str, Any] = Body(...)) -> dict[str, Any]:  # noqa: B008 - FastAPI's own idiom
    try:
        parsed = ProfileBody.model_validate(body)
    except ValidationError as exc:
        problems = [f"{'.'.join(str(part) for part in err['loc']) or 'profile'}: {err['msg']}" for err in exc.errors()]
        raise HTTPException(400, "; ".join(problems[:6])) from exc
    settings = server.desk_settings()
    fields = parsed.model_dump()
    if fields["name"] is None:
        del fields["name"]  # an empty name falls back to the one in the config
    # The saved file must load cleanly next time, so check it the way load_settings will.
    CompanyProfile.model_validate({**settings.company_profile.model_dump(), **fields})
    path = profile_overlay_path(settings)
    path.parent.mkdir(parents=True, exist_ok=True)
    document = {"saved_at": datetime.utcnow().isoformat(timespec="seconds") + "Z", "company_profile": fields}
    partial = path.with_name(path.name + ".part")
    partial.write_text(HEADER + yaml.safe_dump(document, sort_keys=False, allow_unicode=True), encoding="utf-8")
    os.replace(partial, path)
    return _payload(settings)
