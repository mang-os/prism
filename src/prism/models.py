import math
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from prism.analysis import KEYWORD_FIELDS, NUMERIC_FIELDS


class Document(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    id: str
    title: str
    body: str = ""
    kind: Literal["product", "service", "document"] = "document"
    category: str | None = None
    tags: list[str] = Field(default_factory=list, max_length=64)
    price: float | None = Field(default=None, ge=0, allow_inf_nan=False)
    rating: float | None = Field(default=None, ge=0, le=5, allow_inf_nan=False)

    @field_validator("price", "rating", mode="before")
    @classmethod
    def numeric_source_type(cls, value):
        if value is not None and (isinstance(value, bool) or not isinstance(value, (int, float))):
            raise ValueError("numeric metadata must be a number, not a coerced string")
        return value

    @model_validator(mode="after")
    def sizes(self):
        if not self.id or len(self.id.encode()) > 128:
            raise ValueError("id must contain 1–128 UTF-8 bytes")
        if not self.title.strip() or len(self.title.encode()) > 1024:
            raise ValueError("title must contain 1–1024 UTF-8 bytes")
        if len((self.title + self.body).encode()) > 65536:
            raise ValueError("document text exceeds 64 KiB")
        if any(len(t.encode()) > 256 for t in self.tags):
            raise ValueError("tag exceeds 256 bytes")
        if self.category is not None and len(self.category.encode()) > 256:
            raise ValueError("category exceeds 256 bytes")
        return self


class Filter(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    field: str
    op: Literal["eq", "in", "gt", "gte", "lt", "lte"] = "eq"
    value: str | float | list[str]

    @field_validator("value", mode="before")
    @classmethod
    def reject_bool(cls, value):
        if isinstance(value, bool):
            raise ValueError("boolean is not a numeric filter value")
        return value

    @model_validator(mode="after")
    def types(self):
        if self.field in NUMERIC_FIELDS:
            if (
                self.op == "in"
                or isinstance(self.value, (str, list, bool))
                or not math.isfinite(self.value)
            ):
                raise ValueError("numeric filters require a finite number")
        elif self.field in KEYWORD_FIELDS:
            if self.op not in ("eq", "in"):
                raise ValueError("keyword fields support eq and in")
            if self.op == "eq" and not isinstance(self.value, str):
                raise ValueError("keyword eq requires a string")
            if self.op == "in" and (not isinstance(self.value, list) or not self.value):
                raise ValueError("keyword in requires a nonempty string list")
        else:
            raise ValueError("unknown filter field")
        return self


class SearchRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    q: str = Field(min_length=1, max_length=2048)
    query_mode: Literal["plain", "dsl"] = "plain"
    mode: Literal["lexical", "semantic", "hybrid"] | None = None
    filters: list[Filter] = Field(default_factory=list, max_length=32)
    top_k: int = Field(default=10, ge=1, le=100)
    candidate_k: int = Field(default=100, ge=1, le=1000)
    typo_tolerance: bool = True
    synonyms: bool = True
    timeout_ms: int = Field(default=500, ge=10, le=5000)
    allow_partial: bool = True
    allow_lexical_fallback: bool = False
    explain: bool = False

    @field_validator("q")
    @classmethod
    def nonempty(cls, v):
        if not v.strip():
            raise ValueError("query must not be blank")
        return v

    @model_validator(mode="after")
    def candidate_limit(self):
        if self.candidate_k < self.top_k:
            raise ValueError("candidate_k must be >= top_k")
        return self
