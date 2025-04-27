"""Platform for SiHAS device integration."""
from __future__ import annotations

import logging
import math
import time
from abc import abstractmethod
from dataclasses import dataclass
from datetime import timedelta
from enum import Enum, IntEnum
from typing import Dict, List, Optional, cast

from homeassistant.components.climate import ClimateEntity
from homeassistant.components.climate.const import (
    HVACAction,
    HVACMode,
    FAN_AUTO,
    FAN_HIGH,
    FAN_LOW,
    FAN_MEDIUM,
    ClimateEntityFeature,
    SWING_BOTH,
    SWING_HORIZONTAL,
    SWING_OFF,
    SWING_VERTICAL,
)
from homeassistant.components.select import SelectEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import ATTR_TEMPERATURE, UnitOfTemperature
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity import Entity
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from typing_extensions import Final
import voluptuous as vol
import homeassistant.helpers.config_validation as cv

from .const import (
    CONF_CFG,
    CONF_IP,
    CONF_MAC,
    CONF_NAME,
    CONF_TYPE,
    DEFAULT_PARALLEL_UPDATES,
    ICON_COOLER,
    ICON_HEATER,
    SIHAS_PLATFORM_SCHEMA,
)
from .errors import ModbusNotEnabledError, PacketSizeError
from .packet_builder import packet_builder as pb
from .sender import send
from .sihas_base import SihasEntity, SihasProxy, SihasSubEntity

SCAN_INTERVAL: Final = timedelta(seconds=5)
_LOGGER = logging.getLogger(__name__)

BCM_REG_ONOFF: Final = 0
BCM_REG_ROOMSETPT: Final = 1
BCM_REG_ONDOLSETPT: Final = 2
BCM_REG_ONSUSETPT: Final = 3
BCM_REG_OPERMODE: Final = 4
BCM_REG_OUTMODE: Final = 5
BCM_REG_TIMERMODE: Final = 6
BCM_REG_ROOMTEMP: Final = 8
BCM_REG_ONDOLTEMP: Final = 9
BCM_REG_ONSUTEMP: Final = 10
BCM_REG_FIRE_STATE: Final = 11

class BcmHeatMode(Enum):
    Room: Final = 0
    Ondol: Final = 1

@dataclass
class BcmOpMode:
    isOnsuOn: bool
    isHeatOn: bool
    heatMode: BcmHeatMode

class Bcm300(SihasEntity, ClimateEntity):
    _attr_icon = ICON_HEATER
    _attr_hvac_modes: Final = [HVACMode.OFF, HVACMode.HEAT, HVACMode.FAN_ONLY, HVACMode.AUTO]
    _attr_max_temp: Final = 80
    _attr_min_temp: Final = 0
    _attr_supported_features: Final = ClimateEntityFeature.TARGET_TEMPERATURE
    _attr_target_temperature_step: Final = 1
    _attr_temperature_unit: Final = UnitOfTemperature.CELSIUS

    def __init__(self, ip: str, mac: str, device_type: str, config: int, name: str | None = None) -> None:
        super().__init__(ip=ip, mac=mac, device_type=device_type, config=config, name=name)
        self.opmode: Optional[BcmOpMode] = None

    async def async_added_to_hass(self) -> None:
        await super().async_added_to_hass()

        async def handle_set_hot_water_mode(call):
            mode = call.data.get("mode")
            entity_ids = call.data.get("entity_id")

            if mode is None or entity_ids is None:
                _LOGGER.error("entity_id and mode are required parameters for set_hot_water_mode")
                return

            entity_ids = entity_ids if isinstance(entity_ids, list) else [entity_ids]

            component = self.hass.data.get("climate")
            if not component:
                _LOGGER.error("climate domain not found")
                return

            entities = component.entities

            for entity_id in entity_ids:
                entity = entities.get(entity_id)
                if entity and hasattr(entity, "set_hot_water_mode"):
                    entity.set_hot_water_mode(mode)
                else:
                    _LOGGER.warning(f"Entity {entity_id} not found or doesn't support set_hot_water_mode.")

        self.hass.services.async_register(
            domain="sihas",
            service="set_hot_water_mode",
            service_func=handle_set_hot_water_mode,
            schema=vol.Schema({
                vol.Required("entity_id"): cv.entity_ids,
                vol.Required("mode"): vol.All(vol.Coerce(int), vol.In([0, 1, 2])),
            }),
        )

    def set_hvac_mode(self, hvac_mode: str):
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

    def set_temperature(self, **kwargs):
        tmp = cast(float, kwargs.get(ATTR_TEMPERATURE))
        assert self.opmode is not None
        self.command(
            BCM_REG_ROOMSETPT if (self.opmode.heatMode == BcmHeatMode.Room) else BCM_REG_ONDOLSETPT,
            math.floor(tmp),
        )

    def set_hot_water_mode(self, mode: int):
        if mode not in (0, 1, 2):
            raise ValueError(f"Invalid hot water mode: {mode}")
        self.command(BCM_REG_ONSUSETPT, mode)

    def update(self):
        if regs := self.poll():
            self.opmode = self._parse_oper_mode(regs)
            self._attr_hvac_mode = self._resolve_hvac_mode(regs)
            self._attr_hvac_action = self._resolve_hvac_action(regs)

            if self.opmode.heatMode == BcmHeatMode.Room:
                self._attr_target_temperature = regs[BCM_REG_ROOMSETPT]
                self._attr_current_temperature = math.floor(regs[BCM_REG_ROOMTEMP] / 10)
            else:
                self._attr_target_temperature = regs[BCM_REG_ONDOLSETPT]
                self._attr_current_temperature = regs[BCM_REG_ONDOLTEMP]

    def _resolve_hvac_mode(self, regs):
        if regs[BCM_REG_ONOFF] == 0:
            return HVACMode.OFF
        elif regs[BCM_REG_TIMERMODE] == 1:
            return HVACMode.HEAT
        elif regs[BCM_REG_OUTMODE] == 1:
            return HVACMode.FAN_ONLY
        return HVACMode.AUTO

    def _resolve_hvac_action(self, regs):
        if regs[BCM_REG_ONOFF] == 0:
            return HVACAction.OFF
        elif regs[BCM_REG_FIRE_STATE] == 0:
            return HVACAction.IDLE
        return HVACAction.HEATING

    def _parse_oper_mode(self, regs: List[int]) -> BcmOpMode:
        reg = regs[BCM_REG_OPERMODE]
        return BcmOpMode(
            (reg & 1) != 0,
            (reg & (1 << 1)) != 0,
            BcmHeatMode.Ondol if (reg & (1 << 2)) != 0 else BcmHeatMode.Room,
        )
