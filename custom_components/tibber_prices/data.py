"""Custom types for tibber_prices."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from typing import TYPE_CHECKING, Any

from homeassistant.util import dt as dt_util

from .const import (
    PRICE_LEVEL_CHEAP,
    PRICE_LEVEL_EXPENSIVE,
    PRICE_LEVEL_NORMAL,
    PRICE_LEVEL_UNKNOWN,
    PRICE_LEVEL_VERY_CHEAP,
    PRICE_LEVEL_VERY_EXPENSIVE,
    PRICE_RATING_HIGH,
    PRICE_RATING_LOW,
    PRICE_RATING_NORMAL,
    PRICE_RATING_UNKNOWN,
)

if TYPE_CHECKING:
    from homeassistant.loader import Integration

    from .api import TibberPricesApiClient
    from .coordinator import TibberPricesDataUpdateCoordinator


class PriceLevel(str, Enum):
    """Enum for price levels."""

    VERY_CHEAP = PRICE_LEVEL_VERY_CHEAP
    CHEAP = PRICE_LEVEL_CHEAP
    NORMAL = PRICE_LEVEL_NORMAL
    EXPENSIVE = PRICE_LEVEL_EXPENSIVE
    VERY_EXPENSIVE = PRICE_LEVEL_VERY_EXPENSIVE
    UNKNOWN = PRICE_LEVEL_UNKNOWN


class PriceRatingLevel(str, Enum):
    """Enum for price rating levels."""

    LOW = PRICE_RATING_LOW
    NORMAL = PRICE_RATING_NORMAL
    HIGH = PRICE_RATING_HIGH
    UNKNOWN = PRICE_RATING_UNKNOWN


@dataclass
class TibberPricesConfigEntry:
    """Type definition for TibberPrices config entry."""

    client: TibberPricesApiClient
    coordinator: TibberPricesDataUpdateCoordinator
    integration: Integration


class TibberAddress(dict):
    """Tibber address data."""

    address1: str
    postal_code: str
    city: str
    country: str


class TibberHome(dict):
    """Tibber home data."""

    id: str
    type: str
    app_nickname: str | None
    address: TibberAddress


class TibberUser(dict):
    """Tibber user data."""

    user_id: str
    name: str
    login: str
    homes: list[TibberHome]


class TibberViewer(dict):
    """Tibber viewer data."""

    viewer: TibberUser


@dataclass
class PriceInfo:
    """Price information for a single price point."""

    starts_at: datetime
    total: float
    energy: float
    tax: float
    level: PriceLevel
    currency: str = ""

    @classmethod
    def from_api_response(cls, data: dict[str, Any], currency: str = "") -> PriceInfo:
        """Create PriceInfo from API response data."""
        starts_at = dt_util.parse_datetime(data["startsAt"])
        if starts_at is None:
            starts_at = datetime.now(dt_util.DEFAULT_TIME_ZONE)

        level_str = data.get("level", PRICE_LEVEL_UNKNOWN)
        try:
            level = PriceLevel(level_str)
        except ValueError:
            level = PriceLevel.UNKNOWN

        return cls(
            starts_at=starts_at,
            total=float(data.get("total", 0.0)),
            energy=float(data.get("energy", 0.0)),
            tax=float(data.get("tax", 0.0)),
            level=level,
            currency=currency,
        )


@dataclass
class PriceInfoRange:
    """Price information range data."""

    prices: list[PriceInfo] = field(default_factory=list)

    @classmethod
    def from_range_response(cls, data: dict[str, Any], currency: str = "") -> PriceInfoRange:
        """Create PriceInfoRange from range API response."""
        result = cls()
        edges = data.get("edges", [])
        for edge in edges:
            node = edge.get("node", {})
            price_info = PriceInfo.from_api_response(node, currency)
            result.prices.append(price_info)
        return result


@dataclass
class HomeCurrentPriceInfo:
    """Current price information for a home."""

    home_id: str
    current: PriceInfo | None = None
    today: list[PriceInfo] = field(default_factory=list)
    tomorrow: list[PriceInfo] = field(default_factory=list)
    range_prices: list[PriceInfo] = field(default_factory=list)
    currency: str = ""

    def get_current_price(self) -> PriceInfo | None:
        """Get the current price information."""
        now = datetime.now(dt_util.DEFAULT_TIME_ZONE)
        if self.current and self.current.starts_at.hour == now.hour:
            return self.current

        # Find the current price from today's prices
        for price in self.today:
            if price.starts_at.hour == now.hour:
                self.current = price
                return price

        return None

    def get_price_at(self, target_time: datetime) -> PriceInfo | None:
        """Get price information at a specific time."""
        if not target_time.tzinfo:
            target_time = target_time.replace(tzinfo=dt_util.DEFAULT_TIME_ZONE)

        # Check in today's prices
        for price in self.today:
            if price.starts_at.hour == target_time.hour and price.starts_at.date() == target_time.date():
                return price

        # Check in tomorrow's prices
        for price in self.tomorrow:
            if price.starts_at.hour == target_time.hour and price.starts_at.date() == target_time.date():
                return price

        # Check in range prices
        for price in self.range_prices:
            if price.starts_at.hour == target_time.hour and price.starts_at.date() == target_time.date():
                return price

        return None

    def get_cheapest_hours(self, num_hours: int = 1) -> list[PriceInfo]:
        """Get the cheapest hours within the next 24 hours."""
        now = datetime.now(dt_util.DEFAULT_TIME_ZONE)
        future_hours = [price for price in self.today + self.tomorrow if price.starts_at >= now]

        # Sort by total price and return the cheapest ones
        future_hours.sort(key=lambda p: p.total)
        return future_hours[:num_hours]


@dataclass
class PriceRatingThresholds:
    """Price rating thresholds."""

    low: float
    high: float

    @classmethod
    def from_api_response(cls, data: dict[str, Any]) -> PriceRatingThresholds:
        """Create PriceRatingThresholds from API response data."""
        return cls(
            low=float(data.get("low", 0.33)),
            high=float(data.get("high", 0.66)),
        )


@dataclass
class PriceRatingEntry:
    """Price rating entry data."""

    time: datetime
    total: float
    energy: float
    tax: float
    difference: float
    level: PriceRatingLevel

    @classmethod
    def from_api_response(cls, data: dict[str, Any]) -> PriceRatingEntry:
        """Create PriceRatingEntry from API response data."""
        time_str = data.get("time", "")
        time = dt_util.parse_datetime(time_str)
        if time is None:
            time = datetime.now(dt_util.DEFAULT_TIME_ZONE)

        level_str = data.get("level", "").lower()
        if level_str == "low":
            level = PriceRatingLevel.LOW
        elif level_str == "high":
            level = PriceRatingLevel.HIGH
        elif level_str == "normal":
            level = PriceRatingLevel.NORMAL
        else:
            level = PriceRatingLevel.UNKNOWN

        return cls(
            time=time,
            total=float(data.get("total", 0.0)),
            energy=float(data.get("energy", 0.0)),
            tax=float(data.get("tax", 0.0)),
            difference=float(data.get("difference", 0.0)),
            level=level,
        )


@dataclass
class PriceRatingPeriod:
    """Price rating period data."""

    currency: str
    entries: list[PriceRatingEntry] = field(default_factory=list)

    @classmethod
    def from_api_response(cls, data: dict[str, Any]) -> PriceRatingPeriod:
        """Create PriceRatingPeriod from API response data."""
        result = cls(currency=data.get("currency", ""))
        for entry_data in data.get("entries", []):
            entry = PriceRatingEntry.from_api_response(entry_data)
            result.entries.append(entry)
        return result


@dataclass
class HomePriceRating:
    """Price rating for a home."""

    home_id: str
    thresholds: PriceRatingThresholds
    hourly: PriceRatingPeriod | None = None
    daily: PriceRatingPeriod | None = None
    monthly: PriceRatingPeriod | None = None

    def get_current_rating(self) -> PriceRatingEntry | None:
        """Get the current price rating."""
        if not self.hourly or not self.hourly.entries:
            return None

        now = datetime.now(dt_util.DEFAULT_TIME_ZONE)
        for entry in self.hourly.entries:
            if entry.time.hour == now.hour and entry.time.date() == now.date():
                return entry

        return None

    def get_day_average(self, target_date: datetime | None = None) -> float:
        """Get the average price for a specific day."""
        if not self.daily or not self.daily.entries:
            return 0.0

        if target_date is None:
            target_date = datetime.now(dt_util.DEFAULT_TIME_ZONE)

        total_sum = 0.0
        count = 0

        for entry in self.daily.entries:
            if entry.time.date() == target_date.date():
                total_sum += entry.total
                count += 1

        if count > 0:
            return total_sum / count

        return 0.0


@dataclass
class TibberPricesData:
    """Data for the TibberPrices integration."""

    client: TibberPricesApiClient
    coordinator: TibberPricesDataUpdateCoordinator | None = None
    integration: Integration | None = None

    # User and home information
    user_info: TibberUser | None = None
    homes: dict[str, TibberHome] = field(default_factory=dict)

    # Price information per home
    price_info: dict[str, HomeCurrentPriceInfo] = field(default_factory=dict)
    price_rating: dict[str, HomePriceRating] = field(default_factory=dict)

    def get_home_names(self) -> list[tuple[str, str]]:
        """Get list of home IDs and names."""
        result = []
        for home_id, home in self.homes.items():
            name = home.get("app_nickname") or f"Home {home_id[-6:]}"
            result.append((home_id, name))
        return result

    def get_cheapest_time_today(self, home_id: str) -> datetime | None:
        """Get the cheapest time to run appliances today."""
        if home_id not in self.price_info:
            return None

        price_info = self.price_info[home_id]
        now = datetime.now(dt_util.DEFAULT_TIME_ZONE)
        future_prices = [p for p in price_info.today if p.starts_at >= now]

        if not future_prices:
            return None

        cheapest = min(future_prices, key=lambda p: p.total)
        return cheapest.starts_at

    def calculate_price_difference(self, home_id: str) -> float | None:
        """Calculate the price difference from average (in percentage)."""
        if home_id not in self.price_info:
            return None

        price_info = self.price_info[home_id]
        current = price_info.get_current_price()

        if not current or not price_info.today:
            return None

        # Calculate the average price for today
        total = sum(p.total for p in price_info.today)
        avg = total / len(price_info.today)

        if avg == 0:
            return 0

        return ((current.total - avg) / avg) * 100

    def get_price_distribution(self, home_id: str) -> dict[PriceLevel, int]:
        """Get distribution of price levels for today."""
        if home_id not in self.price_info:
            return {}

        price_info = self.price_info[home_id]
        result = {
            PriceLevel.VERY_CHEAP: 0,
            PriceLevel.CHEAP: 0,
            PriceLevel.NORMAL: 0,
            PriceLevel.EXPENSIVE: 0,
            PriceLevel.VERY_EXPENSIVE: 0,
            PriceLevel.UNKNOWN: 0,
        }

        for price in price_info.today:
            result[price.level] += 1

        return result

    def get_rating_distribution(self, home_id: str) -> dict[PriceRatingLevel, int]:
        """Get distribution of price rating levels for today."""
        if home_id not in self.price_rating:
            return {}

        price_rating = self.price_rating[home_id]
        if not price_rating.daily or not price_rating.daily.entries:
            return {}

        result = {
            PriceRatingLevel.LOW: 0,
            PriceRatingLevel.NORMAL: 0,
            PriceRatingLevel.HIGH: 0,
            PriceRatingLevel.UNKNOWN: 0,
        }

        for entry in price_rating.daily.entries:
            result[entry.level] += 1

        return result
