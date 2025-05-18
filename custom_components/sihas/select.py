from __future__ import annotations
import logging
from datetime import timedelta
from typing_extensions import Final

from homeassistant.components.select import SelectEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .const import (
    CONF_IP, CONF_MAC, CONF_CFG, CONF_NAME, CONF_TYPE,
    DEFAULT_PARALLEL_UPDATES, ICON_BUTTON, SIHAS_PLATFORM_SCHEMA
)
from .climate import BCM_REG_ONSUSETPT
from .sihas_base import SihasEntity

SCAN_INTERVAL: Final = timedelta(seconds=5)
PARALLEL_UPDATES: Final = DEFAULT_PARALLEL_UPDATES
PLATFORM_SCHEMA: Final = SIHAS_PLATFORM_SCHEMA

_LOGGER = logging.getLogger(__name__)

OPTIONS = ["저온", "중온", "고온"]           # 0·1·2 레지스터 값 매핑

VALUE_TO_OPTION = {i: opt for i, opt in enumerate(OPTIONS)}
OPTION_TO_VALUE = {v: k for k, v in VALUE_TO_OPTION.items()}


async def async_setup_entry(
    hass: HomeAssistant, entry: ConfigEntry, async_add_entities: AddEntitiesCallback
) -> None:
    if entry.data[CONF_TYPE] == "BCM":
        async_add_entities([
            BcmOnsuSelect(
                ip=entry.data[CONF_IP],
                mac=entry.data[CONF_MAC],
                device_type=entry.data[CONF_TYPE],
                config=entry.data[CONF_CFG],
                name=entry.data[CONF_NAME],
            )
        ])


class BcmOnsuSelect(SelectEntity, SihasEntity):
    """BCM-300 온수 3단계 드롭다운"""

    _attr_icon = ICON_BUTTON
    _attr_options = OPTIONS
    _attr_current_option = None
    _attr_translation_key = "sihas_bcm_onsu"   # optional: for localize

    def __init__(
        self,
        ip: str,
        mac: str,
        device_type: str,
        config: int,
        name: str | None,
    ) -> None:
        SihasEntity.__init__(
            self,
            ip=ip,
            mac=mac,
            device_type=device_type,
            config=config,
            uid=f"{device_type}-{mac}-onsu-select",
            name=f"{name} 온수" if name else None,
        )
        self._attr_available = True

    # ---------- 상태 갱신 ----------
    def update(self):
        if regs := self.poll():
            val = regs[BCM_REG_ONSUSETPT]          # 0·1·2
            self._attr_current_option = VALUE_TO_OPTION.get(val, None)

    # ---------- 사용자 선택 ----------
    def select_option(self, option: str) -> None:
        val = OPTION_TO_VALUE[option]
        self.command(BCM_REG_ONSUSETPT, val)
        # 성공했다면 곧바로 UI 반영
        self._attr_current_option = option