"""Validated job options shared by the API, the worker and the MCP server."""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field, model_validator


class JobOptions(BaseModel):
    max_clips: int = Field(5, ge=1, le=30)
    min_seconds: float = Field(15, ge=5, le=120)
    max_seconds: float = Field(60, ge=10, le=300)
    language: str | None = Field(None, max_length=8)
    caption_style: Literal["pop", "classic", "boxed", "none"] = "pop"
    layout: Literal["auto", "fill", "fit_blur"] = "auto"
    hook_title: bool = True
    platform: Literal["shorts", "tiktok", "reels"] = "shorts"
    use_llm: bool = True

    @model_validator(mode="after")
    def _check_range(self) -> JobOptions:
        if self.min_seconds >= self.max_seconds:
            raise ValueError("min_seconds must be smaller than max_seconds")
        return self


class RerenderOptions(BaseModel):
    clip_id: str
    start: float | None = Field(None, ge=0)
    end: float | None = Field(None, ge=0)
    caption_style: Literal["pop", "classic", "boxed", "none"] | None = None
    layout: Literal["auto", "fill", "fit_blur"] | None = None
    hook_title: bool | None = None
