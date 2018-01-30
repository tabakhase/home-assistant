"""The Config Manager is responsible for managing configuration for components.

The Config Manager allows for creating config entries to be consumed by
components. Each entry is created via a Config Flow Handler, as defined by each
component.

During startup, Home Assistant will setup each component for which config
entries are available. It will first call the normal setup and then call
the method `async_setup_entry(hass, entry)` for each entry. The same method is
called when Home Assistant is running and a config entry is created.

When a config flow is started for a domain, Home Assistant will make sure to
load all dependencies and install the requirements.
"""
import asyncio
import logging
import os
import uuid

from .core import callback
from .exceptions import HomeAssistantError
from .setup import async_setup_component, async_process_deps_reqs
from .util.json import load_json, save_json
from .util.decorator import Registry


_LOGGER = logging.getLogger(__name__)
HANDLERS = Registry()
# Components that have config flows. In future we will auto-generate this list.
FLOWS = [
    'config_entry_example'
]

SOURCE_USER = 'user'
SOURCE_DISCOVERY = 'discovery'

PATH_CONFIG = '.config_entries.json'

SAVE_DELAY = 1

RESULT_TYPE_FORM = 'form'
RESULT_TYPE_CREATE_ENTRY = 'create_entry'
RESULT_TYPE_ABORT = 'abort'

ENTRY_STATE_LOADED = 'loaded'
ENTRY_STATE_SETUP_ERROR = 'setup_error'
ENTRY_STATE_NOT_LOADED = 'not_loaded'


class ConfigEntry:
    """Hold a configuration entry."""

    __slots__ = ('entry_id', 'version', 'domain', 'title', 'data', 'source',
                 'state')

    def __init__(self, version, domain, title, data, source, entry_id=None,
                 state=ENTRY_STATE_NOT_LOADED):
        """Initialize a config entry."""
        # Unique id of the config entry
        self.entry_id = entry_id or uuid.uuid4().hex

        # Version of the configuration.
        self.version = version

        # Domain the configuration belongs to
        self.domain = domain

        # Title of the configuration
        self.title = title

        # Config data
        self.data = data

        # Source of the configuration (user, discovery, cloud)
        self.source = source

        # State of the entry (LOADED, NOT_LOADED)
        self.state = state

    @asyncio.coroutine
    def async_setup(self, hass, *, component=None):
        """Set up an entry."""
        if component is None:
            component = getattr(hass.components, self.domain)

        try:
            result = yield from component.async_setup_entry(hass, self)

            if not isinstance(result, bool):
                _LOGGER.error('%s.async_config_entry did not return boolean',
                              self.domain)
        except Exception:  # pylint: disable=broad-except
            _LOGGER.exception('Error setting up entry %s for %s',
                              self.title, self.domain)
            result = False

        if result:
            self.state = ENTRY_STATE_LOADED
        else:
            self.state = ENTRY_STATE_SETUP_ERROR

    @asyncio.coroutine
    def async_unload(self, hass):
        """Unload an entry.

        Returns if unload is possible and was successful.
        """
        component = getattr(hass.components, self.domain)

        supports_unload = hasattr(component, 'async_unload_entry')

        if not supports_unload:
            return False

        try:
            result = yield from component.async_unload_entry(hass, self)
            return result
        except Exception:  # pylint: disable=broad-except
            _LOGGER.exception('Error unloading entry %s for %s',
                              self.title, self.domain)
            return False

    def as_dict(self):
        """Return dictionary version of this entry."""
        return {
            'entry_id': self.entry_id,
            'version': self.version,
            'domain': self.domain,
            'title': self.title,
            'data': self.data,
            'source': self.source,
            'state': self.state,
        }


class ConfigError(HomeAssistantError):
    """Error while configuring an account."""


class UnknownEntry(ConfigError):
    """Unknown entry specified."""


class UnknownHandler(ConfigError):
    """Unknown handler specified."""


class UnknownFlow(ConfigError):
    """Uknown flow specified."""


class UnknownStep(ConfigError):
    """Unknown step specified."""


class ConfigEntries:
    """Manage the configuration entries.

    An instance of this object is available via `hass.config_entries`.
    """

    def __init__(self, hass, hass_config):
        """Initialize the entry manager."""
        self.hass = hass
        self.flow = FlowManager(hass, hass_config)
        self._hass_config = hass_config
        self._entries = None
        self._sched_save = None

    @callback
    def async_domains(self):
        """Return domains for which we have entries."""
        seen = set()
        result = []

        for entry in self._entries:
            if entry.domain not in seen:
                seen.add(entry.domain)
                result.append(entry.domain)

        return result

    @callback
    def async_entries(self, domain=None):
        """Return all entries or entries for a specific domain."""
        if domain is None:
            return list(self._entries)
        return [entry for entry in self._entries if entry.domain == domain]

    @asyncio.coroutine
    def async_add_entry(self, entry):
        """Add an entry."""
        handler = yield from self.flow.async_get_handler(entry.domain)

        # Raises vol.Invalid if data does not conform schema
        handler.ENTRY_SCHEMA(entry.data)

        self._entries.append(entry)
        self._async_schedule_save()

        # Setup entry
        if entry.domain in self.hass.config.components:
            # Component already set up, just need to call setup_entry
            yield from entry.async_setup(self.hass)
        else:
            # Setting up component will also load the entries
            self.hass.async_add_job(
                async_setup_component, self.hass, entry.domain,
                self._hass_config)

    @asyncio.coroutine
    def async_remove(self, entry_id):
        """Remove an entry."""
        found = None
        for index, entry in enumerate(self._entries):
            if entry.entry_id == entry_id:
                found = index
                break

        if found is None:
            raise UnknownEntry

        entry = self._entries[found]
        self._entries.pop(found)
        self._async_schedule_save()

        unloaded = yield from entry.async_unload(self.hass)

        return {
            'require_restart': not unloaded
        }

    @asyncio.coroutine
    def async_load(self):
        """Load the config."""
        path = self.hass.config.path(PATH_CONFIG)
        if not os.path.isfile(path):
            self._entries = []

        entries = yield from self.hass.async_add_job(load_json, path)
        self._entries = [ConfigEntry(**entry) for entry in entries]

    @callback
    def _async_schedule_save(self):
        """Schedule saving the entity registry."""
        if self._sched_save is not None:
            self._sched_save.cancel()

        self._sched_save = self.hass.loop.call_later(
            SAVE_DELAY, self.hass.async_add_job, self._async_save
        )

    @asyncio.coroutine
    def _async_save(self):
        """Save the entity registry to a file."""
        self._sched_save = None
        data = [entry.as_dict() for entry in self._entries]

        yield from self.hass.async_add_job(
            save_json, self.hass.config.path(PATH_CONFIG), data)


