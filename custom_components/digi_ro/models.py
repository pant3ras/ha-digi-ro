"""Data models for the Digi Romania integration.

Adapted from HAForgeLabs/utilitati_romania (MIT, © 2026 Marius Onițiu;
portions © Cristian Necrea).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Any


@dataclass(slots=True)
class InvoiceSummary:
    """A row parsed from the invoices listing page."""

    invoice_id: str
    address_key: str
    address: str
    issue_date: str
    due_date: str
    description: str
    amount: float


@dataclass(slots=True)
class InvoiceDetail:
    """Detail fetched per invoice (total/rest/status/pdf/services)."""

    invoice_id: str
    invoice_number: str | None
    issue_date: str | None
    due_date: str | None
    total: float | None
    rest: float | None
    status: str | None
    pdf_url: str | None
    services: list[dict[str, Any]] = field(default_factory=list)


@dataclass(slots=True)
class AddressInvoices:
    """All invoices for one address: newest first + the latest invoice."""

    address_key: str
    address: str
    latest: dict[str, Any]
    history: list[dict[str, Any]]
    unpaid_count: int
    unpaid_total: float


@dataclass(slots=True)
class DigiData:
    """The full snapshot the coordinator hands to the sensors."""

    invoices_by_address: dict[str, AddressInvoices]
    last_update: datetime


@dataclass(slots=True)
class TwoFactorOption:
    value: str
    label: str


@dataclass(slots=True)
class TwoFactorContext:
    methods: dict[str, dict[str, Any]]
    html: str
    selections: dict[str, str] = field(default_factory=dict)


@dataclass(slots=True)
class AddressOption:
    value: str
    label: str
