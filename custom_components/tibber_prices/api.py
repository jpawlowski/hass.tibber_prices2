"""Tibber API Client for the tibber_prices integration."""

from __future__ import annotations

import asyncio
import socket
from typing import Any

import aiohttp
import async_timeout

from .const import (
    DEFAULT_TIMEOUT,
    HTTP_RATE_LIMIT_TOO_MANY_REQUESTS,
    LOGGER,
    MAX_RETRIES,
    RETRY_DELAY,
    TIBBER_API_URL,
)


class TibberPricesApiClientError(Exception):
    """Exception to indicate a general API error."""


class TibberPricesApiClientCommunicationError(TibberPricesApiClientError):
    """Exception to indicate a communication error."""


class TibberPricesApiClientAuthenticationError(TibberPricesApiClientError):
    """Exception to indicate an authentication error."""


class TibberPricesApiClientRateLimitError(TibberPricesApiClientError):
    """Exception to indicate a rate limit error."""


def _verify_response_or_raise(response: aiohttp.ClientResponse) -> None:
    """Verify that the response is valid."""
    if response.status in (401, 403):
        msg = "Invalid access token or unauthorized access"
        raise TibberPricesApiClientAuthenticationError(msg)

    if response.status == HTTP_RATE_LIMIT_TOO_MANY_REQUESTS:
        msg = "Rate limit exceeded"
        raise TibberPricesApiClientRateLimitError(msg)

    response.raise_for_status()


