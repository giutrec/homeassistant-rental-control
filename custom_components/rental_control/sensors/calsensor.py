"""Creating sensors for upcoming events."""
from __future__ import annotations

import logging
import random
import re
from datetime import datetime

from homeassistant.helpers.entity import Entity
from homeassistant.helpers.entity import EntityCategory

from ..const import ICON
from ..util import gen_uuid
from ..util import get_slot_name

_LOGGER = logging.getLogger(__name__)


class RentalControlCalSensor(Entity):
    """
    Implementation of a iCal sensor.

    Represents the Nth upcoming event.
    May have a name like 'sensor.mycalander_event_0' for the first
    upcoming event.
    """

    def __init__(self, hass, rental_control_events, sensor_name, event_number):
        """
        Initialize the sensor.

        sensor_name is typically the name of the calendar.
        eventnumber indicates which upcoming event this is, starting at zero
        """
        self.rental_control_events = rental_control_events
        self.rental_control_events.event_sensors.append(self)
        if rental_control_events.event_prefix:
            summary = f"{rental_control_events.event_prefix} No reservation"
        else:
            summary = "No reservation"
        self._code_generator = rental_control_events.code_generator
        self._code_length = rental_control_events.code_length
        self._entity_category = EntityCategory.DIAGNOSTIC
        self._event_attributes = {
            "summary": summary,
            "description": None,
            "location": None,
            "start": None,
            "end": None,
            "eta_days": None,
            "eta_hours": None,
            "eta_minutes": None,
            "slot_name": None,
            "slot_code": None,
        }
        self._parsed_attributes = {}
        self._event_number = event_number
        self._hass = hass
        self._is_available = False
        self._name = f"{sensor_name} Event {self._event_number}"
        self._state = summary
        self._unique_id = gen_uuid(
            f"{self.rental_control_events.unique_id} sensor {self._event_number}"
        )

    def _extract_email(self) -> str | None:
        """Extract guest email from a description"""
        if self._event_attributes["description"] is None:
            return None
        p = re.compile(r"""Email:\s+(\S+@\S+)""")
        ret = p.findall(self._event_attributes["description"])
        if ret:
            return str(ret[0])
        else:
            return None

    def _extract_last_four(self) -> str | None:
        """Extract the last 4 digits from a description."""
        if self._event_attributes["description"] is None:
            return None
        p = re.compile(r"""\(Last 4 Digits\):\s+(\d{4})""")
        ret = p.findall(self._event_attributes["description"])
        if ret:
            return str(ret[0])
        else:
            return None

    def _extract_num_guests(self) -> str | None:
        """Extract the number of guests from a description."""
        if self._event_attributes["description"] is None:
            return None
        p = re.compile(r"""Guests:\s+(\d+)$""", re.M)
        ret = p.findall(self._event_attributes["description"])
        if ret:
            return str(ret[0])
        else:
            return None

    def _extract_phone_number(self) -> str | None:
        """Extract guest phone number from a description"""
        if self._event_attributes["description"] is None:
            return None
        p = re.compile(r"""Phone Number:\s+(\+?[\d\. \-\(\)]{9,})""")
        ret = p.findall(self._event_attributes["description"])
        if ret:
            return str(ret[0]).strip()
        else:
            return None

    def _extract_url(self) -> str | None:
        """Extract reservation URL."""
        if self._event_attributes["description"] is None:
            return None
        p = re.compile(r"""(https?://.*$)""", re.M)
        ret = p.findall(self._event_attributes["description"])
        if ret:
            return str(ret[0])
        else:
            return None

    def _generate_door_code(self) -> str:
        """Generate a door code based upon the selected type."""

        generator = self._code_generator
        code_length = self._code_length

        # If there is no event description force date_based generation
        # This is because VRBO does not appear to provide any descriptions in
        # their calendar entries!
        # This also gets around Unavailable and Blocked entries that do not
        # have a description either
        if self._event_attributes["description"] is None:
            generator = "date_based"

        # AirBnB provides the last 4 digits of the guest's registered phone
        #
        # VRBO does not appear to provide any phone numbers
        #
        # Guesty provides last 4 + either a full number or all but last digit
        # for VRBO listings and doesn't appear to provide anything for AirBnB
        # listings, or if it does provide them, my example Guesty calendar doesn't
        # have any new enough to have the data
        #
        # TripAdvisor does not appear to provide any phone number data

        ret = None

        # Last 4 is only valid for code lengths of 4
        if generator == "last_four" and code_length == 4:
            ret = self._extract_last_four()
        elif generator == "static_random":
            # If the description changes this will most likely change the code
            random.seed(self._event_attributes["description"])
            max_range = int("9999".rjust(code_length, "9"))
            ret = str(random.randrange(1, max_range, code_length)).zfill(code_length)

        if ret is None:
            # Generate code based on checkin/out days
            #
            # This generator will have a side effect of changing the code
            # if the start or end dates shift!
            #
            # This is the default and fall back generator if no other
            # generator produced a code
            start_day = self._event_attributes["start"].strftime("%d")
            start_month = self._event_attributes["start"].strftime("%m")
            start_year = self._event_attributes["start"].strftime("%Y")
            end_day = self._event_attributes["end"].strftime("%d")
            end_month = self._event_attributes["end"].strftime("%m")
            end_year = self._event_attributes["end"].strftime("%Y")
            # This should be longer than anybody ever needs
            code = f"{start_day}{end_day}{start_month}{end_month}{start_year}{end_year}"
            # use a zfill in case the code really wasn't long enough for some
            # weird reason
            ret = (
                code[:code_length]
                if len(code) > code_length
                else code.zfill(code_length)
            )

        return ret

    @property
    def available(self):
        """Return True if calendar is ready."""
        return self._is_available

    @property
    def device_info(self):
        """Return the device info block."""
        return self.rental_control_events.device_info

    @property
    def entity_category(self):
        """Return the entity category."""
        return self._entity_category

    @property
    def extra_state_attributes(self) -> dict:
        """Return the attributes of the event."""
        attrib = {**self._event_attributes, **self._parsed_attributes}
        return attrib

    @property
    def icon(self):
        """Return the icon for the frontend."""
        return ICON

    @property
    def name(self):
        """Return the name of the sensor."""
        return self._name

    @property
    def state(self):
        """Return the date of the next event."""
        return self._state

    @property
    def unique_id(self):
        """Return the unique_id."""
        return self._unique_id

    async def async_update(self):
        """Update the sensor."""
        _LOGGER.debug("Running RentalControlCalSensor async update for %s", self.name)

        await self.rental_control_events.update()

        # Calendar is not ready, no reason to continue processing
        if not self.rental_control_events.calendar_ready:
            return

        self._code_generator = self.rental_control_events.code_generator
        self._code_length = self.rental_control_events.code_length
        event_list = self.rental_control_events.calendar
        if event_list and (self._event_number < len(event_list)):
            event = event_list[self._event_number]
            name = event.summary
            start = event.start

            _LOGGER.debug(
                "Adding event %s - Start %s - End %s - as event %s to calendar %s",
                event.summary,
                event.start,
                event.end,
                str(self._event_number),
                self.name,
            )

            self._event_attributes["summary"] = event.summary
            self._event_attributes["start"] = event.start
            self._event_attributes["end"] = event.end
            self._event_attributes["location"] = event.location
            self._event_attributes["description"] = event.description
            # get timedelta for eta
            td = start - datetime.now(start.tzinfo)
            eta_days = None
            eta_hours = None
            eta_minutes = None
            if td.total_seconds() >= 0:
                eta_days = td.days
                eta_hours = round(td.total_seconds() // 3600)
                eta_minutes = round(td.total_seconds() // 60)

            self._event_attributes["eta_days"] = eta_days
            self._event_attributes["eta_hours"] = eta_hours
            self._event_attributes["eta_minutes"] = eta_minutes
            self._state = f"{name} - {start.strftime('%-d %B %Y')}"
            self._state += f" {start.strftime('%H:%M')}"
            slot_name = get_slot_name(
                self._event_attributes["summary"],
                self._event_attributes["description"],
                self.rental_control_events.event_prefix,
            )
            self._event_attributes["slot_name"] = slot_name

            override = None
            if slot_name and slot_name in self.rental_control_events.event_overrides:
                override = self.rental_control_events.event_overrides[slot_name]
                _LOGGER.debug("override: '%s'", override)
                # If start and stop are the same, then we ignore the override
                # This shouldn't happen except when a slot has been cleared
                # In that instance we shouldn't find an override
                if override["start_time"] == override["end_time"]:
                    _LOGGER.debug("override is now none")
                    override = None

            if override and override["slot_code"]:
                slot_code = str(override["slot_code"])
            else:
                slot_code = self._generate_door_code()
            self._event_attributes["slot_code"] = slot_code

            # attributes parsed from description
            parsed_attributes = {}

            last_four = self._extract_last_four()
            if last_four is not None:
                parsed_attributes["last_four"] = last_four

            num_guests = self._extract_num_guests()
            if num_guests is not None:
                parsed_attributes["number_of_guests"] = num_guests

            guest_email = self._extract_email()
            if guest_email is not None:
                parsed_attributes["guest_email"] = guest_email

            phone_number = self._extract_phone_number()
            if phone_number is not None:
                parsed_attributes["phone_number"] = phone_number

            reservation_url = self._extract_url()
            if reservation_url is not None:
                parsed_attributes["reservation_url"] = reservation_url

            self._parsed_attributes = parsed_attributes
        else:
            # No reservations
            _LOGGER.debug(
                "No events available for sensor %s, removing from calendar %s",
                str(self._event_number),
                self.name,
            )
            if self.rental_control_events.event_prefix:
                summary = f"{self.rental_control_events.event_prefix} No reservation"
            else:
                summary = "No reservation"
            self._event_attributes = {
                "summary": summary,
                "description": None,
                "location": None,
                "start": None,
                "end": None,
                "eta_days": None,
                "eta_hours": None,
                "eta_minutes": None,
                "slot_name": None,
                "slot_code": None,
            }
            self._parsed_attributes = {}
            self._state = summary

        self._is_available = self.rental_control_events.calendar_ready
