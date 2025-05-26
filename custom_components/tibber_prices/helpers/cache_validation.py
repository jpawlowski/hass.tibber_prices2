"""Helper module for advanced cache validation and repair in Tibber Prices integration."""

from datetime import date, datetime, timedelta
from typing import Any

from homeassistant.util import dt as dt_util

from .const import (
    API_SEVERELY_STALE_THRESHOLD_HOURS,
    API_STALE_THRESHOLD_MINUTES,
    MIN_QUARTER_HOUR_BOUNDARY_MINUTES,
)


def validate_cache_structure(
    data_cache: dict[str, Any],
) -> dict[str, Any]:
    """
    Perform deep validation of cache structure to detect corrupted data.

    Args:
        data_cache: The entire data cache from coordinator
        logger: Logger instance

    Returns:
        Dictionary with validation results and details about issues

    """
    result = {
        "valid": True,
        "structural_issues": [],
        "price_structure_issues": [],
        "data_completeness_issues": [],
        "needs_full_refresh": False,
    }

    # 1. Check basic cache structure
    if not data_cache:
        result["valid"] = False
        result["structural_issues"].append("Empty data cache")
        result["needs_full_refresh"] = True
        return result

    # 2. Check essential sections exist
    required_sections = ["user_info", "homes", "price_info"]
    missing_sections = [section for section in required_sections if section not in data_cache]

    if missing_sections:
        result["valid"] = False
        result["structural_issues"].append(f"Missing required sections: {', '.join(missing_sections)}")
        result["needs_full_refresh"] = True
        return result

    # 3. Check user_info structure
    if not isinstance(data_cache.get("user_info"), dict):
        result["valid"] = False
        result["structural_issues"].append("Invalid user_info structure - not a dictionary")
        result["needs_full_refresh"] = True

    # 4. Check homes structure
    if not isinstance(data_cache.get("homes"), dict):
        result["valid"] = False
        result["structural_issues"].append("Invalid homes structure - not a dictionary")
        result["needs_full_refresh"] = True

    # 5. Check price_info structure
    if not isinstance(data_cache.get("price_info"), dict):
        result["valid"] = False
        result["structural_issues"].append("Invalid price_info structure - not a dictionary")
        result["needs_full_refresh"] = True
        return result

    # 6. Check home_id consistency between homes and price_info
    home_ids_in_homes = set(data_cache.get("homes", {}).keys())
    home_ids_in_price_info = set(data_cache.get("price_info", {}).keys())

    missing_in_price_info = home_ids_in_homes - home_ids_in_price_info
    if missing_in_price_info:
        result["valid"] = False
        result["structural_issues"].append(f"Homes missing from price_info: {', '.join(missing_in_price_info)}")

    extra_in_price_info = home_ids_in_price_info - home_ids_in_homes
    if extra_in_price_info:
        result["valid"] = False
        result["structural_issues"].append(f"Unknown home IDs in price_info: {', '.join(extra_in_price_info)}")

    # 7. Check each home's price data structure
    for home_id, price_info in data_cache.get("price_info", {}).items():
        # Check price_info is a dictionary
        if not isinstance(price_info, dict):
            result["valid"] = False
            result["price_structure_issues"].append(
                f"Invalid price_info structure for home {home_id} - not a dictionary"
            )
            continue

        # Check today and tomorrow are lists
        for key in ["today", "tomorrow"]:
            if key in price_info and not isinstance(price_info[key], list):
                result["valid"] = False
                result["price_structure_issues"].append(f"Invalid {key} structure for home {home_id} - not a list")

    return result


# Helper functions to break down complexity of check_price_data_completeness
def _process_home_price_data(
    home_price_info: dict[str, Any],
    current_date: date,
    current_hour: int,
    now: datetime,
    all_homes_hour_counts: dict[int, int],
) -> dict[str, Any]:
    """
    Process a single home's price data and check for completeness.

    Args:
        home_id: The home ID
        home_price_info: The price data for this home
        current_date: The current date
        current_hour: The current hour
        now: The current datetime
        all_homes_hour_counts: Dictionary to count hours across all homes

    Returns:
        Dictionary with results for this home

    """
    home_result = {
        "today_complete": True,
        "has_today": "today" in home_price_info and home_price_info["today"],
        "has_tomorrow": "tomorrow" in home_price_info and home_price_info["tomorrow"],
        "missing_hours": [],
    }

    # If home has no today data, mark as incomplete
    if not home_result["has_today"]:
        home_result["today_complete"] = False
        return home_result

    # Process today's data and check completeness
    hours_found = set()
    for price in home_price_info["today"]:
        starts_at = dt_util.parse_datetime(price.get("startsAt", ""))
        if starts_at and starts_at.date() == current_date:
            hour = starts_at.hour
            hours_found.add(hour)

            # Count this hour across all homes
            if hour not in all_homes_hour_counts:
                all_homes_hour_counts[hour] = 0
            all_homes_hour_counts[hour] += 1

    # Get expected hours based on DST status
    expected_hours = _get_expected_hours(now, hours_found)

    # Check for missing hours
    missing_hours = expected_hours - hours_found
    home_result["missing_hours"] = sorted(missing_hours)

    # Check if any hours up to the current hour are missing
    current_missing = [h for h in missing_hours if h <= current_hour]
    if current_missing:
        home_result["today_complete"] = False
        home_result["current_missing"] = current_missing

    return home_result


