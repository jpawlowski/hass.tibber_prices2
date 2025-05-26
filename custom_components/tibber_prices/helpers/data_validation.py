"""Helper module for data validation in Tibber Prices integration."""

from dataclasses import dataclass
from datetime import date, datetime, timedelta
from typing import Any

from homeassistant.util import dt as dt_util

# Constants for magic numbers
HOURS_IN_DAY = 24
SPRING_FORWARD_HOURS = 23
FALL_BACK_HOURS = 25
DUPLICATE_HOUR_COUNT = 2
SIGNIFICANT_DAYS_OLD = 1


@dataclass
class ValidationContext:
    """Context data for validation operations."""

    current_date: date
    current_hour: int
    now: datetime
    logger: Any


def validate_price_data(
    price_info: dict[str, Any],
    current_date: date,
    current_hour: int,
    now: datetime,
    logger: Any,
) -> dict[str, Any]:
    """
    Validate price data for all homes.

    Args:
        price_info: The price info data from the cache
        current_date: The current date
        current_hour: The current hour
        now: The current datetime
        logger: Logger instance

    Returns:
        Dictionary with validation results

    """
    result = {"valid": True, "total_homes": len(price_info), "homes_with_issues": 0, "issues": []}
    context = ValidationContext(current_date, current_hour, now, logger)

    # Check each home's data for the current hour
    for home_id, home_price_info in price_info.items():
        home_result = validate_home_price_data(home_id, home_price_info, context)

        if not home_result["valid"]:
            result["valid"] = False
            result["homes_with_issues"] += 1
            result["issues"].extend(home_result["issues"])

    return result


def validate_home_price_data(
    home_id: str,
    price_info: dict[str, Any],
    context: ValidationContext,
) -> dict[str, Any]:
    """
    Validate price data for a single home.

    Args:
        home_id: The home ID
        price_info: The price info data for this home
        context: Validation context with date, time and logger

    Returns:
        Dictionary with validation results for this home

    """
    result = {"valid": True, "issues": []}
    current_date = context.current_date
    logger = context.logger

    # Step 1: Check if today data exists
    if not price_info.get("today"):
        # No today data
        result["valid"] = False
        result["issues"].append(f"Home {home_id} has no 'today' data at all")
        return result

    try:
        # Step 2: Validate basic data structure
        valid_structure = _validate_home_data_structure(home_id, price_info, logger)
        if not valid_structure["valid"]:
            result["valid"] = False
            result["issues"].extend(valid_structure["issues"])
            return result

        # Step 3: Validate the date of the data
        valid_date = _validate_home_data_date(home_id, price_info, current_date, logger)
        if not valid_date["valid"]:
            result["valid"] = False
            result["issues"].extend(valid_date["issues"])
            return result

        # Step 4: Check for current hour data
        current_hour_result = validate_current_hour_data(home_id, price_info["today"], context)

        if not current_hour_result["valid"]:
            result["valid"] = False
            result["issues"].extend(current_hour_result["issues"])

    except (IndexError, KeyError, ValueError, TypeError) as err:
        logger.warning("Error checking price data for home %s: %s", home_id, err)
        result["valid"] = False
        result["issues"].append(f"Home {home_id} error: {err!s}")

    return result


def _validate_home_data_structure(
    home_id: str,
    price_info: dict[str, Any],
    logger: Any,
) -> dict[str, Any]:
    """
    Validate the structure of a home's price data.

    Args:
        home_id: The home ID
        price_info: The price info data for this home
        logger: Logger instance

    Returns:
        Dictionary with validation results

    """
    result = {"valid": True, "issues": []}

    # Check if today is a list
    if not isinstance(price_info["today"], list):
        logger.warning("Invalid data structure: 'today' is not a list for home %s", home_id)
        result["valid"] = False
        result["issues"].append(f"Home {home_id} has invalid data structure")
        return result

    # Check if today is empty
    if not price_info["today"]:
        logger.warning("Empty price data: 'today' list is empty for home %s", home_id)
        result["valid"] = False
        result["issues"].append(f"Home {home_id} has empty 'today' data")
        return result

    # Check price structure
    first_price = price_info["today"][0]
    if not isinstance(first_price, dict) or "startsAt" not in first_price:
        logger.warning("Invalid price data structure for home %s", home_id)
        result["valid"] = False
        result["issues"].append(f"Home {home_id} has invalid price data structure")
        return result

    return result