class FlowManager:
    """Manage all the config flows that are in progress."""

    def __init__(self, hass, hass_config):
        """Initialize the config manager."""
        self.hass = hass
        self._hass_config = hass_config
        self._progress = {}

    @asyncio.coroutine
    def async_get_handler(self, domain, *, resolve_reqs_deps=False):
        """Get the handler for a specific domain."""
        handler = HANDLERS.get(domain)

        if handler is not None:
            return handler

        # This will load the component and thus register the handler
        component = getattr(self.hass.components, domain)

        handler = HANDLERS.get(domain)

        if handler is None:
            raise self.hass.helpers.UnknownHandler

        if resolve_reqs_deps:
            # Make sure requirements and dependencies of component are resolved
            yield from async_process_deps_reqs(
                self.hass, self._hass_config, domain, component)

        return handler

    @callback
    def async_progress(self):
        """Return the flows in progress."""
        return [{
            'flow_id': flow.flow_id,
            'domain': flow.domain,
            'source': flow.source,
        } for flow in self._progress.values()]

    @asyncio.coroutine
    def async_init(self, domain, *, source=SOURCE_USER, data=None):
        """Start a configuration flow."""
        handler = yield from self.async_get_handler(
            domain, resolve_reqs_deps=True)

        flow_id = uuid.uuid4().hex
        flow = self._progress[flow_id] = handler()
        flow.hass = self.hass
        flow.domain = domain
        flow.flow_id = flow_id
        flow.source = source

        if source == SOURCE_USER:
            step = 'init'
        else:
            step = source

        return (yield from self._async_handle_step(flow, step, data))

    @asyncio.coroutine
    def async_configure(self, flow_id, user_input=None):
        """Start or continue a configuration flow."""
        flow = self._progress.get(flow_id)

        if flow is None:
            raise UnknownFlow

        step_id, data_schema = flow.cur_step

        if data_schema is not None and user_input is not None:
            user_input = data_schema(user_input)

        return (yield from self._async_handle_step(
            flow, step_id, user_input))

    @callback
    def async_abort(self, flow_id):
        """Abort a flow."""
        if self._progress.pop(flow_id, None) is None:
            raise UnknownFlow

    @asyncio.coroutine
    def _async_handle_step(self, flow, step_id, user_input):
        """Handle a step of a flow."""
        method = "async_step_{}".format(step_id)

        if not hasattr(flow, method):
            self._progress.pop(flow.flow_id)
            raise UnknownStep("Handler {} doesn't support step {}".format(
                flow.__class__.__name__, step_id))

        result = yield from getattr(flow, method)(user_input)

        if result['type'] not in (RESULT_TYPE_FORM, RESULT_TYPE_CREATE_ENTRY,
                                  RESULT_TYPE_ABORT):
            raise ValueError(
                'Handler returned incorrect type: {}'.format(result['type']))

        if result['type'] == RESULT_TYPE_FORM:
            flow.cur_step = (result.pop('step_id'), result['data_schema'])
            return result

        # Abort and Success results both finish the flow
        self._progress.pop(flow.flow_id)

        if result['type'] == RESULT_TYPE_ABORT:
            return result

        entry = ConfigEntry(
            version=flow.VERSION,
            domain=flow.domain,
            title=result['title'],
            data=result.pop('data'),
            source=flow.source
        )
        yield from self.hass.config_entries.async_add_entry(entry)
        return result


class ConfigFlowHandler:
    """Handle the configuration flow of a component."""

    # Set by config manager
    flow_id = None
    hass = None
    source = SOURCE_USER
    cur_step = None

    # Set by dev
    VERSION = 0

    @callback
    def async_show_form(self, *, title, step_id, description=None,
                        data_schema=None, errors=None):
        """Return the definition of a form to gather user input."""
        return {
            'type': RESULT_TYPE_FORM,
            'flow_id': self.flow_id,
            'title': title,
            'step_id': step_id,
            'description': description,
            'data_schema': data_schema,
            'errors': errors,
        }

    @callback
    def async_create_entry(self, *, title, data):
        """Finish config flow and create a config entry."""
        return {
            'type': RESULT_TYPE_CREATE_ENTRY,
            'flow_id': self.flow_id,
            'title': title,
            'data': data,
        }

    @callback
    def async_abort(self, *, reason):
        """Abort the config flow."""
        return {
            'type': RESULT_TYPE_ABORT,
            'flow_id': self.flow_id,
            'reason': reason
        }
