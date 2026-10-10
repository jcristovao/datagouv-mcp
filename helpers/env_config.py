import os
from urllib.parse import urlsplit

_ENV_TARGETS = {
    "demo": {
        "datagouv_api": "https://demo.data.gouv.fr/api/",
        "site": "https://demo.data.gouv.fr/",
        "tabular_api": "https://tabular-api.preprod.data.gouv.fr/api/",
        "metrics_api": "https://metric-api.data.gouv.fr/api/",  # No demo/preprod for Metrics API
        "crawler_api": "https://demo-crawler.data.gouv.fr/api/",
    },
    "prod": {
        "datagouv_api": "https://www.data.gouv.fr/api/",
        "site": "https://www.data.gouv.fr/",
        "tabular_api": "https://tabular-api.data.gouv.fr/api/",
        "metrics_api": "https://metric-api.data.gouv.fr/api/",
        "crawler_api": "https://crawler.data.gouv.fr/api/",
    },
}

DEFAULT_PORTAL_NAME = "data.gouv.fr"

# Environment variables for pointing the server at another udata instance.
# Setting UDATA_API_URL selects the "custom" target and takes precedence over
# DATAGOUV_API_ENV. Satellite services (Tabular, Metrics, Crawler) are only
# available on a custom instance when their URL is set explicitly.
_CUSTOM_ENV_VARS = {
    "datagouv_api": "UDATA_API_URL",
    "site": "UDATA_SITE_URL",
    "tabular_api": "TABULAR_API_URL",
    "metrics_api": "METRICS_API_URL",
    "crawler_api": "CRAWLER_API_URL",
}


class ServiceUnavailableError(KeyError):
    """Raised when an API is not configured for the current udata instance."""


def _env_url(name: str) -> str | None:
    value = os.getenv(name, "").strip()
    if not value:
        return None
    return value if value.endswith("/") else f"{value}/"


def _default_site_url(api_url: str) -> str:
    """Derive the site URL from the API URL (https://host/api/ -> https://host/)."""
    if api_url.endswith("/api/"):
        return api_url[: -len("api/")]
    parts = urlsplit(api_url)
    return f"{parts.scheme}://{parts.netloc}/"


def get_env_name() -> str:
    """
    Get the name of the current environment target.

    Returns "custom" when UDATA_API_URL is set, otherwise the value of
    DATAGOUV_API_ENV (demo|prod), defaulting to prod if not set or invalid.
    """
    if _env_url("UDATA_API_URL"):
        return "custom"
    env_name: str = os.getenv("DATAGOUV_API_ENV", "prod").strip().lower()
    if env_name not in _ENV_TARGETS:
        env_name = "prod"
    return env_name


def _get_config() -> dict[str, str | None]:
    env_name = get_env_name()
    if env_name != "custom":
        return dict(_ENV_TARGETS[env_name])
    config = {
        api_name: _env_url(var_name) for api_name, var_name in _CUSTOM_ENV_VARS.items()
    }
    api_url = config["datagouv_api"]
    assert api_url is not None
    if config["site"] is None:
        config["site"] = _default_site_url(api_url)
    return config


def is_available(api_name: str) -> bool:
    """
    Check whether an API is configured for the current environment.

    Always True for data.gouv.fr (demo|prod). On a custom udata instance,
    satellite services are only available when their URL is set.

    Raises:
        KeyError: If api_name is not a valid API name.
    """
    config = _get_config()
    if api_name not in config:
        raise KeyError(
            f"Invalid api_name: {api_name}. "
            f"Valid values are: {', '.join(config.keys())}"
        )
    return config[api_name] is not None


def get_base_url(api_name: str) -> str:
    """
    Get the base URL for a specific API in the current environment.

    Reads DATAGOUV_API_ENV environment variable (demo|prod). Defaults to prod if not set or invalid.
    If UDATA_API_URL is set, URLs come from the UDATA_* / *_API_URL variables instead.

    Args:
        api_name: API name to get the endpoint for.
                  Valid values: "datagouv_api", "site", "tabular_api", "metrics_api", "crawler_api"

    Returns:
        The API endpoint URL as a string.

    Raises:
        KeyError: If api_name is not a valid API name.
        ServiceUnavailableError: If the API is not configured for this instance.
    """
    config = _get_config()
    if api_name not in config:
        raise KeyError(
            f"Invalid api_name: {api_name}. "
            f"Valid values are: {', '.join(config.keys())}"
        )
    url = config[api_name]
    if url is None:
        raise ServiceUnavailableError(
            f"{api_name} is not available on {get_portal_name()}. "
            f"Set {_CUSTOM_ENV_VARS[api_name]} to enable it."
        )
    return url


def get_portal_name() -> str:
    """
    Get the human-readable name of the udata portal (used in tool descriptions).

    "data.gouv.fr" by default. On a custom instance, UDATA_PORTAL_NAME if set,
    otherwise the host name of the site URL.
    """
    if get_env_name() != "custom":
        return DEFAULT_PORTAL_NAME
    name = os.getenv("UDATA_PORTAL_NAME", "").strip()
    if name:
        return name
    host = urlsplit(get_base_url("site")).hostname or ""
    return host.removeprefix("www.")