def _validate_home_data_date(
    home_id: str,
    price_info: dict[str, Any],
    current_date: date,
    logger: Any,
) -> dict[str, Any]:
    """
    Validate the date of a home's price data.

    Args:
        home_id: The home ID
        price_info: The price info data for this home
        current_date: The current date
        logger: Logger instance

    Returns:
        Dictionary with validation results

    """
    result = {"valid": True, "issues": []}

    # Check first price date
    first_price = price_info["today"][0]
    starts_at = dt_util.parse_datetime(first_price["startsAt"])

    if not starts_at:
        logger.warning("Invalid date format in price data for home %s", home_id)
        result["valid"] = False
        result["issues"].append(f"Home {home_id} has invalid date format")
        return result

    date_difference = (current_date - starts_at.date()).days

    # Check if data is from today
    if date_difference > 0:
        # Data is from a previous day
        logger.warning("Home %s has outdated price data from %d day(s) ago", home_id, date_difference)
        result["valid"] = False
        result["issues"].append(f"Home {home_id} has outdated data from {date_difference} day(s) ago")
    elif date_difference < 0:
        # Data is from a future day (shouldn't happen, but handle it)
        logger.warning("Home %s has unexpected future price data", home_id)
        result["valid"] = False
        result["issues"].append(f"Home {home_id} has unexpected future data")

    return result


def validate_current_hour_data(
    home_id: str,
    today_prices: list[dict[str, Any]],
    context: ValidationContext,
) -> dict[str, Any]:
    """
    Validate price data for the current hour and completeness of the day.

    Args:
        home_id: The home ID
        today_prices: List of price data for today
        context: Validation context with date, time and logger

    Returns:
        Dictionary with validation results

    """
    result = {"valid": True, "issues": []}

    # Extract validation context
    current_date = context.current_date
    current_hour = context.current_hour
    now = context.now
    logger = context.logger

    # Check for DST transition
    is_dst_day = is_dst_transition_day(now)

    # Check for current hour
    has_current_hour = False
    for price in today_prices:
        if "startsAt" not in price:
            continue

        price_time = dt_util.parse_datetime(price["startsAt"])
        if not price_time:
            continue

        if price_time.date() == current_date and price_time.hour == current_hour:
            # Found the current hour data
            has_current_hour = True

            # Validate price data
            if "total" not in price or not isinstance(price["total"], (int, float)):
                logger.warning("Home %s has invalid price data for current hour (%d:00)", home_id, current_hour)
                result["valid"] = False
                result["issues"].append(f"Home {home_id} has corrupt price data for hour {current_hour}")
                return result

            # All checks passed for this hour
            break

    if not has_current_hour:
        result["valid"] = False
        result["issues"].append(f"Home {home_id} is missing current hour ({current_hour}:00) data")
        return result

    # Check for day completeness
    is_dst_day = is_dst_transition_day(now)

    # We'll use the existing validate_day_completeness function which expects a dict
    validation_dict = {
        "current_date": current_date,
        "current_hour": current_hour,
        "is_dst_day": is_dst_day,
        "now": now,
        "logger": logger,
    }
    day_completeness = validate_day_completeness(home_id, today_prices, validation_dict)

    if not day_completeness["valid"]:
        result["valid"] = False
        result["issues"].extend(day_completeness["issues"])

    return result


def validate_day_completeness(
    home_id: str,
    today_prices: list[dict[str, Any]],
    validation_context: dict[str, Any],
) -> dict[str, Any]:
    """
    Validate completeness of the day's price data.

    Args:
        home_id: The home ID
        today_prices: List of price data for today
        validation_context: Dictionary with validation context (current_date, current_hour, is_dst_day, now, logger)

    Returns:
        Dictionary with validation results

    """
    result = {"valid": True, "issues": []}

    # Extract validation context
    current_date = validation_context["current_date"]
    current_hour = validation_context["current_hour"]
    is_dst_day = validation_context["is_dst_day"]
    now = validation_context["now"]
    logger = validation_context["logger"]

    expected_hours = 24  # Default expectation is 24 hours

    # If this is a DST transition day, adjust expectations
    if is_dst_day:
        expected_hours = 23 if is_spring_forward(now) else 25

    # Count actual hours in today's data
    unique_hours = set()
    for price in today_prices:
        price_time = dt_util.parse_datetime(price.get("startsAt", ""))
        if price_time and price_time.date() == current_date:
            unique_hours.add(price_time.hour)

    # Check if we have fewer hours than expected
    if len(unique_hours) < expected_hours:
        logger.warning(
            "Home %s has incomplete data for today (%d/%d hours)", home_id, len(unique_hours), expected_hours
        )

        if current_hour > max(unique_hours or [0]):
            # If we're past the last hour in the data, refresh
            result["valid"] = False
            result["issues"].append(
                f"Home {home_id} has incomplete day data ({len(unique_hours)}/{expected_hours} hours)"
            )

    # For DST transition days, check if the data structure looks correct
    if is_dst_day:
        dst_validation = validate_dst_transition_data(today_prices, now, logger, home_id)

        if not dst_validation["valid"]:
            result["valid"] = False
            result["issues"].extend(dst_validation["issues"])

    return result


