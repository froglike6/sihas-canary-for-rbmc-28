"""Platform for sihas BCM climate (boiler) integration."""
from __future__ import annotations

import logging
import math
import time
import voluptuous as vol
from datetime import timedelta
from enum import IntEnum
from typing import Any, Final, Optional

from homeassistant.core import HomeAssistant, callback
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
    DEFAULT_PARALLEL_UPDATES,
    SIHAS_PLATFORM_SCHEMA,
)
from .sihas_base import SihasEntity

_LOGGER = logging.getLogger(__name__)

SCAN_INTERVAL: Final = timedelta(seconds=5)
PARALLEL_UPDATES: Final = DEFAULT_PARALLEL_UPDATES

# BCM register indices
BCM_REG_ONOFF: Final = 0         # 보일러 운전상태 ON/OFF
BCM_REG_ROOMSETPT: Final = 1     # 실내난방 설정온도(x1)
BCM_REG_ONDOLSETPT: Final = 2    # 온돌난방 설정온도(x1)
BCM_REG_ONSUSETPT: Final = 3     # 온수 전용 설정온도(x1)
BCM_REG_OPERMODE: Final = 4      # 운전모드 비트필드
BCM_REG_OUTMODE: Final = 5       # 외출모드
BCM_REG_TIMERMODE: Final = 6     # 예약모드
BCM_REG_ROOMTEMP: Final = 8      # 실내온도(x0.1)
BCM_REG_ONDOLTEMP: Final = 9     # 온돌온도(x1)
BCM_REG_FIRE_STATE: Final = 11   # 연소상태

class BcmHeatMode(IntEnum):
    Room = 0
    Ondol = 1

class BcmOpMode:
    """운전모드 비트필드 파싱 결과."""
    def __init__(self, reg: int):
        # bit0: 온수, bit1: 난방 on/off, bit2: mode(0=Room,1=Ondol)
        self.isOnsuOn = bool(reg & 0x01)
        self.isHeatOn = bool(reg & 0x02)
        self.heatMode = BcmHeatMode.Ondol if (reg & 0x04) else BcmHeatMode.Room

async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """BCM-300 보일러 엔티티만 추가."""
    if entry.data[CONF_TYPE] != "BCM":
        return
    async_add_entities([
        Bcm300(
            entry.data[CONF_IP],
            entry.data[CONF_MAC],
            entry.data[CONF_TYPE],
            entry.data[CONF_CFG],
            entry.data[CONF_NAME],
        )
    ], update_before_add=True)

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
        """난방(FAN_ONLY), 난방(HEAT), 자동(AUTO), 끄기(OFF) 처리."""
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
        """현재 운전모드(Room/Ondol)에 따라 실내난방 또는 온돌난방 온도 설정."""
        tmp = float(kwargs.get(ATTR_TEMPERATURE))
        assert self.opmode is not None, "운전모드 정보 없음"
        reg = BCM_REG_ROOMSETPT if self.opmode.heatMode == BcmHeatMode.Room else BCM_REG_ONDOLSETPT
        self.command(reg, math.floor(tmp))

    def update(self) -> None:
        """최신 레지스터를 읽어 상태 갱신."""
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
        """
        온수 온도 모드 설정 서비스.
        mode: 0=저온, 1=중온, 2=고온
        """
        assert 0 <= mode <= 2, "mode must be 0, 1 or 2"
        self.command(BCM_REG_ONSUSETPT, mode)
        self.async_write_ha_state()

    async def async_added_to_hass(self) -> None:
        """엔티티가 추가될 때 ‘set_hot_water_mode’ 서비스를 등록."""
        await super().async_added_to_hass()
        self.hass.services.async_register(
            DOMAIN,
            "set_hot_water_mode",
            self._handle_set_hot_water_mode,
            schema=vol.Schema({
                vol.Required("entity_id"): cv.entity_ids,
                vol.Required("mode"): vol.All(vol.Coerce(int), vol.Range(min=0, max=2))
            }),
        )

    @callback
    def _handle_set_hot_water_mode(self, call: Any) -> None:
        """서비스 호출 처리: 대상 entity_id에 set_hot_water_mode 실행."""
        target_ids = call.data["entity_id"]
        mode = call.data["mode"]
        if isinstance(target_ids, str):
            target_ids = [target_ids]
        for ent in self.hass.data["climate"].entities.values():
            if ent.entity_id in target_ids and isinstance(ent, Bcm300):
                ent.set_hot_water_mode(mode)