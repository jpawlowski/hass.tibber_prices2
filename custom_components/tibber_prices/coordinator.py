"""DataUpdateCoordinator for tibber_prices."""

from __future__ import annotations

import asyncio
import secrets
from datetime import datetime, time, timedelta
from enum import Enum
from typing import TYPE_CHECKING, Any, Final

from homeassistant.core import HomeAssistant, callback
from homeassistant.exceptions import ConfigEntryAuthFailed
from homeassistant.helpers.storage import Store
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed
from homeassistant.util import dt as dt_util

from .api import (
    TibberPricesApiClient,
    TibberPricesApiClientAuthenticationError,
    TibberPricesApiClientError,
    TibberPricesApiClientRateLimitError,
)
from .const import DOMAIN, LOGGER
from .helpers import (
    check_for_missed_midnight_transition,
    check_for_missing_current_hour,
    perform_midnight_rotation,
    validate_cache_structure,
)

if TYPE_CHECKING:
    from homeassistant.config_entries import ConfigEntry


class ApiState(str, Enum):
    """Enum for API fetching states based on time of day and data availability."""

    IDLE = "idle"  # No API calls needed - have all data or too early for tomorrow data
    WAITING = "waiting"  # 13:00-15:00: Periodically check for tomorrow's data, distributed
    SEARCHING = "searching"  # 15:00-00:00: Actively searching for tomorrow's data


# Time windows for different API states
TOMORROW_DATA_CHECK_START: Final = time(13, 0)  # Start checking for tomorrow data
INTENSIVE_SEARCH_START: Final = time(15, 0)  # Start more intensive searching

# Entity state update schedule (always happens at quarter-hour marks)
ENTITY_UPDATE_INTERVAL: Final = timedelta(minutes=15)
ENTITY_UPDATE_MINUTES: Final = [0, 15, 30, 45]  # Standard quarter-hour alignment

# API fetch intervals
WAITING_CHECK_INTERVAL: Final = timedelta(minutes=15)  # 15-min distributed checks
SEARCHING_CHECK_INTERVAL: Final = timedelta(minutes=5)  # Frequent checks when searching

# Minutes offset for distributed API calls during WAITING state
API_MINUTE_OFFSETS: Final = [0, 1, 2, 3, 4]

# Hours in a day constant to avoid magic number
HOURS_IN_DAY: Final = 24


