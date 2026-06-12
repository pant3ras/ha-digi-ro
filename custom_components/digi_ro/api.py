"""Async client for Digi Romania's My Account.

Full email/password login with SMS/email 2FA, address selection, and invoice
parsing (Digi has no public JSON API — everything is server-rendered HTML).

The login/2FA/address flow and the HTML parsing are adapted from
HAForgeLabs/utilitati_romania (MIT, © 2026 Marius Onițiu; portions
© Cristian Necrea), trimmed to this integration's scope and wired onto Home
Assistant's aiohttp client session (with a persistable cookie jar).
"""

from __future__ import annotations

import asyncio
import json
import logging
import re
from datetime import datetime
from html import unescape
from typing import Any
from urllib.parse import urljoin

import aiohttp
from yarl import URL

from .const import (
    ADDRESS_CONFIRM_URL,
    ADDRESS_SELECT_URL,
    BASE_URL,
    INVOICES_URL,
    LOGIN_URL,
    TWO_FA_SEND_URL,
    TWO_FA_URL,
    TWO_FA_VALIDATE_URL,
    USER_AGENT,
)
from .models import (
    AddressInvoices,
    AddressOption,
    DigiData,
    InvoiceDetail,
    InvoiceSummary,
    TwoFactorContext,
    TwoFactorOption,
)

_LOGGER = logging.getLogger(__name__)


class DigiError(Exception):
    """Base Digi exception."""


class DigiAuthError(DigiError):
    """Credentials invalid."""


class DigiTwoFactorRequired(DigiError):
    """2FA step required."""


class DigiTwoFactorError(DigiError):
    """2FA validation failed."""


class DigiReauthRequired(DigiError):
    """Saved session expired — a fresh login is needed."""


# -- Parsing patterns (verbatim from the reference; validated against the live page) --
RE_INPUT_TAG = re.compile(r"<input[^>]*>", re.I | re.S)
RE_LABEL_FOR = re.compile(
    r'<label[^>]+for=["\']([^"\']+)["\'][^>]*>(.*?)</label>', re.I | re.S
)
RE_ADDRESS_OPTION = re.compile(
    r'<option[^>]+id=["\'](address-[^"\']+)["\'][^>]*>(.*?)</option>', re.I | re.S
)
RE_SCRIPT_CFG = re.compile(
    r'<script[^>]+id=["\']client-invoices-cfg["\'][^>]*>(.*?)</script>', re.I | re.S
)
RE_ROW = re.compile(
    r'<div class=["\']my-account-tbl-row["\'][^>]*data-invoice-address=["\']([^"\']+)["\'][^>]*>\s*'
    r'<div class=["\']my-account-tbl-col date["\']>\s*(.*?)\s*</div>\s*'
    r'<div class=["\']my-account-tbl-col description["\']>\s*(.*?)\s*<span>\s*(.*?)\s*</span>\s*</div>\s*'
    r'<div class=["\']my-account-tbl-col amount["\']>\s*(.*?)\s*</div>',
    re.I | re.S,
)
RE_CURRENT_ROW = re.compile(
    r'<div class=["\']my-account-tbl-row["\'][^>]*data-invoice-address=["\']([^"\']+)["\'][^>]*>\s*'
    r'<div class=["\']my-account-tbl-col select check["\']>\s*'
    r'<button[^>]*data-invoices-id=["\'](\d+)["\'][^>]*>.*?</button>\s*</div>\s*'
    r'<div class=["\']my-account-tbl-col date["\']>\s*(.*?)\s*</div>\s*'
    r'<div class=["\']my-account-tbl-col description["\']>\s*(.*?)\s*<span>\s*(.*?)\s*</span>\s*</div>\s*'
    r'<div class=["\']my-account-tbl-col amount["\']>\s*(.*?)\s*</div>',
    re.I | re.S,
)
RE_DETAILS_TITLE = re.compile(r"Factura\s+([^<]+?)\s+din data de\s+([0-9.\-/]+)", re.I | re.S)
RE_PDF = re.compile(
    r'href=["\']([^"\']*?/my-account/invoices/pdf-download[^"\']+)["\']', re.I
)
RE_SERVICE_ROW = re.compile(
    r'<div class=["\']popup-content-item["\']>\s*<div class=["\']name["\']>\s*(.*?)\s*</div>\s*'
    r'<div class=["\']price["\']>\s*(.*?)\s*</div>',
    re.I | re.S,
)
RE_HEX32 = re.compile(r"\b[a-f0-9]{32}\b", re.I)
RE_PHONE_PARAM = re.compile(
    r"(?:phone|form-phone-number-confirm|phone-number-confirm)[^a-f0-9]{0,40}([a-f0-9]{32})",
    re.I | re.S,
)
RE_SELECT_BLOCK = re.compile(
    r'<select[^>]*(?:id|name)=["\']([^"\']+)["\'][^>]*>(.*?)</select>', re.I | re.S
)
RE_OPTION_TAG = re.compile(
    r'<option[^>]*value=["\']([^"\']*)["\'][^>]*>(.*?)</option>', re.I | re.S
)
RE_LABEL_VALUE_MONEY = re.compile(
    r">\s*(Total|Rest)\s*<.*?>\s*([0-9]+(?:(?:[.,]|&period;)[0-9]{2})?)\s*LEI", re.I | re.S
)
RE_LABEL_VALUE_TEXT = re.compile(r">\s*Status\s*<.*?>\s*([^<]+)", re.I | re.S)