class TibberPricesApiClient:
    """Tibber API Client for the tibber_prices integration."""

    def __init__(
        self,
        access_token: str,
        session: aiohttp.ClientSession,
    ) -> None:
        """
        Initialize the Tibber API client.

        Args:
            access_token: The Tibber API access token
            session: The aiohttp client session

        """
        self._access_token = access_token
        self._session = session
        self._headers = {
            "Authorization": f"Bearer {access_token}",
            "Content-Type": "application/json",
        }

    async def async_get_user_info(self) -> dict[str, Any]:
        """
        Get user information and list of homes.

        Returns:
            Dict containing user information and homes

        """
        query = """
            {
                viewer {
                    userId
                    name
                    login
                    homes {
                        id
                        type
                        appNickname
                        address {
                            address1
                            postalCode
                            city
                            country
                        }
                    }
                }
            }
        """
        return await self._execute_graphql_query(query)

    async def async_get_price_info(self) -> dict[str, Any]:
        """
        Get price info data for all homes.

        Returns:
            Dict containing price information for all homes

        """
        query = """
            {viewer{homes{id,currentSubscription{priceInfo{
                range(resolution:HOURLY,last:48){edges{node{
                    startsAt total energy tax level
                }}}
                today{startsAt total energy tax level}
                tomorrow{startsAt total energy tax level}
            }}}}}
        """
        return await self._execute_graphql_query(query)

    async def async_get_daily_price_rating(self) -> dict[str, Any]:
        """
        Get daily price rating for all homes.

        Returns:
            Dict containing daily price rating for all homes

        """
        query = """
            {viewer{homes{id,currentSubscription{priceRating{
                thresholdPercentages{low high}
                daily{
                    currency
                    entries{time total energy tax difference level}
                }
            }}}}}
        """
        return await self._execute_graphql_query(query)

    async def async_get_hourly_price_rating(self) -> dict[str, Any]:
        """
        Get hourly price rating for all homes.

        Returns:
            Dict containing hourly price rating for all homes

        """
        query = """
            {viewer{homes{id,currentSubscription{priceRating{
                thresholdPercentages{low high}
                hourly{
                    currency
                    entries{time total energy tax difference level}
                }
            }}}}}
        """
        return await self._execute_graphql_query(query)

    async def async_get_monthly_price_rating(self) -> dict[str, Any]:
        """
        Get monthly price rating for all homes.

        Returns:
            Dict containing monthly price rating for all homes

        """
        query = """
            {viewer{homes{id,currentSubscription{priceRating{
                thresholdPercentages{low high}
                monthly{
                    currency
                    entries{time total energy tax difference level}
                }
            }}}}}
        """
        return await self._execute_graphql_query(query)

    async def _execute_graphql_query(self, query: str, variables: dict[str, Any] | None = None) -> dict[str, Any]:
        """
        Execute a GraphQL query with retry logic.

        Args:
            query: The GraphQL query to execute
            variables: Optional variables for the GraphQL query

        Returns:
            Dict containing the GraphQL query response data

        Raises:
            TibberPricesApiClientError: If the query fails after all retries

        """
        # Extract a more meaningful identifier from the query for logging
        query_type = "GraphQL query"
        query_single_line = " ".join(query.split())

        # Check for specific patterns to identify query types
        if "priceInfo" in query_single_line:
            query_type = "GraphQL price info query"
        elif "priceRating" in query_single_line:
            if ("daily" in query_single_line
                and "hourly" not in query_single_line
                and "monthly" not in query_single_line):
                query_type = "GraphQL daily price rating query"
            elif "hourly" in query_single_line and "daily" not in query_single_line:
                query_type = "GraphQL hourly price rating query"
            elif "monthly" in query_single_line and "daily" not in query_single_line:
                query_type = "GraphQL monthly price rating query"
            else:
                query_type = "GraphQL price rating query"
        elif "userId" in query_single_line and "homes" in query_single_line:
            query_type = "GraphQL user info query"

        LOGGER.debug("Executing %s", query_type)

        data: dict[str, Any] = {"query": query}
        if variables:
            data["variables"] = variables

        retry_count = 0
        last_exception = None

        while retry_count < MAX_RETRIES:
            try:
                response_data = await self._api_wrapper(
                    method="post",
                    url=TIBBER_API_URL,
                    data=data,
                    headers=self._headers,
                )

                if "errors" in response_data:
                    error_message = response_data["errors"][0].get("message", "Unknown GraphQL error")
                    LOGGER.error("GraphQL query error: %s", error_message)
                    msg = f"GraphQL query error: {error_message}"
                    raise TibberPricesApiClientError(msg)

                return response_data.get("data", {})

            except TibberPricesApiClientRateLimitError as exception:
                # For rate limit errors, use exponential backoff
                wait_time = RETRY_DELAY * (2**retry_count)
                LOGGER.warning(
                    "Rate limit exceeded, retrying in %s seconds (attempt %s/%s)",
                    wait_time,
                    retry_count + 1,
                    MAX_RETRIES,
                )
                await asyncio.sleep(wait_time)
                last_exception = exception

            except TibberPricesApiClientCommunicationError as exception:
                # For network errors, retry with linear backoff
                wait_time = RETRY_DELAY * (retry_count + 1)
                LOGGER.warning(
                    "Communication error, retrying in %s seconds (attempt %s/%s): %s",
                    wait_time,
                    retry_count + 1,
                    MAX_RETRIES,
                    exception,
                )
                await asyncio.sleep(wait_time)
                last_exception = exception

            except TibberPricesApiClientAuthenticationError:
                # Don't retry authentication errors
                raise

            except (aiohttp.ClientError, socket.gaierror, TimeoutError) as exception:
                # Handle specific errors we know can happen
                LOGGER.error("Error in GraphQL query: %s", exception)
                last_exception = exception

            retry_count += 1

        # If we get here, all retries have failed
        msg = f"Failed to execute GraphQL query after {MAX_RETRIES} attempts"
        if last_exception:
            raise TibberPricesApiClientError(msg) from last_exception
        raise TibberPricesApiClientError(msg)

    async def _api_wrapper(
        self,
        method: str,
        url: str,
        data: dict[str, Any] | None = None,
        headers: dict[str, str] | None = None,
    ) -> Any:
        """
        Get information from the API with proper error handling.

        Args:
            method: The HTTP method to use
            url: The URL to request
            data: Optional data to send with the request
            headers: Optional headers to include in the request

        Returns:
            Dict containing the API response

        Raises:
            TibberPricesApiClientCommunicationError: On communication errors
            TibberPricesApiClientAuthenticationError: On authentication errors
            TibberPricesApiClientError: On unexpected errors

        """
        try:
            async with async_timeout.timeout(DEFAULT_TIMEOUT):
                response = await self._session.request(
                    method=method,
                    url=url,
                    headers=headers,
                    json=data,
                )
                _verify_response_or_raise(response)
                return await response.json()

        except TimeoutError as exception:
            msg = f"Timeout error fetching information from Tibber API - {exception}"
            raise TibberPricesApiClientCommunicationError(msg) from exception

        except (aiohttp.ClientError, socket.gaierror) as exception:
            msg = f"Error fetching information from Tibber API - {exception}"
            raise TibberPricesApiClientCommunicationError(msg) from exception

        except Exception as exception:  # pylint: disable=broad-except
            msg = f"Unexpected error while contacting Tibber API - {exception}"
            raise TibberPricesApiClientError(msg) from exception
