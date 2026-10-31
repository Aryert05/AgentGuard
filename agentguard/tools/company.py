"""Validated company facts from a small local sample dataset."""
from __future__ import annotations

from pydantic import BaseModel


class CompanyArgs(BaseModel):
    name: str


class Company(BaseModel):
    name: str
    industry: str
    headquarters: str
    summary: str


COMPANIES = {
    "tata motors": Company(name="Tata Motors", industry="Automotive", headquarters="Mumbai, India", summary="Indian automotive manufacturer producing passenger and commercial vehicles."),
    "infosys": Company(name="Infosys", industry="Information technology", headquarters="Bengaluru, India", summary="Indian multinational information technology services and consulting company."),
    "wipro": Company(name="Wipro", industry="Information technology", headquarters="Bengaluru, India", summary="Indian multinational technology services and consulting company."),
}


def get_company(name: str) -> dict[str, str]:
    if not name.strip():
        raise ValueError("Company name must not be empty")
    company = COMPANIES.get(name.strip().lower())
    if company is None:
        raise LookupError(f"Unknown company in local sample dataset: {name}")
    return company.model_dump()