class DigiApiClient:
    """Drives the Digi My Account login + invoice fetch."""

    def __init__(self, session: aiohttp.ClientSession) -> None:
        # Expect an isolated session (async_create_clientsession) so Digi's
        # cookies never touch HA's shared jar.
        self._session = session
        self._headers = {
            "User-Agent": USER_AGENT,
            "Accept-Language": "ro-RO,ro;q=0.9,en-US;q=0.8,en;q=0.7",
            "Referer": BASE_URL,
            "Origin": BASE_URL,
        }

    async def _request(self, method: str, url: str, **kwargs: Any) -> aiohttp.ClientResponse:
        headers = dict(self._headers)
        headers.update(kwargs.pop("headers", {}))
        return await self._session.request(method, url, headers=headers, **kwargs)

    @staticmethod
    async def _text(resp: aiohttp.ClientResponse) -> str:
        return await resp.text(errors="ignore")

    # -- Cookie persistence ---------------------------------------------------

    def export_cookies(self) -> list[dict[str, Any]]:
        cookies: list[dict[str, Any]] = []
        for cookie in self._session.cookie_jar:
            cookies.append(
                {
                    "key": cookie.key,
                    "value": cookie.value,
                    "domain": cookie["domain"],
                    "path": cookie["path"],
                }
            )
        return cookies

    def import_cookies(self, cookies: list[dict[str, Any]] | None) -> None:
        jar = self._session.cookie_jar
        jar.clear()
        for item in cookies or []:
            domain = str(item.get("domain", "")).strip()
            key = str(item.get("key", "")).strip()
            if not domain or not key:
                continue
            jar.update_cookies(
                {key: str(item.get("value", ""))},
                response_url=URL(f"https://{domain.lstrip('.')}"),
            )

    # -- Login + 2FA ----------------------------------------------------------

    async def begin_login(self, email: str, password: str) -> tuple[str, str]:
        """POST credentials. Returns (final_url, html); raises on bad creds."""
        self._session.cookie_jar.clear()
        payload = {
            "signin-input-app": "0",
            "signin-input-email": email,
            "signin-input-password": password,
            "signin-submit-button": "",
        }
        try:
            resp = await self._request(
                "POST",
                LOGIN_URL,
                data=payload,
                allow_redirects=True,
                headers={"Content-Type": "application/x-www-form-urlencoded"},
            )
            text = await self._text(resp)
        except aiohttp.ClientError as err:
            raise DigiError(f"Network error during login: {err}") from err

        final_url = str(resp.url)
        if "auth/login" in final_url and "2fa" not in final_url:
            raise DigiAuthError("Invalid credentials")
        return final_url, text

    async def get_2fa_context(self, html: str | None = None) -> TwoFactorContext:
        if html is None:
            resp = await self._request("GET", TWO_FA_URL, allow_redirects=True)
            html = await self._text(resp)
        methods = self._parse_2fa_context(html)
        if not methods:
            raise DigiTwoFactorRequired("Could not parse 2FA page")
        return TwoFactorContext(methods=methods, html=html)

    async def send_2fa_code(
        self, context: TwoFactorContext, method: str, target_value: str | None = None
    ) -> None:
        selected = context.methods.get(method)
        if not selected:
            raise DigiTwoFactorError(f"2FA method '{method}' is not available")

        payload = dict(selected["send_payload"])
        target_options = selected.get("target_options") or []
        default_target = selected.get("default_target")

        if method == "sms" and (target_options or default_target):
            resolved = (target_value or default_target or "").strip()
            if target_options and not resolved:
                if len(target_options) == 1:
                    resolved = str(target_options[0].get("value") or "").strip()
                else:
                    raise DigiTwoFactorError("2FA target selection is required")
            if target_options:
                allowed = {str(o.get("value") or "").strip() for o in target_options}
                if resolved not in allowed:
                    raise DigiTwoFactorError("Invalid 2FA target selected")
            if not resolved:
                raise DigiTwoFactorError("2FA target could not be determined")
            payload["phone"] = resolved
            context.selections[method] = resolved

        try:
            resp = await self._request(
                "POST",
                selected["send_url"],
                data=payload,
                allow_redirects=True,
                headers={"Content-Type": "application/x-www-form-urlencoded; charset=UTF-8"},
            )
        except aiohttp.ClientError as err:
            raise DigiTwoFactorError(f"Failed to send code: {err}") from err
        if resp.status >= 400:
            raise DigiTwoFactorError(f"Failed to send code: HTTP {resp.status}")

    async def validate_2fa_code(
        self, context: TwoFactorContext, method: str, code: str
    ) -> tuple[str, str]:
        selected = context.methods.get(method)
        if not selected:
            raise DigiTwoFactorError(f"2FA method '{method}' is not available")

        payload = dict(selected["validate_payload"])
        chosen = context.selections.get(method) or selected.get("default_target")
        if method == "sms" and chosen:
            payload["phone"] = chosen
        payload["code"] = code.strip()

        try:
            resp = await self._request(
                "POST",
                TWO_FA_VALIDATE_URL,
                data=payload,
                allow_redirects=True,
                headers={"Content-Type": "application/x-www-form-urlencoded; charset=UTF-8"},
            )
            raw = await self._text(resp)
        except aiohttp.ClientError as err:
            raise DigiTwoFactorError(f"Failed to validate code: {err}") from err

        data: dict[str, Any] = {}
        try:
            data = json.loads(raw) if raw else {}
        except json.JSONDecodeError:
            pass
        if resp.status >= 400:
            raise DigiTwoFactorError(f"Failed to validate code: HTTP {resp.status}")
        if data and not data.get("success", True):
            raise DigiTwoFactorError(data.get("message") or "Invalid verification code")

        follow = await self._request("GET", ADDRESS_SELECT_URL, allow_redirects=True)
        return str(follow.url), await self._text(follow)

    async def get_address_options(self, html: str | None = None) -> list[AddressOption]:
        if html is None:
            resp = await self._request("GET", ADDRESS_SELECT_URL, allow_redirects=True)
            html = await self._text(resp)
        options = self._extract_radio_options(html)
        if not options:
            for _, label in RE_ADDRESS_OPTION.findall(html):
                clean = self._clean_text(label)
                if clean and clean.lower() != "toate adresele":
                    options.append(AddressOption(value="", label=clean))
        return options

    async def confirm_address(self, address_id: str) -> None:
        payload = {"address": address_id, "order-btn-id": ""}
        try:
            resp = await self._request(
                "POST",
                ADDRESS_CONFIRM_URL,
                data=payload,
                allow_redirects=True,
                headers={"Content-Type": "application/x-www-form-urlencoded; charset=UTF-8"},
            )
            text = await self._text(resp)
        except aiohttp.ClientError as err:
            raise DigiError(f"Address confirmation failed: {err}") from err
        if resp.status >= 400:
            raise DigiError(f"Address confirmation failed: HTTP {resp.status}")
        if text:
            try:
                data = json.loads(text)
                if not data.get("success", True):
                    raise DigiError(data.get("message") or "Address confirmation failed")
            except json.JSONDecodeError:
                pass

    # -- Data fetch -----------------------------------------------------------

    async def async_fetch_data(self, history_limit: int = 6) -> DigiData:
        try:
            resp = await self._request("GET", INVOICES_URL, allow_redirects=True)
            html = await self._text(resp)
        except aiohttp.ClientError as err:
            raise DigiError(f"Network error fetching invoices: {err}") from err

        final_url = str(resp.url)
        if "/auth/" in final_url:
            raise DigiReauthRequired("Session expired")

        rows = self._parse_invoice_page(html)
        if not rows:
            raise DigiError("No invoices found in Digi page")

        recent_by_address: dict[str, list[str]] = {}
        for row in rows:
            bucket = recent_by_address.setdefault(row.address_key, [])
            if len(bucket) < history_limit:
                bucket.append(row.invoice_id)

        wanted = {iid for ids in recent_by_address.values() for iid in ids}
        details: dict[str, InvoiceDetail] = {}
        for invoice_id in wanted:
            try:
                details[invoice_id] = await self._fetch_invoice_details(invoice_id)
            except DigiError as err:
                _LOGGER.debug("Digi invoice %s detail fetch failed: %s", invoice_id, err)
            await asyncio.sleep(0.15)

        grouped: dict[str, list[dict[str, Any]]] = {}
        for row in rows:
            detail = details.get(row.invoice_id)
            item = {
                "invoice_id": row.invoice_id,
                "address": row.address,
                "issue_date": (detail.issue_date if detail else None) or row.issue_date,
                "due_date": (detail.due_date if detail else None) or row.due_date,
                "description": row.description,
                "amount": (detail.total if detail and detail.total is not None else row.amount),
                "rest": (detail.rest if detail and detail.rest is not None else None),
                "status": detail.status if detail else None,
                "invoice_number": detail.invoice_number if detail else None,
                "pdf_url": detail.pdf_url if detail else None,
                "services": detail.services if detail else [],
            }
            grouped.setdefault(row.address_key, []).append(item)

        invoices_by_address: dict[str, AddressInvoices] = {}
        for address_key, items in grouped.items():
            items.sort(key=lambda x: self._parse_date_for_sort(x.get("issue_date")), reverse=True)
            unpaid = [i for i in items if self._is_unpaid(i)]
            invoices_by_address[address_key] = AddressInvoices(
                address_key=address_key,
                address=items[0]["address"],
                latest=items[0],
                history=items,
                unpaid_count=len(unpaid),
                unpaid_total=round(sum((i.get("rest") or i.get("amount") or 0) for i in unpaid), 2),
            )

        return DigiData(invoices_by_address=invoices_by_address, last_update=datetime.now())

    @staticmethod
    def _is_unpaid(item: dict[str, Any]) -> bool:
        rest = item.get("rest")
        if rest is not None:
            return rest > 0
        return "neach" in (item.get("status") or "").lower()

    # -- HTML parsing helpers (from the reference) ----------------------------

    def _parse_invoice_page(self, html: str) -> list[InvoiceSummary]:
        addresses = {key: self._clean_text(label) for key, label in RE_ADDRESS_OPTION.findall(html)}
        rows: list[InvoiceSummary] = []

        def addr_label(key: str) -> str:
            return addresses.get(key, key.replace("address-", "").replace("_", " ").strip())

        # "Facturi curente" = unpaid; rows carry their own invoice id.
        current_html = self._extract_section(html, "Facturi curente", "Facturi achitate")
        current_ids: list[str] = []
        if current_html:
            for address_key, invoice_id, issue, desc, due, amount in RE_CURRENT_ROW.findall(current_html):
                current_ids.append(str(invoice_id))
                rows.append(
                    InvoiceSummary(
                        invoice_id=str(invoice_id),
                        address_key=address_key,
                        address=addr_label(address_key),
                        issue_date=self._clean_text(issue),
                        due_date=self._clean_text(due),
                        description=self._clean_text(desc),
                        amount=self._parse_money(amount) or 0.0,
                    )
                )

        # Archive rows ("Facturi achitate") don't carry ids in the row; they
        # come from the client-invoices-cfg JSON (minus the current ones).
        archive_html = self._extract_section(html, "Facturi achitate", None)
        archive_ids: list[str] = []
        cfg_match = RE_SCRIPT_CFG.search(html)
        if cfg_match:
            try:
                cfg = json.loads(unescape(cfg_match.group(1).strip()))
                remaining = list(current_ids)
                for entry in cfg:
                    iid = str(entry.get("id")) if entry.get("id") else None
                    if not iid:
                        continue
                    if iid in remaining:
                        remaining.remove(iid)
                        continue
                    archive_ids.append(iid)
            except json.JSONDecodeError:
                _LOGGER.debug("Digi client-invoices-cfg JSON not parseable")

        for idx, match in enumerate(RE_ROW.findall(archive_html or html)):
            if idx >= len(archive_ids):
                break
            address_key, issue, desc, due, amount = match
            rows.append(
                InvoiceSummary(
                    invoice_id=archive_ids[idx],
                    address_key=address_key,
                    address=addr_label(address_key),
                    issue_date=self._clean_text(issue),
                    due_date=self._clean_text(due),
                    description=self._clean_text(desc),
                    amount=self._parse_money(amount) or 0.0,
                )
            )
        return rows

    async def _fetch_invoice_details(self, invoice_id: str) -> InvoiceDetail:
        url = f"{BASE_URL}/my-account/invoices/details?invoice_id={invoice_id}"
        resp = await self._request(
            "POST",
            url,
            data={"url": f"/my-account/invoices/details?invoice_id={invoice_id}", "id": invoice_id},
            allow_redirects=True,
            headers={
                "Content-Type": "application/x-www-form-urlencoded; charset=UTF-8",
                "X-Requested-With": "XMLHttpRequest",
            },
        )
        html = await self._text(resp)
        if resp.status >= 400:
            raise DigiError(f"Detail HTTP {resp.status} for {invoice_id}")

        unesc = unescape(html)
        title = RE_DETAILS_TITLE.search(unesc)
        pdf = RE_PDF.search(unesc)
        money = {
            self._clean_text(label).lower(): self._parse_money(value)
            for label, value in RE_LABEL_VALUE_MONEY.findall(html)
        }
        status = RE_LABEL_VALUE_TEXT.search(unesc)
        services = [
            {"name": self._clean_text(n), "amount": self._parse_money(p)}
            for n, p in RE_SERVICE_ROW.findall(unesc)
        ]
        return InvoiceDetail(
            invoice_id=invoice_id,
            invoice_number=self._clean_text(title.group(1)) if title else invoice_id,
            issue_date=self._clean_text(title.group(2)) if title else None,
            due_date=None,
            total=money.get("total"),
            rest=money.get("rest"),
            status=self._clean_text(status.group(1)) if status else None,
            pdf_url=urljoin(BASE_URL, unescape(pdf.group(1))) if pdf else None,
            services=services,
        )

    # -- 2FA / address parsing ------------------------------------------------

    @staticmethod
    def _parse_attrs(tag: str) -> dict[str, str]:
        attrs: dict[str, str] = {}
        pattern = r'(\w+(?:-\w+)*)\s*=\s*(?:"([^"]*)"|\'([^\']*)\'|([^\s>]+))'
        for key, v1, v2, v3 in re.findall(pattern, tag, re.I):
            attrs[key.lower()] = v1 or v2 or v3 or ""
        return attrs

    def _extract_hidden_inputs(self, html: str) -> dict[str, str]:
        hidden: dict[str, str] = {}
        for tag in RE_INPUT_TAG.findall(html):
            attrs = self._parse_attrs(tag)
            if attrs.get("type", "").lower() != "hidden":
                continue
            if name := attrs.get("name"):
                hidden[name] = attrs.get("value", "")
        return hidden

    def _extract_select_options(self, html: str, *names: str) -> list[TwoFactorOption]:
        candidates = {n.lower() for n in names if n}
        out: list[TwoFactorOption] = []
        seen: set[tuple[str, str]] = set()
        for select_name, body in RE_SELECT_BLOCK.findall(html):
            if candidates and select_name.lower() not in candidates:
                continue
            for value, label in RE_OPTION_TAG.findall(body):
                v, l = (value or "").strip(), self._clean_text(label)
                if v and l and (v, l) not in seen:
                    seen.add((v, l))
                    out.append(TwoFactorOption(value=v, label=l))
        return out

    def _extract_radio_options(self, html: str) -> list[AddressOption]:
        labels = {k: self._clean_text(v) for k, v in RE_LABEL_FOR.findall(html)}
        out: list[AddressOption] = []
        for tag in RE_INPUT_TAG.findall(html):
            attrs = self._parse_attrs(tag)
            if attrs.get("type", "").lower() != "radio":
                continue
            value = attrs.get("value", "")
            label = labels.get(attrs.get("id", ""), "")
            if value and label:
                out.append(AddressOption(value=value, label=label))
        return out

    def _parse_2fa_context(self, html: str) -> dict[str, dict[str, Any]]:
        methods: dict[str, dict[str, Any]] = {}
        hidden = self._extract_hidden_inputs(html)
        html_lower = html.lower()

        phone_value: str | None = None
        for key in ("form-phone-number-confirm", "phone", "phone-number-confirm", "form_phone_number_confirm"):
            value = hidden.get(key)
            if value and RE_HEX32.fullmatch(value):
                phone_value = value
                break
        if not phone_value:
            for key, value in hidden.items():
                if ("phone" in key.lower() or "telefon" in key.lower()) and value and RE_HEX32.fullmatch(value):
                    phone_value = value
                    break
        if not phone_value and (match := RE_PHONE_PARAM.search(html)):
            phone_value = match.group(1)

        sms_candidates = self._extract_select_options(
            html, "form-my-account-2fa-send-phone", "phone", "phone-number-confirm", "form-phone-number-confirm"
        )
        sms_text = any(
            t in html_lower
            for t in ("trimite sms", "codul primit prin sms", "cod de siguranță prin sms", "cod de siguranta prin sms")
        )
        if not phone_value and sms_text:
            tokens = list(dict.fromkeys(RE_HEX32.findall(html)))
            if len(tokens) == 1:
                phone_value = tokens[0]

        if phone_value or sms_candidates:
            sms: dict[str, Any] = {
                "send_url": TWO_FA_SEND_URL,
                "send_payload": {"action": "myAccount2FASend"},
                "validate_payload": {"action": "myAccount2FAVerify"},
            }
            if phone_value:
                sms["default_target"] = phone_value
            if sms_candidates:
                sms["target_options"] = [{"value": o.value, "label": o.label} for o in sms_candidates]
            methods["sms"] = sms

        email_hidden = {k: v for k, v in hidden.items() if ("mail" in k.lower() or "email" in k.lower()) and v}
        if email_hidden:
            key, value = next(iter(email_hidden.items()))
            methods["email"] = {
                "send_url": TWO_FA_SEND_URL,
                "send_payload": {"action": "myAccount2FASend", key: value},
                "validate_payload": {"action": "myAccount2FAVerify", key: value},
            }
        return methods

    # -- value helpers --------------------------------------------------------

    @staticmethod
    def _parse_money(text: str | None) -> float | None:
        if text is None:
            return None
        clean = re.sub(r"[^0-9,.\-]", "", unescape(text).strip())
        if not clean:
            return None
        if "," in clean and "." in clean:
            if clean.rfind(",") > clean.rfind("."):
                clean = clean.replace(".", "").replace(",", ".")
            else:
                clean = clean.replace(",", "")
        elif "," in clean:
            clean = clean.replace(",", ".")
        elif "." not in clean:
            try:
                return int(clean) / 100  # bare integer = bani
            except ValueError:
                return None
        try:
            return float(clean)
        except ValueError:
            return None

    @staticmethod
    def _clean_text(text: str) -> str:
        return re.sub(r"\s+", " ", unescape(text)).strip()

    @staticmethod
    def _parse_date_for_sort(value: str | None) -> datetime:
        if not value:
            return datetime.min
        parts = value.strip().replace(".", "-").replace("/", "-").split("-")
        if len(parts) != 3:
            return datetime.min
        try:
            day, month, year = (int(p) for p in parts)
            return datetime(year, month, day)
        except ValueError:
            return datetime.min

    @staticmethod
    def _extract_section(html: str, start_marker: str, end_marker: str | None) -> str:
        start = html.find(start_marker)
        if start == -1:
            return ""
        sliced = html[start:]
        if end_marker and (end := sliced.find(end_marker)) != -1:
            return sliced[:end]
        return sliced
