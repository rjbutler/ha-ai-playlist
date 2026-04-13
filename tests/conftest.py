"""Test configuration — stub homeassistant modules for testing without HA installed."""
import sys
from pathlib import Path
from unittest.mock import MagicMock, AsyncMock

# Add project root to path so custom_components is importable
ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

# ── Stub homeassistant modules ───────────────────────────────────
# These stubs allow importing integration code without a real HA install.
# Only the module structure matters — actual HA functionality is not tested.


def _make_module(name, **attrs):
    mod = MagicMock()
    mod.__name__ = name
    for k, v in attrs.items():
        setattr(mod, k, v)
    return mod


# Core HA modules
_ha_modules = {
    "homeassistant": _make_module("homeassistant"),
    "homeassistant.const": _make_module("homeassistant.const",
        EVENT_HOMEASSISTANT_STARTED="homeassistant_started",
    ),
    "homeassistant.core": _make_module("homeassistant.core"),
    "homeassistant.config_entries": _make_module("homeassistant.config_entries"),
    "homeassistant.exceptions": _make_module("homeassistant.exceptions"),
    "homeassistant.helpers": _make_module("homeassistant.helpers"),
    "homeassistant.helpers.storage": _make_module("homeassistant.helpers.storage"),
    "homeassistant.helpers.event": _make_module("homeassistant.helpers.event"),
    "homeassistant.helpers.entity_platform": _make_module("homeassistant.helpers.entity_platform"),
    "homeassistant.helpers.restore_state": _make_module("homeassistant.helpers.restore_state"),
    "homeassistant.helpers.selector": _make_module("homeassistant.helpers.selector"),
    "homeassistant.components": _make_module("homeassistant.components"),
    "homeassistant.components.sensor": _make_module("homeassistant.components.sensor"),
    "homeassistant.components.select": _make_module("homeassistant.components.select"),
}

# Create real base classes (not MagicMock) to avoid metaclass conflicts
class _SensorEntity:
    pass

class _SelectEntity:
    pass

class _RestoreEntity:
    pass

_ha_modules["homeassistant.components.sensor"].SensorEntity = _SensorEntity
_ha_modules["homeassistant.components.select"].SelectEntity = _SelectEntity
_ha_modules["homeassistant.helpers.restore_state"].RestoreEntity = _RestoreEntity

# callback decorator stub — just return the function
_ha_modules["homeassistant.core"].callback = lambda f: f
_ha_modules["homeassistant.core"].CALLBACK_TYPE = None
_ha_modules["homeassistant.core"].Event = object
_ha_modules["homeassistant.core"].HomeAssistant = object

# Add Platform enum stub
class _Platform:
    SELECT = "select"
    SENSOR = "sensor"
_ha_modules["homeassistant.const"].Platform = _Platform

# Add SupportsResponse stub
class _SupportsResponse:
    OPTIONAL = "optional"
_ha_modules["homeassistant.core"].SupportsResponse = _SupportsResponse

# Add HomeAssistantError stub
class _HomeAssistantError(Exception):
    pass
_ha_modules["homeassistant.exceptions"].HomeAssistantError = _HomeAssistantError

# Add ConfigEntry and ConfigEntryState stubs
class _ConfigEntry:
    pass
class _ConfigEntryState:
    LOADED = "loaded"
_ha_modules["homeassistant.config_entries"].ConfigEntry = _ConfigEntry
_ha_modules["homeassistant.config_entries"].ConfigEntryState = _ConfigEntryState

# Add Store stub
class _Store:
    def __init__(self, *args, **kwargs):
        pass
    async def async_load(self):
        return None
    async def async_save(self, data):
        pass
_ha_modules["homeassistant.helpers.storage"].Store = _Store

# Add voluptuous stub
_ha_modules["voluptuous"] = _make_module("voluptuous")

# Add config_entries module-level objects
_ha_modules["homeassistant.config_entries"].config_entries = _make_module("config_entries")

# Register all stubs
for mod_name, mod in _ha_modules.items():
    sys.modules.setdefault(mod_name, mod)

# Also stub music_assistant_models if needed
sys.modules.setdefault("music_assistant_models", _make_module("music_assistant_models"))
sys.modules.setdefault("music_assistant_models.enums", _make_module("music_assistant_models.enums"))
