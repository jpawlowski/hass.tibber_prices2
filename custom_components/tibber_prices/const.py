"""Constants for tibber_prices."""

from logging import Logger, getLogger

LOGGER: Logger = getLogger(__package__)

# Integration constants
DOMAIN = "tibber_prices"
NAME = "Tibber Prices"
VERSION = "0.1.0"
ATTRIBUTION = "Data provided by Tibber API"

# Tibber API constants
TIBBER_API_URL = "https://api.tibber.com/v1-beta/gql"
DEFAULT_TIMEOUT = 10
MAX_RETRIES = 3
RETRY_DELAY = 1.0
HTTP_RATE_LIMIT_TOO_MANY_REQUESTS = 429

# Configuration constants
CONF_HOME_ID = "home_id"
CONF_HOME_NAME = "home_name"
CONF_SCAN_INTERVAL = "scan_interval"
CONF_PRICE_UNIT = "price_unit"
CONF_FETCH_MODE = "fetch_mode"
CONF_PARENT_ENTRY_ID = "parent_entry_id"
CONF_SUB_ENTRY = "sub_entry"

# Default values
DEFAULT_SCAN_INTERVAL = 60  # 1 hour in minutes
DEFAULT_PRICE_UNIT = "kWh"
DEFAULT_FETCH_MODE = "auto"  # Auto, Conservative, Active, Aggressive

# Fetch mode options
FETCH_MODE_AUTO = "auto"
FETCH_MODE_CONSERVATIVE = "conservative"
FETCH_MODE_ACTIVE = "active"
FETCH_MODE_AGGRESSIVE = "aggressive"

# Entity constants
ATTR_HOME_ID = "home_id"
ATTR_HOME_NAME = "home_name"
ATTR_PRICE_LEVEL = "price_level"
ATTR_CURRENCY = "currency"
ATTR_ENERGY = "energy"
ATTR_TAX = "tax"
ATTR_TOTAL = "total"
ATTR_STARTS_AT = "starts_at"
ATTR_THRESHOLD_LOW = "threshold_low"
ATTR_THRESHOLD_HIGH = "threshold_high"

# Price level names
PRICE_LEVEL_VERY_CHEAP = "very_cheap"
PRICE_LEVEL_CHEAP = "cheap"
PRICE_LEVEL_NORMAL = "normal"
PRICE_LEVEL_EXPENSIVE = "expensive"
PRICE_LEVEL_VERY_EXPENSIVE = "very_expensive"
PRICE_LEVEL_UNKNOWN = "unknown"

# Price rating level names
PRICE_RATING_LOW = "low"
PRICE_RATING_NORMAL = "normal"
PRICE_RATING_HIGH = "high"
PRICE_RATING_UNKNOWN = "unknown"

# Services
SERVICE_REFRESH = "refresh"
