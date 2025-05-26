"""Helper module for current hour data validation in Tibber Prices integration."""

import asyncio
from collections.abc import Callable
from datetime import datetime
from typing import Any

from homeassistant.util import dt as dt_util

from .cache_validation import check_for_stale_cache, check_price_data_completeness, validate_cache_structure
from .data_validation import validate_price_data


async def check_for_missing_current_hour(
    logger: Any,
    data_cache: dict[str, Any],
    refresh_method: Callable,
    last_full_update: datetime | None = None,
) -> None:
    """
    Check if cache is missing data for the current hour and refresh if needed.

    Args:
        logger: Logger instance
        data_cache: The cached data from the coordinator
        refresh_method: Async method to call to refresh data if needed
        last_full_update: When the data was last updated

    Returns:
        None

    """
    # If we don't have price info data yet, nothing to check
    if not data_cache.get("price_info"):
        logger.debug("No price info data in cache to check")
        return

    now = dt_util.now()
    current_date = now.date()
    current_hour = now.hour

    # Perform multiple levels of validation:
    # 1. First, check for structural issues
    structure_validation = validate_cache_structure(data_cache)
    if not structure_validation["valid"]:
        logger.warning(
            "Cache structure validation failed. Issues detected: %s",
            ", ".join(structure_validation["structural_issues"]),
        )

        if structure_validation["needs_full_refresh"]:
            logger.info("Scheduling immediate data refresh to fix structural issues")
            refresh_task = asyncio.create_task(refresh_method())
            refresh_task.add_done_callback(lambda _: None)
            return

    # 2. Check for staleness
    if last_full_update:
        staleness_check = check_for_stale_cache(last_full_update, now)
        if staleness_check["is_stale"]:
            logger.warning("Cache data is stale: %s", staleness_check["reason"])

            if staleness_check["needs_refresh"]:
                logger.info("Scheduling data refresh to update stale cache")
                refresh_task = asyncio.create_task(refresh_method())
                refresh_task.add_done_callback(lambda _: None)
                return

    # 3. Check data completeness
    completeness_check = check_price_data_completeness(data_cache["price_info"], current_date, logger)

    if not completeness_check["complete"]:
        logger.warning(
            "Cache completeness check failed: %d/%d homes have incomplete data",
            completeness_check["homes_with_incomplete_data"],
            completeness_check["total_homes"],
        )

        # Log missing hour ranges if available
        if completeness_check["missing_hour_ranges"]:
            logger.warning(
                "Missing hour ranges detected:\n- %s", "\n- ".join(completeness_check["missing_hour_ranges"])
            )

        if completeness_check["needs_refresh"]:
            logger.info("Scheduling data refresh to fix incomplete data")
            refresh_task = asyncio.create_task(refresh_method())
            refresh_task.add_done_callback(lambda _: None)
            return

    # 4. Traditional price data validation (most specific)
    validation_result = validate_price_data(data_cache["price_info"], current_date, current_hour, now, logger)

    if not validation_result["valid"]:
        logger.warning(
            "Cache validation failed. Issues detected: %d/%d homes have problems. Forcing refresh.",
            validation_result["homes_with_issues"],
            validation_result["total_homes"],
        )

        if validation_result["issues"]:
            logger.warning("Detected issues:\n- %s", "\n- ".join(validation_result["issues"]))

        logger.info("Scheduling immediate data refresh to fix data issues")
        refresh_task = asyncio.create_task(refresh_method())
        refresh_task.add_done_callback(lambda _: None)
    else:
        logger.debug("Current hour data validation successful - data is current")