# https://developers.home-assistant.io/docs/integration_fetching_data#coordinated-single-api-poll-for-data-for-all-entities
class TibberPricesDataUpdateCoordinator(DataUpdateCoordinator):
    """Class to manage fetching data from the API with time-aware strategy."""

    config_entry: ConfigEntry
    _last_full_update: datetime | None = None
    _last_tomorrow_check: datetime | None = None
    _tomorrow_data_available: bool = False
    _scheduled_update_task: asyncio.Task | None = None

    def __init__(
        self,
        hass: HomeAssistant,
        client: TibberPricesApiClient,
        home_id: str | None = None,
        **kwargs: Any,
    ) -> None:
        """Initialize coordinator with time-aware update strategy."""
        # Set to None to disable automatic scheduling
        kwargs["update_interval"] = None

        super().__init__(
            hass=hass,
            logger=LOGGER,
            name="tibber_prices",
            **kwargs,
        )
        self.client = client
        self.home_id = home_id
        self._data_cache: dict[str, Any] = {}
        self._initialized: bool = False

        # Generate a stable offset for this installation to distribute API calls
        if home_id:
            # Use hash of home_id for stable distribution
            self._offset_index = abs(hash(home_id)) % len(API_MINUTE_OFFSETS)
        else:
            # Fallback to random offset if no home_id
            self._offset_index = secrets.randbelow(len(API_MINUTE_OFFSETS))

        self.logger.debug("Using minute offset index %s for API distribution", self._offset_index)

    async def async_initialize(self) -> None:
        """
        Initialize the coordinator by loading data and setting up updates.

        This method loads cached data and prepares the coordinator for operation,
        but does not perform a refresh. Call async_config_entry_first_refresh()
        afterwards if you need an immediate refresh.
        """
        self.logger.info("====== INITIALIZING TIBBER PRICES COORDINATOR ======")

        # Load data from persistent storage
        await self._load_cached_data()

        # Mark as initialized - ready to handle refresh calls
        self._initialized = True

        # Log initial state based on loaded cache
        initial_state = self.current_api_state
        has_cache = bool(self._data_cache.get("user_info"))

        self.logger.info(
            "Initialization complete - %s (API state: %s, tomorrow data: %s)",
            "using cached data" if has_cache else "no cached data found",
            initial_state.value,
            "available" if self._tomorrow_data_available else "not available",
        )

        # Schedule first update at the next aligned quarter-hour
        self._schedule_next_entity_update()

    # Method removed as it was redundant with async_initialize

    @property
    def current_api_state(self) -> ApiState:
        """Determine the current API state based on data availability and time of day."""
        # First, check if we have all the data we need
        # If we already have tomorrow's data, we're in IDLE state regardless of time
        if self._tomorrow_data_available:
            return ApiState.IDLE

        # If we're missing basic data, we need to search regardless of time
        if not self._data_cache.get("user_info") or not self._data_cache.get("homes"):
            return ApiState.SEARCHING

        # If we're missing today's data, we need to search regardless of time
        if self._is_missing_today_data():
            return ApiState.SEARCHING

        # Otherwise, base state on time of day
        now = dt_util.now().time()

        if now < TOMORROW_DATA_CHECK_START:
            return ApiState.IDLE  # Too early to check for tomorrow's data
        if now < INTENSIVE_SEARCH_START:
            return ApiState.WAITING  # Time to start periodic checks
        return ApiState.SEARCHING  # Time to search more intensively

    async def _load_cached_data(self) -> None:
        """Load data from persistent storage."""
        self.logger.info("INITIALIZATION PHASE: Loading cached data")
        store = Store(self.hass, 1, f"{DOMAIN}_{self.config_entry.entry_id}")
        stored_data = await store.async_load()

        if stored_data:
            self.logger.info("Cache found: Restoring data from persistent storage")
            self._data_cache = stored_data.get("data", {})

            # Check if we have tomorrow's data available
            if "price_info" in self._data_cache:
                homes_count = len(self._data_cache.get("homes", {}))
                price_info_count = len(self._data_cache.get("price_info", {}))
                tomorrow_data_exists = any(
                    price_info.get("tomorrow") for price_info in self._data_cache["price_info"].values()
                )
                self._tomorrow_data_available = tomorrow_data_exists

                self.logger.info(
                    "Cache contains: %d homes, %d price info records, tomorrow's data: %s",
                    homes_count,
                    price_info_count,
                    "available" if self._tomorrow_data_available else "not available",
                )

                # Check if we need to perform a missed midnight data rotation
                await self._check_and_handle_missed_midnight_transition()
        else:
            self.logger.info("First run: No cached data found - will perform full initialization")

    async def _save_cached_data(self) -> None:
        """Save data to persistent storage."""
        store = Store(self.hass, 1, f"{DOMAIN}_{self.config_entry.entry_id}")
        await store.async_save({"data": self._data_cache})

        homes_count = len(self._data_cache.get("homes", {}))
        price_info_count = len(self._data_cache.get("price_info", {}))

        # Calculate more detailed statistics
        today_prices_count = sum(len(info.get("today", [])) for info in self._data_cache.get("price_info", {}).values())
        tomorrow_prices_count = sum(
            len(info.get("tomorrow", [])) for info in self._data_cache.get("price_info", {}).values()
        )

        self.logger.info(
            "CACHE UPDATE: Saved %d homes, %d price records (%d today prices, %d tomorrow prices)",
            homes_count,
            price_info_count,
            today_prices_count,
            tomorrow_prices_count,
        )

    async def async_refresh(self) -> None:
        """Refresh data and save to persistent storage."""
        # If not initialized, don't do a refresh yet
        if not self._initialized:
            self.logger.debug("Skipping refresh: Coordinator not fully initialized yet")
            return

        # Log the refresh attempt
        is_first_refresh = not bool(self.data)
        if is_first_refresh:
            self.logger.info("====== PERFORMING INITIAL DATA REFRESH ======")
        else:
            self.logger.debug("Performing scheduled data refresh")

        # Call the parent's refresh which will call our _async_update_data()
        start_time = dt_util.now()
        await super().async_refresh()
        duration = (dt_util.now() - start_time).total_seconds()

        # Only need to explicitly save if we got data
        if self.data and self.data != {}:
            await self._save_cached_data()

        # Log completion info
        if is_first_refresh:
            self.logger.info(
                "Initial data refresh complete in %.3f seconds - API state: %s", duration, self.current_api_state.value
            )

    def _is_missing_today_data(self) -> bool:
        """Check if today's price data is missing."""
        return any(not price_info.get("today") for price_info in self._data_cache.get("price_info", {}).values())

    def _should_check_in_waiting_state(self) -> bool:
        """Determine if we should check for data in WAITING state (13:00-15:00)."""
        now = dt_util.now()

        # Skip if we checked recently
        if self._last_tomorrow_check and (now - self._last_tomorrow_check) < WAITING_CHECK_INTERVAL:
            return False

        # Only check at specific distributed minutes
        target_minute = ENTITY_UPDATE_MINUTES[self._offset_index % len(ENTITY_UPDATE_MINUTES)]
        minute_offset = API_MINUTE_OFFSETS[self._offset_index % len(API_MINUTE_OFFSETS)]
        return now.minute == (target_minute + minute_offset) % 60

    def _should_check_in_searching_state(self) -> bool:
        """Determine if we should check for data in SEARCHING state (15:00-00:00)."""
        now = dt_util.now()

        # Check frequently until we get tomorrow's data
        return not self._last_tomorrow_check or (now - self._last_tomorrow_check) >= SEARCHING_CHECK_INTERVAL

    def _should_fetch_data(self) -> bool:
        """Determine whether we should fetch data from the API based on current state."""
        # Skip fetch if we haven't completed initialization
        if not self._initialized:
            self.logger.debug("Skipping fetch: Coordinator not fully initialized yet")
            return False

        # First run or missing basic data - always fetch
        if not self._data_cache.get("user_info") or not self._data_cache.get("homes"):
            self.logger.debug("Fetching data: First run or missing basic data")
            return True

        # Missing today's data - always fetch
        if self._is_missing_today_data():
            self.logger.debug("Fetching data: Missing today's data")
            return True

        # Check API state
        state = self.current_api_state

        # In IDLE state, no need to fetch data
        if state == ApiState.IDLE:
            self.logger.debug("Not fetching data: In idle state (have tomorrow's data or before 13:00)")
            return False

        # In WAITING state, check periodically based on distribution
        if state == ApiState.WAITING:
            fetch_decision = self._should_check_in_waiting_state()
            if fetch_decision:
                self.logger.debug("Fetching data: Scheduled distributed check for tomorrow's data")
            return fetch_decision

        # In SEARCHING state, check more frequently
        fetch_decision = self._should_check_in_searching_state()
        if fetch_decision:
            self.logger.debug("Fetching data: Actively searching for tomorrow's data")
        return fetch_decision

    async def _async_update_data(self) -> dict[str, Any]:
        """Update data via library with time-aware strategy."""
        now = dt_util.now()
        data = self._data_cache.copy()

        # For tracking API calls and timing
        start_time = now

        try:
            # Get client from hass.data
            client = self._get_api_client()
            if not client:
                self.logger.error("API client not available")
                return self._data_cache

            # Log current state and cache status
            self._log_update_status()

            # Check if we should fetch new data
            if not self._should_fetch_data():
                self.logger.debug("Using cached data - no API call needed, just entity recalculation")
                self._schedule_next_entity_update()
                return self._data_cache

            state = self.current_api_state
            self.logger.info("===== STARTING API DATA UPDATE CYCLE =====")
            self.logger.info(
                "API state: %s, Time window: %s",
                state.value,
                (
                    "before 13:00"
                    if now.time() < TOMORROW_DATA_CHECK_START
                    else "13:00-15:00"
                    if now.time() < INTENSIVE_SEARCH_START
                    else "after 15:00"
                ),
            )

            # Fetch basic data if needed
            await self._fetch_basic_data(client, data)

            # Get price info
            state = self.current_api_state
            self.logger.debug("Fetching price info from API (state: %s)", state.value)
            price_info = await client.async_get_price_info()
            self._process_price_info(price_info, data)
            self._last_full_update = now

            # Get price ratings
            await self._fetch_price_ratings(client, state, data)

            # Check for tomorrow's data
            if state in (ApiState.WAITING, ApiState.SEARCHING):
                await self._check_tomorrow_data(client, state, data)

            # Update cache and save
            self._data_cache = data
            await self._save_cached_data()

            # Log state transition and data summary
            self._log_state_transition(state)
            self._log_data_summary(data)

            # Log update timing
            duration = (dt_util.now() - start_time).total_seconds()
            self.logger.info("API update cycle completed in %.3f seconds", duration)

            # Schedule next update
            self._schedule_next_entity_update()

            return data
        except TibberPricesApiClientAuthenticationError as exception:
            raise ConfigEntryAuthFailed(exception) from exception
        except TibberPricesApiClientRateLimitError as exception:
            self.logger.warning("Rate limit exceeded, using cached data: %s", exception)
            return self._data_cache
        except TibberPricesApiClientError as exception:
            raise UpdateFailed(exception) from exception
        else:
            return data

    async def _fetch_price_ratings(self, client: TibberPricesApiClient, state: ApiState, data: dict[str, Any]) -> None:
        """Fetch price ratings based on the current state."""
        # Track API calls for more detailed logging
        api_calls = 0
        start_time = dt_util.now()

        # Daily ratings - fetch always
        self.logger.debug("Fetching daily price ratings from API")
        daily_ratings = await client.async_get_daily_price_rating()
        self._process_price_rating(daily_ratings, "daily", data)
        api_calls += 1

        # Hourly ratings - fetch during active states, or if not in cache
        if state != ApiState.IDLE or "hourly" not in data.get("price_rating", {}):
            self.logger.debug("Fetching hourly price ratings from API")
            hourly_ratings = await client.async_get_hourly_price_rating()
            self._process_price_rating(hourly_ratings, "hourly", data)
            api_calls += 1

        # Monthly ratings - fetch only in IDLE state to minimize API calls, or if not in cache
        if state == ApiState.IDLE or "monthly" not in data.get("price_rating", {}):
            self.logger.debug("Fetching monthly price ratings from API")
            monthly_ratings = await client.async_get_monthly_price_rating()
            self._process_price_rating(monthly_ratings, "monthly", data)
            api_calls += 1

        # Log timing information
        duration = (dt_util.now() - start_time).total_seconds()
        self.logger.debug("Price ratings fetch complete: %d API calls in %.3f seconds", api_calls, duration)

    async def _check_tomorrow_data(self, client: TibberPricesApiClient, state: ApiState, data: dict[str, Any]) -> None:
        """Check if tomorrow's data is available and update if needed."""
        now = dt_util.now()
        start_time = now
        tomorrow = (now + timedelta(days=1)).date()
        tomorrow_str = tomorrow.strftime("%Y-%m-%d")

        # Skip if we checked recently and we're in WAITING state
        if (
            self._last_tomorrow_check
            and (now - self._last_tomorrow_check) < WAITING_CHECK_INTERVAL
            and state == ApiState.WAITING
        ):
            return

        old_tomorrow_data_status = self._tomorrow_data_available

        # Check if we have tomorrow's data for each home
        self._tomorrow_data_available = True
        homes_with_tomorrow = 0
        total_homes = len(data.get("price_info", {}))

        for price_info in data.get("price_info", {}).values():
            if not price_info.get("tomorrow"):
                self._tomorrow_data_available = False
                continue

            # Check if the data is actually for tomorrow
            has_tomorrow_data = False
            for price in price_info.get("tomorrow", []):
                # Parse the datetime string before attempting to access date()
                starts_at = dt_util.parse_datetime(price["startsAt"])
                if starts_at and starts_at.date() == tomorrow:
                    has_tomorrow_data = True
                    homes_with_tomorrow += 1
                    break

            if not has_tomorrow_data:
                self._tomorrow_data_available = False

        self._last_tomorrow_check = now
        duration = (now - start_time).total_seconds()

        # Log the result of the tomorrow data check
        if self._tomorrow_data_available != old_tomorrow_data_status:
            if self._tomorrow_data_available:
                self.logger.info(
                    "TOMORROW DATA CHECK: Found complete price data for %s (all %d homes) in %.3f seconds",
                    tomorrow_str,
                    total_homes,
                    duration,
                )
            else:
                self.logger.info(
                    "TOMORROW DATA CHECK: Still waiting for complete price data for %s"
                    " (%d/%d homes have data) - check took %.3f seconds",
                    tomorrow_str,
                    homes_with_tomorrow,
                    total_homes,
                    duration,
                )

        # If we're in SEARCHING state and don't have tomorrow's data, try to fetch it
        if state == ApiState.SEARCHING and not self._tomorrow_data_available:
            self.logger.debug("Actively searching for tomorrow's data in SEARCHING state")
            price_info = await client.async_get_price_info()
            self._process_price_info(price_info, data)

    def _process_price_info(self, price_info: dict[str, Any], data: dict[str, Any]) -> None:
        """Process and store price information in the data dictionary."""
        if "viewer" not in price_info or "homes" not in price_info["viewer"]:
            return

        if "price_info" not in data:
            data["price_info"] = {}

        for home in price_info["viewer"]["homes"]:
            home_id = home["id"]
            subscription = home.get("currentSubscription", {})
            price_info_data = subscription.get("priceInfo", {})

            if home_id not in data["price_info"]:
                data["price_info"][home_id] = {}

            # Process range prices
            if "range" in price_info_data and "edges" in price_info_data["range"]:
                data["price_info"][home_id]["range_prices"] = []
                for edge in price_info_data["range"]["edges"]:
                    if "node" in edge:
                        data["price_info"][home_id]["range_prices"].append(edge["node"])

            # Process today's prices
            if "today" in price_info_data:
                data["price_info"][home_id]["today"] = price_info_data["today"]

            # Process tomorrow's prices
            if "tomorrow" in price_info_data:
                data["price_info"][home_id]["tomorrow"] = price_info_data["tomorrow"]

    def _process_price_rating(self, price_rating: dict[str, Any], period_type: str, data: dict[str, Any]) -> None:
        """Process and store price rating information in the data dictionary."""
        if "viewer" not in price_rating or "homes" not in price_rating["viewer"]:
            return

        if "price_rating" not in data:
            data["price_rating"] = {}

        for home in price_rating["viewer"]["homes"]:
            home_id = home["id"]
            subscription = home.get("currentSubscription", {})
            rating_data = subscription.get("priceRating", {})

            if home_id not in data["price_rating"]:
                data["price_rating"][home_id] = {}

            # Store threshold percentages
            if "thresholdPercentages" in rating_data:
                data["price_rating"][home_id]["thresholds"] = rating_data["thresholdPercentages"]

            # Store period data (hourly, daily, monthly)
            if period_type in rating_data:
                data["price_rating"][home_id][period_type] = rating_data[period_type]

    def _get_next_entity_update_time(self, now: datetime) -> datetime:
        """Get next entity update time aligned to quarter-hour intervals."""
        minutes_now = now.minute

        # Find the next standard minute
        next_minute = None
        for std_minute in ENTITY_UPDATE_MINUTES:
            if std_minute > minutes_now:
                next_minute = std_minute
                break

        if next_minute is None:
            # If we're past all standard minutes, go to next hour
            next_minute = ENTITY_UPDATE_MINUTES[0]
            next_hour = now.hour + 1
        else:
            next_hour = now.hour

        # Create target time
        return self._create_update_time(now, next_hour, next_minute)

    def _create_update_time(self, now: datetime, hour: int, minute: int) -> datetime:
        """Create update time with proper handling of day rollover."""
        next_update = now.replace(
            hour=hour % HOURS_IN_DAY,
            minute=minute,
            second=0,
            microsecond=0,
        )

        # If we rolled over to next day, adjust the date
        if hour >= HOURS_IN_DAY:
            next_update = next_update + timedelta(days=1)

        return next_update

    @callback
    def _schedule_next_entity_update(self) -> None:
        """Schedule the next entity update aligned to quarter-hour intervals."""
        if self._scheduled_update_task:
            self._scheduled_update_task.cancel()

        now = dt_util.now()
        next_update = self._get_next_entity_update_time(now)

        # Calculate seconds until next update
        delay = (next_update - now).total_seconds()

        # Clarify in the log whether this is for API fetching or just entity recalculation
        will_fetch = self._should_fetch_data()
        api_state = self.current_api_state

        # More descriptive log message
        action_type = "API update" if will_fetch else "entity recalculation"

        time_status = "before 13:00"
        if now.time() >= TOMORROW_DATA_CHECK_START:
            time_status = "13:00-15:00" if now.time() < INTENSIVE_SEARCH_START else "after 15:00"

        data_status = "have tomorrow data" if self._tomorrow_data_available else "need tomorrow data"

        self.logger.debug(
            "Scheduling next %s at %s (API state: %s, time: %s, %s)",
            action_type,
            next_update.isoformat(),
            api_state.value,
            time_status,
            data_status,
        )

        # Schedule the update
        self._scheduled_update_task = asyncio.create_task(self._handle_scheduled_update(delay))

    async def _handle_scheduled_update(self, delay: float) -> None:
        """Handle the scheduled update after the specified delay."""
        await asyncio.sleep(delay)
        # This will call async_refresh() which then calls the parent's implementation
        # which will in turn call our _async_update_data()
        await self.async_refresh()

    def cancel_scheduled_updates(self) -> None:
        """Cancel any scheduled update tasks."""
        if self._scheduled_update_task is not None:
            self._scheduled_update_task.cancel()
            self._scheduled_update_task = None

    @callback
    def async_handle_midnight_transition(self) -> None:
        """Handle data rotation at midnight."""
        # This is called from __init__.py at midnight
        self.logger.info("====== MIDNIGHT TRANSITION DETECTED ======")

        # Perform the midnight rotation using shared implementation
        self._perform_midnight_rotation()

        # Save the updated data to persistent storage
        save_task = asyncio.create_task(self._save_cached_data())
        save_task.add_done_callback(lambda _: None)

        # Force a refresh to get updated data
        self.logger.info("Scheduling refresh to fetch new tomorrow's data")
        refresh_task = asyncio.create_task(self.async_refresh())
        refresh_task.add_done_callback(lambda _: None)

    async def _check_and_handle_missed_midnight_transition(self) -> None:
        """
        Check if a midnight transition was missed and handle it if needed.

        This is called during initialization to ensure data is properly rotated
        when Home Assistant wasn't running at midnight.
        """
        now = dt_util.now()
        current_date = now.date()

        # If we don't have price info data yet, nothing to rotate
        if not self._data_cache.get("price_info"):
            return

        # Use the helper to check for missed midnight transitions
        midnight_check = check_for_missed_midnight_transition(self._data_cache["price_info"], current_date, self.logger)

        # Handle midnight rotation if needed
        if midnight_check["needs_rotation"]:
            self.logger.warning(
                "====== MISSED MIDNIGHT TRANSITION DETECTED ======\n%d/%d homes have outdated 'today' data from %s",
                midnight_check["outdated_homes"],
                midnight_check["total_homes"],
                (
                    f"multiple days ago (avg: {midnight_check['avg_days_old']:.1f} days)"
                    if midnight_check["severely_outdated"]
                    else "previous day"
                ),
            )

            # Perform the midnight rotation using the helper
            perform_midnight_rotation(self._data_cache, self.logger)

            # Reset tomorrow data availability flag
            self._tomorrow_data_available = False

            # Save rotated data to storage
            await self._save_cached_data()
            self.logger.info("Completed missed midnight data rotation during initialization")

            # Force an immediate refresh if data is severely outdated
            if midnight_check["severely_outdated"]:
                self.logger.warning(
                    "Data is severely outdated (avg: %.1f days old). Forcing immediate refresh to get current data.",
                    midnight_check["avg_days_old"],
                )
                # Schedule immediate refresh task
                refresh_task = asyncio.create_task(self.async_refresh())
                refresh_task.add_done_callback(lambda _: None)
        else:
            # Run validations on the cache
            await self._validate_cache_data()

    async def _validate_cache_data(self) -> None:
        """Validate cache data for structural issues and missing current hour."""
        # Structure validation first
        structure_result = validate_cache_structure(self._data_cache)
        if not structure_result["valid"]:
            self.logger.warning(
                "Cache structure validation detected issues: %s", ", ".join(structure_result["structural_issues"])
            )
            if structure_result["needs_full_refresh"]:
                self.logger.info("Scheduling immediate refresh to fix structural issues")
                refresh_task = asyncio.create_task(self.async_refresh())
                refresh_task.add_done_callback(lambda _: None)
                return

        # Then check for missing current hour data
        await check_for_missing_current_hour(self.logger, self._data_cache, self.async_refresh, self._last_full_update)
        self.logger.debug("Completed cache data validation")

    def _perform_midnight_rotation(self) -> None:
        """
        Perform the midnight data rotation (move tomorrow to today).

        This is a synchronous version of the data rotation logic that can be
        called from both async_handle_midnight_transition and the initialization check.
        """
        # Use the helper to perform the rotation
        perform_midnight_rotation(self._data_cache, self.logger)

        # Reset tomorrow data availability flag
        self._tomorrow_data_available = False

    def _get_api_client(self) -> TibberPricesApiClient | None:
        """Get the API client from hass.data."""
        if DOMAIN in self.hass.data and self.config_entry.entry_id in self.hass.data[DOMAIN]:
            runtime_data = self.hass.data[DOMAIN][self.config_entry.entry_id]
            return runtime_data.client
        return None

    def _log_update_status(self) -> None:
        """Log current state and cache status."""
        state = self.current_api_state
        now = dt_util.now()

        # Create a more informative status message based on time and data
        time_window = "before 13:00"
        if now.time() >= TOMORROW_DATA_CHECK_START:
            time_window = "13:00-15:00" if now.time() < INTENSIVE_SEARCH_START else "15:00-00:00"

        status_parts = []
        if self._tomorrow_data_available:
            status_parts.append("tomorrow data available")
        elif time_window == "before 13:00":
            status_parts.append("too early for tomorrow data")
        else:
            status_parts.append("waiting for tomorrow data")

        # Add information about last update
        if self._last_full_update:
            minutes_ago = (now - self._last_full_update).total_seconds() / 60
            status_parts.append(f"last update {minutes_ago:.1f} min ago")

        # Detailed log at debug level
        self.logger.debug(
            "STATUS: API state: %s (%s, time window: %s)",
            state.value,
            ", ".join(status_parts),
            time_window,
        )

        # Check if basic data exists - log at INFO level for missing data
        if not self._data_cache.get("user_info") or not self._data_cache.get("homes"):
            self.logger.info("CACHE STATUS: Missing basic user data, initialization needed")
        elif self._is_missing_today_data():
            self.logger.info("CACHE STATUS: Missing today's price data, fetch required")
        else:
            self.logger.debug("CACHE STATUS: Basic data and today's prices available")

    async def _fetch_basic_data(self, client: TibberPricesApiClient, data: dict[str, Any]) -> None:
        """Fetch basic user and home data if needed."""
        if not data.get("user_info") or not data.get("homes"):
            self.logger.info("INITIALIZATION PHASE: Fetching user info and homes from API")
            start_time = dt_util.now()
            user_data = await client.async_get_user_info()
            if "viewer" in user_data:
                data["user_info"] = user_data["viewer"]
                if "homes" in data["user_info"]:
                    homes_count = len(data["user_info"]["homes"])
                    data["homes"] = {home["id"]: home for home in data["user_info"]["homes"]}
                    duration = (dt_util.now() - start_time).total_seconds()
                    self.logger.info(
                        "Initial user data loaded: %d homes from API, account name: %s (in %.3f seconds)",
                        homes_count,
                        data["user_info"].get("name", "unknown"),
                        duration,
                    )

    def _log_state_transition(self, previous_state: ApiState) -> None:
        """Log state transition if it changed."""
        updated_state = self.current_api_state
        if updated_state != previous_state:
            self.logger.info(
                "API STATE TRANSITION: %s → %s after data update",
                previous_state.value,
                updated_state.value,
            )

    def _log_data_summary(self, data: dict[str, Any]) -> None:
        """Log a summary of what data was fetched."""
        # Create sections for different data types
        sections = []

        # User info and homes section
        if data.get("user_info"):
            sections.append("user info")

        if data.get("homes"):
            home_count = len(data["homes"])
            sections.append(f"{home_count} homes")

        # Price info section with details
        if data.get("price_info"):
            homes_with_today = sum(1 for info in data["price_info"].values() if info.get("today"))
            homes_with_tomorrow = sum(1 for info in data["price_info"].values() if info.get("tomorrow"))

            # More detailed info for price data
            price_points_today = sum(len(info.get("today", [])) for info in data["price_info"].values())
            price_points_tomorrow = sum(len(info.get("tomorrow", [])) for info in data["price_info"].values())

            sections.append(
                f"prices (homes with today: {homes_with_today}, tomorrow: {homes_with_tomorrow}, "
                f"points today: {price_points_today}, tomorrow: {price_points_tomorrow})"
            )

        # Ratings section
        if data.get("price_rating"):
            ratings = []
            if any("hourly" in rating for rating in data["price_rating"].values()):
                ratings.append("hourly")
            if any("daily" in rating for rating in data["price_rating"].values()):
                ratings.append("daily")
            if any("monthly" in rating for rating in data["price_rating"].values()):
                ratings.append("monthly")
            sections.append(f"ratings ({', '.join(ratings)})")

        # Log the full data summary
        if data:
            self.logger.info("DATA UPDATE SUMMARY: %s", ", ".join(sections))
        else:
            self.logger.warning("No data received from API")
