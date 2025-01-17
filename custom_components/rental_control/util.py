# SPDX-License-Identifier: Apache-2.0
##############################################################################
# COPYRIGHT 2022 Andrew Grimberg
#
# All rights reserved. This program and the accompanying materials
# are made available under the terms of the Apache 2.0 License
# which accompanies this distribution, and is available at
# https://www.apache.org/licenses/LICENSE-2.0
#
# Contributors:
#   Andrew Grimberg - Initial implementation
##############################################################################
"""Rental Control utils."""
from __future__ import annotations

import hashlib
import logging
import os
import re
import uuid
from typing import Any
from typing import List

from homeassistant.components.automation import DOMAIN as AUTO_DOMAIN
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import CONF_NAME
from homeassistant.const import SERVICE_RELOAD
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import ServiceNotFound
from homeassistant.util import dt
from homeassistant.util import slugify
from jinja2 import Environment
from jinja2 import PackageLoader
from jinja2 import select_autoescape

from .const import ATTR_CODE_SLOT
from .const import ATTR_NAME
from .const import ATTR_SLOT_NAME
from .const import CONF_PATH
from .const import EVENT_RENTAL_CONTROL_CLEAR_CODE
from .const import EVENT_RENTAL_CONTROL_SET_CODE
from .const import NAME

_LOGGER = logging.getLogger(__name__)


def delete_rc_and_base_folder(hass: HomeAssistant, config_entry: ConfigEntry) -> None:
    """Delete packages folder for RC and base rental_control folder if empty."""
    base_path = os.path.join(hass.config.path(), config_entry.get(CONF_PATH))
    rc_name_slug = slugify(config_entry.get(CONF_NAME))

    delete_folder(base_path, rc_name_slug)
    # It is possible that the path may not exist because of RCs not
    # being connected to Keymaster configurations
    if os.path.exists(base_path):
        if not os.listdir(base_path):
            os.rmdir(base_path)


def delete_folder(absolute_path: str, *relative_paths: str) -> None:
    """Recursively delete folder and all children files and folders (depth first)."""
    path = os.path.join(absolute_path, *relative_paths)

    # RC that doesn't manage a lock has no files to purge
    if not os.path.exists(path):
        return

    if os.path.isfile(path):
        os.remove(path)
    else:
        for file_or_dir in os.listdir(path):
            delete_folder(path, file_or_dir)
        os.rmdir(path)


async def async_check_overrides(rc):
    """Check if overrides need to have a clear_code event fired."""

    _LOGGER.debug("In async_check_overrides")

    event_list = rc.calendar
    overrides = rc.event_overrides.copy()

    event_names = get_event_names(rc)
    _LOGGER.debug("event_names = '%s'", event_names)
    _LOGGER.debug(overrides)

    for override in overrides:
        clear_code = False

        if "Slot " not in override and override not in event_names:
            _LOGGER.debug("%s is not in events, setting clear flag", override)
            clear_code = True

        ovr = overrides[override]
        _LOGGER.debug("Checking ovr = '%s'", ovr)

        if ("slot_name" in ovr or "slot_code" in ovr) and (
            (ovr["end_time"].date() < dt.start_of_local_day().date())
            or (
                (event_list and rc.max_events <= len(event_list))
                and (
                    ovr["start_time"].date() > event_list[rc.max_events - 1].end.date()
                )
            )
        ):
            _LOGGER.debug("%s is outside time options, setting clear flag", override)
            clear_code = True

        if clear_code:
            fire_clear_code(rc.hass, overrides[override]["slot"], rc.name)


def fire_clear_code(hass: HomeAssistant, slot: int, name: str) -> None:
    """Fire clear_code event."""
    _LOGGER.debug("In fire_clear_code - slot: %d, name: %s", slot, name)
    hass.bus.fire(
        EVENT_RENTAL_CONTROL_CLEAR_CODE,
        event_data={
            ATTR_CODE_SLOT: slot,
            ATTR_NAME: name,
        },
    )