def is_dst_transition_day(now: datetime) -> bool:
    """Check if today is a DST transition day."""
    # Get yesterday and tomorrow at the same hour
    yesterday = now - timedelta(days=1)
    tomorrow = now + timedelta(days=1)

    # If the UTC offset changed between yesterday and today or
    # will change between today and tomorrow, it's a transition day
    today_offset = now.utcoffset()
    yesterday_offset = yesterday.utcoffset()
    tomorrow_offset = tomorrow.utcoffset()

    return bool((today_offset != yesterday_offset) or (today_offset != tomorrow_offset))


def is_spring_forward(now: datetime) -> bool:
    """Check if today is a spring-forward day (lose an hour)."""
    yesterday = now - timedelta(days=1)

    # Get the UTC offsets
    today_offset = now.utcoffset()
    yesterday_offset = yesterday.utcoffset()

    # If today's offset is greater than yesterday's, we sprung forward
    if today_offset and yesterday_offset:
        return today_offset > yesterday_offset
    return False


def validate_dst_transition_data(
    prices: list[dict[str, Any]],
    now: datetime,
    logger: Any,
    home_id: str,
) -> dict[str, Any]:
    """
    Validate price data during DST transitions.

    Args:
        prices: List of price data
        now: Current datetime
        logger: Logger instance
        home_id: Home ID for logging

    Returns:
        Dictionary with validation results

    """
    result = {"valid": True, "issues": []}

    # Sort prices by start time
    def get_datetime(price: dict[str, Any]) -> datetime:
        """Parse datetime from price data."""
        dt = dt_util.parse_datetime(price.get("startsAt", ""))
        return dt if dt else dt_util.utcnow().replace(1970, 1, 1)

    sorted_prices = sorted(prices, key=get_datetime)

    # Count hours for today
    today_date = now.date()
    hours_count = 0

    # Keep track of hour frequency
    hour_frequency = {}

    for price in sorted_prices:
        starts_at = dt_util.parse_datetime(price.get("startsAt", ""))
        if not starts_at or starts_at.date() != today_date:
            continue

        hours_count += 1

        # Count frequency of each hour
        hour = starts_at.hour
        hour_frequency[hour] = hour_frequency.get(hour, 0) + 1

    # Check if spring forward (expect 23 hours, no duplicates)
    if is_spring_forward(now):
        if hours_count != SPRING_FORWARD_HOURS:
            logger.warning(
                "DST spring forward: Home %s expected %d hours, found %d",
                home_id,
                SPRING_FORWARD_HOURS,
                hours_count,
            )
            result["valid"] = False
            result["issues"].append(
                f"Home {home_id} has incorrect hour count for DST spring forward: {hours_count}/{SPRING_FORWARD_HOURS}"
            )

        # Check for duplicates (shouldn't have any)
        duplicate_hours = [h for h, freq in hour_frequency.items() if freq > 1]
        if duplicate_hours:
            logger.warning("DST spring forward: Home %s has unexpected duplicate hours: %s", home_id, duplicate_hours)
            result["valid"] = False
            result["issues"].append(
                f"Home {home_id} has unexpected duplicate hours during DST spring forward: {duplicate_hours}"
            )
    else:
        # Fall back (expect 25 hours with one duplicate hour)
        if hours_count != FALL_BACK_HOURS:
            logger.warning(
                "DST fall back: Home %s expected %d hours, found %d",
                home_id,
                FALL_BACK_HOURS,
                hours_count,
            )
            result["valid"] = False
            result["issues"].append(
                f"Home {home_id} has incorrect hour count for DST fall back: {hours_count}/{FALL_BACK_HOURS}"
            )

        # Check for exactly one duplicate hour
        duplicate_hours = [h for h, freq in hour_frequency.items() if freq > 1]
        expected_duplicates = hour_frequency.get(duplicate_hours[0] if duplicate_hours else 0, 0)
        if len(duplicate_hours) != 1 or expected_duplicates != DUPLICATE_HOUR_COUNT:
            logger.warning(
                "DST fall back: Home %s expected exactly one duplicate hour, found: %s", home_id, duplicate_hours
            )
            result["valid"] = False
            result["issues"].append(
                f"Home {home_id} has incorrect duplicate hours during DST fall back: {duplicate_hours}"
            )

    return result
