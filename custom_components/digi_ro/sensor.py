"""Sensor platform for Digi Romania (one device per address)."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from datetime import date
from typing import Any

from homeassistant.components.sensor import (
    SensorDeviceClass,
    SensorEntity,
    SensorEntityDescription,
)
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import CURRENCY_RON, DOMAIN
from .coordinator import DigiDataCoordinator
from .models import AddressInvoices


def _to_date(value: str | None) -> date | None:
    if not value:
        return None
    parts = value.strip().replace(".", "-").replace("/", "-").split("-")
    if len(parts) != 3:
        return None
    try:
        day, month, year = (int(p) for p in parts)
        return date(year, month, day)
    except ValueError:
        return None


@dataclass(frozen=True, kw_only=True)
class DigiSensorDescription(SensorEntityDescription):
    """Describes a Digi sensor derived from one address's invoices."""

    value_fn: Callable[[AddressInvoices], Any]
    attrs_fn: Callable[[AddressInvoices], dict[str, Any]] | None = None


SENSORS: tuple[DigiSensorDescription, ...] = (
    DigiSensorDescription(
        key="last_invoice_amount",
        translation_key="last_invoice_amount",
        icon="mdi:receipt-text",
        native_unit_of_measurement=CURRENCY_RON,
        device_class=SensorDeviceClass.MONETARY,
        value_fn=lambda a: a.latest.get("amount"),
        attrs_fn=lambda a: {
            "invoice_number": a.latest.get("invoice_number"),
            "description": a.latest.get("description"),
            "status": a.latest.get("status"),
            "pdf_url": a.latest.get("pdf_url"),
            "services": a.latest.get("services"),
        },
    ),
    DigiSensorDescription(
        key="last_invoice_date",
        translation_key="last_invoice_date",
        icon="mdi:calendar-arrow-right",
        device_class=SensorDeviceClass.DATE,
        value_fn=lambda a: _to_date(a.latest.get("issue_date")),
    ),
    DigiSensorDescription(
        key="last_invoice_due_date",
        translation_key="last_invoice_due_date",
        icon="mdi:calendar-clock",
        device_class=SensorDeviceClass.DATE,
        value_fn=lambda a: _to_date(a.latest.get("due_date")),
    ),
    DigiSensorDescription(
        key="unpaid_total",
        translation_key="unpaid_total",
        icon="mdi:cash-multiple",
        native_unit_of_measurement=CURRENCY_RON,
        device_class=SensorDeviceClass.MONETARY,
        value_fn=lambda a: a.unpaid_total,
        attrs_fn=lambda a: {
            "invoices": [
                {k: i.get(k) for k in ("invoice_number", "issue_date", "due_date", "amount", "rest", "pdf_url")}
                for i in a.history
                if (i.get("rest") or 0) > 0 or "neach" in (i.get("status") or "").lower()
            ]
        },
    ),
    DigiSensorDescription(
        key="unpaid_count",
        translation_key="unpaid_count",
        icon="mdi:file-alert",
        native_unit_of_measurement="facturi",
        value_fn=lambda a: a.unpaid_count,
    ),
)


async def async_setup_entry(
    hass: HomeAssistant, entry: ConfigEntry, async_add_entities: AddEntitiesCallback
) -> None:
    """Set up Digi sensors. One device per address, created as addresses appear."""
    coordinator: DigiDataCoordinator = entry.runtime_data
    known: set[str] = set()

    @callback
    def _add_new() -> None:
        data = coordinator.data
        if not data:
            return
        new: list[DigiSensor] = []
        for address_key in data.invoices_by_address:
            if address_key in known:
                continue
            known.add(address_key)
            new.extend(
                DigiSensor(coordinator, entry, address_key, desc) for desc in SENSORS
            )
        if new:
            async_add_entities(new)

    _add_new()
    entry.async_on_unload(coordinator.async_add_listener(_add_new))


class DigiSensor(CoordinatorEntity[DigiDataCoordinator], SensorEntity):
    """A single Digi address sensor."""

    _attr_has_entity_name = True
    entity_description: DigiSensorDescription

    def __init__(
        self,
        coordinator: DigiDataCoordinator,
        entry: ConfigEntry,
        address_key: str,
        description: DigiSensorDescription,
    ) -> None:
        super().__init__(coordinator)
        self.entity_description = description
        self._address_key = address_key
        self._attr_unique_id = f"{entry.entry_id}_{address_key}_{description.key}"
        address = coordinator.data.invoices_by_address[address_key].address
        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, f"{entry.entry_id}_{address_key}")},
            name=f"Digi — {address}",
            manufacturer="Digi / RCS & RDS",
            configuration_url="https://www.digi.ro/my-account/invoices",
        )

    @property
    def _address(self) -> AddressInvoices | None:
        return self.coordinator.data.invoices_by_address.get(self._address_key)

    @property
    def available(self) -> bool:
        return super().available and self._address is not None

    @property
    def native_value(self) -> Any:
        address = self._address
        return self.entity_description.value_fn(address) if address else None

    @property
    def extra_state_attributes(self) -> dict[str, Any] | None:
        address = self._address
        if address is None or self.entity_description.attrs_fn is None:
            return None
        return self.entity_description.attrs_fn(address)
