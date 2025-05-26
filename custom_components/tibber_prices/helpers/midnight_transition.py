"""Helper module for handling midnight transitions in Tibber Prices integration."""

from datetime import date
from typing import Any

from homeassistant.util import dt as dt_util


def check_for_missed_midnight_transition(
    price_info: dict[str, Any],
    current_date: date,
    logger: Any,
) -> dict[str, Any]:
    """
    Check if a midnight transition was missed by analyzing the cache.

    Args:
        price_info: The price info data from the cache
        current_date: The current date
        logger: Logger instance

    Returns:
        Dictionary with analysis results

    """
    result = {
        "needs_rotation": False,
        "outdated_homes": 0,
        "total_homes": len(price_info),
        "days_old_by_home": {},
        "severely_outdated": False,
        "avg_days_old": 0,
    }

    if not price_info:
        return result

    # Check each home's "today" data to see if it matches the current date
    for home_id, home_price_info in price_info.items():
        if not home_price_info.get("today"):
            # No today data, can't determine if rotation is needed
            continue

        # Check the date of the first price point for "today"
        try:
            first_price = home_price_info["today"][0]
            starts_at = dt_util.parse_datetime(first_price["startsAt"])

            if starts_at and starts_at.date() < current_date:
                # The "today" data is actually from a previous day
                result["needs_rotation"] = True
                result["outdated_homes"] += 1

                # Calculate how many days old the data is
                days_old = (current_date - starts_at.date()).days
                if days_old > 1:
                    result["days_old_by_home"][home_id] = days_old
        except (IndexError, KeyError, ValueError) as err:
            logger.warning("Error checking price data date for home %s: %s", home_id, err)

    # Calculate average days old
    if result["days_old_by_home"]:
        total_days = sum(result["days_old_by_home"].values())
        result["avg_days_old"] = total_days / len(result["days_old_by_home"])
        result["severely_outdated"] = result["avg_days_old"] > 1

    return result


def perform_midnight_rotation(
    data_cache: dict[str, Any],
    logger: Any,
) -> None:
    """
    Perform the midnight data rotation (move tomorrow to today).

    Args:
        data_cache: The data cache to modify
        logger: Logger instance

    """
    # Skip if no data cache
    if not data_cache or "price_info" not in data_cache:
        logger.warning("No data cache available for midnight rotation")
        return

    price_info_count = len(data_cache.get("price_info", {}))
    homes_with_tomorrow = sum(1 for info in data_cache.get("price_info", {}).values() if info.get("tomorrow"))

    logger.info(
        "Rotating data: Moving tomorrow's prices to today (%d/%d homes have tomorrow data)",
        homes_with_tomorrow,
        price_info_count,
    )

    # Move tomorrow's data to today
    for price_info in data_cache.get("price_info", {}).values():
        if "tomorrow" in price_info:
            price_info["today"] = price_info["tomorrow"]
            price_info["tomorrow"] = []
