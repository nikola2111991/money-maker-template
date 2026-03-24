"""Pydantic models for Money Maker schema validation.

Validates schema_draft.json structure to catch typos and missing fields
early, before rendering or deployment.
"""
from __future__ import annotations

from typing import Optional

from pydantic import BaseModel, Field, field_validator


class Review(BaseModel):
    reviewer_name: str
    text: str
    rating: Optional[int] = None


class Service(BaseModel):
    title: str
    description: str = ""
    image_url: str = ""
    duration: str = ""
    price: str = ""


class Benefit(BaseModel):
    title: str
    description: str


class Problem(BaseModel):
    title: str
    treatment: str
    description: str
    duration: str = ""
    price: str = ""
    recovery: str = ""


class FAQ(BaseModel):
    question: str
    answer: str


class CoreValue(BaseModel):
    title: str
    description: str
    ikona: str = "check"

    @field_validator("ikona")
    @classmethod
    def valid_icon(cls, v: str) -> str:
        if v not in {"heart", "clock", "check", "shield"}:
            return "check"
        return v


class SchemaDraft(BaseModel):
    """Schema for lead website data.

    Validates the structure of schema_draft.json files.
    Use extra="allow" to permit meta fields (_score, _category, etc.).
    """

    # Required identifiers
    slug: str
    name: str
    name_short: str = ""
    city: str

    # Owner
    owner: str = ""
    owner_short: str = ""

    # Contact
    address: str = ""
    phone: str = ""
    phone_display: str = ""
    email: str = ""

    # Ratings
    rating: float = 0
    review_count: int = 0

    # Copy fields
    hero_headline: str = ""
    hero_subtitle: str = ""
    about_headline: str = ""
    about_subtitle: str = ""
    about_story: str = ""
    about_blockquote: str = ""
    benefits_headline: str = ""
    services_subtitle: str = ""
    contact_subtitle: str = ""

    # Arrays
    reviews: list[Review] = Field(default_factory=list)
    services: list[Service] = Field(default_factory=list)
    benefits: list[Benefit] = Field(default_factory=list)
    problems: list[Problem] = Field(default_factory=list)
    faq: list[FAQ] = Field(default_factory=list)
    core_values: list[CoreValue] = Field(default_factory=list)
    about_stats: list[dict] = Field(default_factory=list)
    about_paragraphs: list[str] = Field(default_factory=list)

    # Service area
    service_area: str = ""

    # Optional metadata
    district: str = ""
    specialization: str = ""
    years_established: int | str | None = None
    founded: int | str | None = None
    hours: list[dict] | str = ""
    hero_image: str = ""
    google_maps_url: str = ""
    google_maps_embed_url: str = ""
    base_url: str = ""
    is_city_level: bool = False

    # Social
    facebook: str = ""
    instagram: str = ""

    # Grammatical forms (Serbian)
    name_genitive: str = ""
    name_locative: str = ""

    model_config = {"extra": "allow"}