def fire_set_code(hass: HomeAssistant, name: str, slot: int, slot_name: str) -> None:
    """Fire set_code event."""
    _LOGGER.debug(
        "In fire_set_code - name: %s, slot: %d, slot_name: %s", name, slot, slot_name
    )
    hass.bus.fire(
        EVENT_RENTAL_CONTROL_SET_CODE,
        event_data={
            ATTR_CODE_SLOT: slot,
            ATTR_NAME: name,
            ATTR_SLOT_NAME: slot_name,
        },
    )


def get_event_names(rc) -> List[str]:
    """Get the current event names."""
    event_names = [
        e.extra_state_attributes["slot_name"]
        for e in rc.event_sensors
        if e.extra_state_attributes["slot_name"]
    ]
    return event_names


def gen_uuid(created: str) -> str:
    """Generation a UUID from the NAME and creation time."""
    m = hashlib.md5(f"{NAME} {created}".encode("utf-8"))
    return str(uuid.UUID(m.hexdigest()))


def get_slot_name(summary: str, description: str, prefix: str) -> str | None:
    """Determine the name for a given slot / event."""

    # strip off any prefix if it's being used
    if prefix:
        p = re.compile(f"{prefix} (.*)")
        name = p.findall(summary)[0]
    else:
        name = summary

    # Blocked and Unavailable should not have anything
    p = re.compile("Not available|Blocked")
    if p.search(name):
        return None

    # Airbnb and VRBO
    if "Reserved" in name:
        # Airbnb
        if name == "Reserved":
            p = re.compile(r"([A-Z][A-Z0-9]{9})")
            if description:
                ret = p.search(description)  # type: Any
                if ret is not None:
                    return str(ret[0])
                else:
                    return None
            else:
                return None
        else:
            p = re.compile(r" - (.*)$")
            ret = p.findall(name)
            if len(ret):
                return str(ret[0])

    # Tripadvisor
    if "Tripadvisor" in name:
        p = re.compile(r"Tripadvisor.*: (.*)")
        ret = p.findall(name)
        if len(ret):
            return str(ret[0])

    # Booking.com
    if "CLOSED" in name:
        p = re.compile(r"\s*CLOSED - (.*)")
        ret = p.findall(name)
        if len(ret):
            return str(ret[0])

    # Guesty
    p = re.compile(r"-(.*)-.*-")
    ret = p.findall(name)
    if len(ret):
        return str(ret[0])

    # Degenerative case, we can't figure it out at all, we'll just use the
    # name as is, this could cause duplicate slot names but this is likely
    # a custom calendar anyway
    return str(name)


def write_template_config(
    output_path: str, template_name: str, NAME: str, rc_name: str, config_entry
) -> None:
    """Render the given template to disk."""
    _LOGGER.debug("In write_template_config")

    jinja_env = Environment(
        loader=PackageLoader("custom_components.rental_control"),
        autoescape=select_autoescape(),
    )

    template = jinja_env.get_template(template_name + ".yaml.j2")
    render = template.render(
        NAME=NAME,
        rc_name=rc_name,
        config_entry=config_entry,
        rc_slug=slugify(rc_name),
    )

    _LOGGER.debug(
        f"""Rendered Template is:
    {render}"""
    )

    filename = slugify(f"{rc_name}_{template_name}") + ".yaml"

    with open(os.path.join(output_path, filename), "w+") as outfile:
        _LOGGER.debug("Writing %s", filename)
        outfile.write(render)

    _LOGGER.debug("Completed writing %s", filename)


async def async_reload_package_platforms(hass: HomeAssistant) -> bool:
    """Reload package platforms to pick up any changes to package files."""
    _LOGGER.debug("In async_reload_package_platforms")
    for domain in [
        AUTO_DOMAIN,
    ]:
        try:
            await hass.services.async_call(domain, SERVICE_RELOAD, blocking=True)
        except ServiceNotFound:
            return False
    return True
