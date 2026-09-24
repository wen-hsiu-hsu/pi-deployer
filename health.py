"""Health check logic for pi-deployer."""

import logging
import time

import requests

logger = logging.getLogger("pi-deployer")


def run_health_check(url, retries=3, interval=5, initial_delay=0):
    """Check if a URL responds with 2xx, with retries.

    Args:
        url: URL to check.
        retries: Number of attempts.
        interval: Seconds between retries.
        initial_delay: Seconds to wait before the first attempt, to give the
            container time to start listening before probing begins.

    Returns:
        True if healthy, False otherwise.
    """
    if initial_delay:
        time.sleep(initial_delay)

    for attempt in range(1, retries + 1):
        try:
            resp = requests.get(url, timeout=10)
            if 200 <= resp.status_code < 300:
                logger.info("Health check passed: %s (attempt %d)", url, attempt)
                return True
            logger.warning(
                "Health check %s returned %d (attempt %d/%d)",
                url, resp.status_code, attempt, retries,
            )
        except requests.RequestException as e:
            logger.warning(
                "Health check %s failed (attempt %d/%d): %s",
                url, attempt, retries, e,
            )

        if attempt < retries:
            time.sleep(interval)

    logger.error("Health check failed after %d attempts: %s", retries, url)
    return False
