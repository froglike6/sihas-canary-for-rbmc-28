"""Platform for sihas BCM climate (boiler) integration."""
from __future__ import annotations

import logging
import math
import voluptuous as vol
from datetime import timedelta
from enum import IntEnum
from typing import Any, Final, Optional

from homeassistant.core import HomeAssistant
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import ATTR_TEMPERATURE, UnitOfTemperature
from homeassistant.helpers import config_validation as cv
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.components.climate import ClimateEntity
from homeassistant.components.climate.const import (
    HVACMode,
    HVACAction,
    ClimateEntityFeature,
)

from .const import (
    CONF_IP,
    CONF_MAC,
    CONF_TYPE,
    CONF_CFG,
    CONF_NAME,
    DOMAIN,
    ICON_HEATER,
)
from .sihas_base import SihasEntity

_LOGGER = logging.getLogger(__name__)

SCAN_INTERVAL: Final = timedelta(seconds=5)
PARALLEL_UPDATES: Final = 5  # or import DEFAULT_PARALLEL_UPDATES if preferred

# BCM register indices
BCM_REG_ONOFF: Final = 0
BCM_REG_ROOMSETPT: Final = 1
BCM_REG_ONDOLSETPT: Final = 2
BCM_REG_ONSUSETPT: Final = 3
BCM_REG_OPERMODE: Final = 4
BCM_REG_OUTMODE: Final = 5
BCM_REG_TIMERMODE: Final = 6
BCM_REG_ROOMTEMP: Final = 8
BCM_REG_ONDOLTEMP: Final = 9
BCM_REG_FIRE_STATE: Final = 11

class BcmHeatMode(IntEnum):
    Room = 0
    Ondol = 1

class BcmOpMode:
    def __init__(self, reg: int):
        self.isOnsuOn = bool(reg & 0x01)
        self.isHeatOn = bool(reg & 0x02)
        self.heatMode = BcmHeatMode.Ondol if (reg & 0x04) else BcmHeatMode.Room

async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up the BCM climate entity and register service."""
    if entry.data[CONF_TYPE] != "BCM":
        return

    # Create and add the boiler entity
    bcm_entity = Bcm300(
        entry.data[CONF_IP],
        entry.data[CONF_MAC],
        entry.data[CONF_TYPE],
        entry.data[CONF_CFG],
        entry.data[CONF_NAME],
    )
    async_add_entities([bcm_entity], update_before_add=True)

    # Register service for hot water mode
    def handle_set_hot_water_mode(call: Any) -> None:
        target_entities = call.data.get("entity_id")
        if isinstance(target_entities, str):
            target_entities = [target_entities]
        mode = call.data.get("mode")
        for ent in [bcm_entity]:
            if ent.entity_id in target_entities:
                ent.set_hot_water_mode(mode)

    hass.services.async_register(
        DOMAIN,
        "set_hot_water_mode",
        handle_set_hot_water_mode,
        schema=vol.Schema({
            vol.Required("entity_id"): cv.entity_ids,
            vol.Required("mode"): vol.All(vol.Coerce(int), vol.Range(min=0, max=2)),
        }),
    )

class Bcm300(SihasEntity, ClimateEntity):
    """SiHAS BCM-300 Boiler controller."""

    _attr_icon = ICON_HEATER
    _attr_hvac_modes = [HVACMode.OFF, HVACMode.HEAT, HVACMode.FAN_ONLY, HVACMode.AUTO]
    _attr_supported_features = ClimateEntityFeature.TARGET_TEMPERATURE
    _attr_max_temp = 80
    _attr_min_temp = 0
    _attr_target_temperature_step = 1
    _attr_temperature_unit = UnitOfTemperature.CELSIUS

    def __init__(
        self,
        ip: str,
        mac: str,
        device_type: str,
        config: int,
        name: str | None = None,
    ) -> None:
        super().__init__(ip, mac, device_type, config, name)
        self.opmode: Optional[BcmOpMode] = None

    def set_hvac_mode(self, hvac_mode: str) -> None:
        if hvac_mode == HVACMode.FAN_ONLY:
            self.command(BCM_REG_OUTMODE, 1)
            self.command(BCM_REG_ONOFF, 1)
        elif hvac_mode == HVACMode.HEAT:
            self.command(BCM_REG_OUTMODE, 0)
            self.command(BCM_REG_ONOFF, 1)
            self.command(BCM_REG_TIMERMODE, 1)
        elif hvac_mode == HVACMode.AUTO:
            self.command(BCM_REG_OUTMODE, 0)
            self.command(BCM_REG_ONOFF, 1)
            self.command(BCM_REG_TIMERMODE, 0)
        elif hvac_mode == HVACMode.OFF:
            self.command(BCM_REG_ONOFF, 0)

    def set_temperature(self, **kwargs: Any) -> None:
        tmp = float(kwargs.get(ATTR_TEMPERATURE))
        assert self.opmode is not None, "운전모드 정보 없음"
        reg = BCM_REG_ROOMSETPT if self.opmode.heatMode == BcmHeatMode.Room else BCM_REG_ONDOLSETPT
        self.command(reg, math.floor(tmp))

    def update(self) -> None:
        if regs := self.poll():
            self.opmode = BcmOpMode(regs[BCM_REG_OPERMODE])
            self._attr_hvac_mode = self._resolve_hvac_mode(regs)
            self._attr_hvac_action = self._resolve_hvac_action(regs)
            if self.opmode.heatMode == BcmHeatMode.Room:
                cur = math.floor(regs[BCM_REG_ROOMTEMP] / 10)
                tgt = regs[BCM_REG_ROOMSETPT]
            else:
                cur = regs[BCM_REG_ONDOLTEMP]
                tgt = regs[BCM_REG_ONDOLSETPT]
            self._attr_current_temperature = cur
            self._attr_target_temperature = tgt

    def _resolve_hvac_mode(self, regs: list[int]) -> str:
        if regs[BCM_REG_ONOFF] == 0:
            return HVACMode.OFF
        if regs[BCM_REG_TIMERMODE] == 1:
            return HVACMode.HEAT
        if regs[BCM_REG_OUTMODE] == 1:
            return HVACMode.FAN_ONLY
        return HVACMode.AUTO

    def _resolve_hvac_action(self, regs: list[int]) -> str:
        if regs[BCM_REG_ONOFF] == 0:
            return HVACAction.OFF
        if regs[BCM_REG_FIRE_STATE] == 0:
            return HVACAction.IDLE
        return HVACAction.HEATING

    def set_hot_water_mode(self, mode: int) -> None:
        assert 0 <= mode <= 2, "mode must be 0, 1 or 2"
        self.command(BCM_REG_ONSUSETPT, mode)
        self.async_write_ha_state()