def _get_expected_hours(now: datetime, hours_found: set[int]) -> set[int]:
    """
    Get the expected set of hours for a day, adjusting for DST transitions.

    Args:
        now: Current datetime
        hours_found: Set of hours found in the data

    Returns:
        Set of expected hours for the day

    """
    # Default expectation is 24 hours
    expected_hours = set(range(24))

    # Check for DST transition
    from .const import HOURS_IN_DAY
    from .data_validation import is_dst_transition_day, is_spring_forward

    is_dst_day = is_dst_transition_day(now)
    if is_dst_day and is_spring_forward(now):
        # Find the missing hour for spring forward
        for h in range(HOURS_IN_DAY):
            if (
                h not in hours_found
                and (h > 0 and h - 1 in hours_found)
                and (h < HOURS_IN_DAY - 1 and h + 1 in hours_found)
            ):
                # This is likely the DST transition hour
                expected_hours.remove(h)
                break

    return expected_hours


def _find_missing_hour_ranges(missing_hours: list[int]) -> list[str]:
    """
    Find ranges of missing hours for better reporting.

    Args:
        missing_hours: List of missing hour numbers

    Returns:
        List of strings representing ranges of missing hours

    """
    ranges = []
    start = None

    for h in sorted(missing_hours):
        if start is None:
            start = h
        elif h > start + 1:
            ranges.append(f"{start}" if start == h - 1 else f"{start}-{h - 1}")
            start = h

    if start is not None:
        ranges.append(f"{start}" if start == missing_hours[-1] else f"{start}-{missing_hours[-1]}")

    return ranges


def check_price_data_completeness(
    price_info: dict[str, Any],
    current_date: date,
    logger: Any,
) -> dict[str, Any]:
    """
    Check completeness of price data across all homes.

    Args:
        price_info: The price info section of the data cache
        current_date: The current date
        logger: Logger instance

    Returns:
        Dictionary with completeness check results

    """
    result = {
        "complete": True,
        "total_homes": len(price_info),
        "homes_with_incomplete_data": 0,
        "homes_with_missing_today": 0,
        "homes_with_missing_tomorrow": 0,
        "missing_hour_ranges": [],
        "needs_refresh": False,
        "details": {},
    }

    now = dt_util.now()
    current_hour = now.hour

    logger.debug("Checking price data completeness for %d homes", len(price_info))

    # For counting hours across all homes
    all_homes_hour_counts = {}

    # Check if we should expect tomorrow's data
    afternoon_time = dt_util.parse_time("13:00")
    expect_tomorrow = False
    if afternoon_time and now.time() >= afternoon_time:
        expect_tomorrow = True

    # Process each home's price data
    for home_id, home_price_info in price_info.items():
        home_result = _process_home_price_data(home_price_info, current_date, current_hour, now, all_homes_hour_counts)

        # Update overall result based on this home's data
        if not home_result["has_today"]:
            result["complete"] = False
            result["homes_with_missing_today"] += 1
            result["needs_refresh"] = True
        elif not home_result["today_complete"]:
            result["complete"] = False
            result["homes_with_incomplete_data"] += 1
            result["needs_refresh"] = True

            # Find and add missing hour ranges for reporting
            if "current_missing" in home_result:
                ranges = _find_missing_hour_ranges(home_result["current_missing"])
                if ranges:
                    result["missing_hour_ranges"].append(f"Home {home_id}: hours {', '.join(ranges)}")

        # Check for tomorrow data (only if we expect it)
        if expect_tomorrow and not home_result["has_tomorrow"]:
            result["complete"] = False
            result["homes_with_missing_tomorrow"] += 1

        # Store this home's results
        result["details"][home_id] = home_result

    # Check if we have multiple homes with missing data for the same hour
    # This could indicate a systematic issue rather than per-home problem
    critical_hours = []
    for hour, count in all_homes_hour_counts.items():
        if count < result["total_homes"] * 0.5:  # If more than half of homes missing an hour
            critical_hours.append(hour)

    if critical_hours:
        result["critical_missing_hours"] = sorted(critical_hours)
        result["needs_refresh"] = True

    return result


def check_for_stale_cache(
    last_full_update: datetime | None,
    now: datetime,
) -> dict[str, Any]:
    """
    Check if the cache is stale based on last update timestamp.

    Args:
        last_full_update: Timestamp of last full update
        now: Current datetime
        logger: Logger instance

    Returns:
        Dictionary with staleness check results

    """
    result = {"is_stale": False, "reason": None, "needs_refresh": False}

    if not last_full_update:
        result["is_stale"] = True
        result["reason"] = "No previous update timestamp"
        result["needs_refresh"] = True
        return result

    # Calculate time since last update
    time_since_update = now - last_full_update

    # Check for severely stale cache
    if time_since_update > timedelta(hours=API_SEVERELY_STALE_THRESHOLD_HOURS):
        result["is_stale"] = True
        result["reason"] = f"Cache is severely stale ({time_since_update.total_seconds() / 3600:.1f} hours old)"
        result["needs_refresh"] = True
    # Check for moderately stale cache during active hours
    elif time_since_update > timedelta(minutes=API_STALE_THRESHOLD_MINUTES):
        afternoon_time = dt_util.parse_time("13:00")
        if afternoon_time and now.time() >= afternoon_time:
            result["is_stale"] = True
            result["reason"] = (
                f"Cache is stale during active hours ({time_since_update.total_seconds() / 60:.1f} minutes old)"
            )
            result["needs_refresh"] = True
    # Check if we passed a quarter-hour boundary since last update (15, 30, 45, 00)
    elif (now.minute // 15) != (last_full_update.minute // 15):
        # Only consider it stale if we're in the first 5 minutes after a boundary
        if now.minute % 15 < MIN_QUARTER_HOUR_BOUNDARY_MINUTES:
            result["is_stale"] = True
            result["reason"] = "Passed a quarter-hour boundary since last update"
            result["needs_refresh"] = True

    return result
