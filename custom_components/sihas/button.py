"""Platform for light integration."""
from __future__ import annotations

import logging
from datetime import timedelta
from typing import List

from homeassistant.components.button import ButtonEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from typing_extensions import Final

from .climate import Acm300
from .climate import BCM_REG_ONSUSETPT
from .const import (
    CONF_CFG,
    CONF_IP,
    CONF_MAC,
    CONF_NAME,
    CONF_TYPE,
    DEFAULT_PARALLEL_UPDATES,
    ICON_BUTTON,
    SIHAS_PLATFORM_SCHEMA,
)
from .packet_builder import packet_builder as pb
from .sender import send

SCAN_INTERVAL: Final = timedelta(seconds=5)

_LOGGER = logging.getLogger(__name__)

PARALLEL_UPDATES: Final = DEFAULT_PARALLEL_UPDATES
PLATFORM_SCHEMA: Final = SIHAS_PLATFORM_SCHEMA


async def async_setup_entry(
    hass: HomeAssistant, entry: ConfigEntry, async_add_entities: AddEntitiesCallback
) -> None:
    if entry.data[CONF_TYPE] == "ACM":
        acm = Acm300(
            entry.data[CONF_IP],
            entry.data[CONF_MAC],
            entry.data[CONF_TYPE],
            entry.data[CONF_CFG],
            entry.data[CONF_NAME],
        )

        async_add_entities(await get_ucr(acm))
    elif entry.data[CONF_TYPE] == "BCM":
        async_add_entities(
            [
                BcmOnsuButton(
                    ip=entry.data[CONF_IP],
                    mac=entry.data[CONF_MAC],
                    device_type=entry.data[CONF_TYPE],
                    config=entry.data[CONF_CFG],
                    name=entry.data[CONF_NAME],
                    level=0, # 저온
                ),
                BcmOnsuButton(
                    ip=entry.data[CONF_IP],
                    mac=entry.data[CONF_MAC],
                    device_type=entry.data[CONF_TYPE],
                    config=entry.data[CONF_CFG],
                    name=entry.data[CONF_NAME],
                    level=1, # 중온
                ),
                BcmOnsuButton(
                    ip=entry.data[CONF_IP],
                    mac=entry.data[CONF_MAC],
                    device_type=entry.data[CONF_TYPE],
                    config=entry.data[CONF_CFG],
                    name=entry.data[CONF_NAME],
                    level=2, # 고온
                ),
            ]
        )

    return


async def get_ucr(acm) -> List[AcmUCR]:

    try:
        req = pb.poll()
        resp = send(
            data=req,
            ip=acm.ip,
            retry=3,
        )

        acm.registers = pb.extract_registers(resp)
        ucr_reg = acm.registers[Acm300.REG_LIST_UCR1] + (acm.registers[Acm300.REG_LIST_UCR2] << 16)
        urcs = []
        for i in range(0, 20):
            if ucr_reg & (1 << i) != 0:
                urcs.append(AcmUCR(acm, i))
        return urcs
    except Exception as e:
        _LOGGER.error(f"failed to get UCR: {str(e)}")
        return []


class AcmUCR(ButtonEntity):
    _attr_icon = ICON_BUTTON

    def __init__(self, acm: Acm300, number_of_button: int):
        self.acm = acm
        self.number_of_button = number_of_button
        self._attr_name = f"리모컨 #{number_of_button + 1}"
        self._attr_unique_id = f"{acm.device_type}-{acm.mac}-{number_of_button}"

    def press(self) -> None:
        self.acm.command(Acm300.REG_EXEC_UCR, self.number_of_button)

from .sihas_base import SihasEntity

class BcmOnsuButton(ButtonEntity, SihasEntity):
    _attr_icon = ICON_BUTTON
    _LEVEL_NAME = {0: "저온", 1: "중온", 2: "고온"}

    def __init__(
        self,
        ip: str,
        mac: str,
        device_type: str,
        config: int,
        name: str | None,
        level: int,
    ) -> None:
        SihasEntity.__init__(
            self,
            ip=ip,
            mac=mac,
            device_type=device_type,
            config=config,
            uid=f"{device_type}-{mac}-onsu-{level}",
            name=f"{name} {self._LEVEL_NAME[level]}" if name else None,
        )
        self._level = level
        self._attr_available = True
    
    def update(self) -> None:
        self.poll()
    
    def press(self) -> None:
        self.command(BCM_REG_ONSUSETPT, self._level)
