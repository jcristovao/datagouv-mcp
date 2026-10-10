"""Tests for the env_config helper."""

import pytest

from helpers import env_config


class TestGetBaseUrl:
    """Tests for get_base_url function."""

    def test_get_base_url_defaults_to_prod(self, monkeypatch):
        """Test that get_base_url defaults to prod when DATAGOUV_API_ENV is not set."""
        monkeypatch.delenv("DATAGOUV_API_ENV", raising=False)
        url = env_config.get_base_url("datagouv_api")
        assert url == "https://www.data.gouv.fr/api/"

    def test_get_base_url_case_insensitive(self, monkeypatch):
        """Test that get_base_url is case insensitive for environment."""
        monkeypatch.setenv("DATAGOUV_API_ENV", "PROD")
        url = env_config.get_base_url("datagouv_api")
        assert url == "https://www.data.gouv.fr/api/"

    def test_get_base_url_invalid_defaults_to_prod(self, monkeypatch):
        """Test that invalid environment defaults to prod."""
        monkeypatch.setenv("DATAGOUV_API_ENV", "invalid")
        url = env_config.get_base_url("datagouv_api")
        assert url == "https://www.data.gouv.fr/api/"

    @pytest.mark.parametrize(
        "api_name,env,expected_url",
        [
            ("datagouv_api", "demo", "https://demo.data.gouv.fr/api/"),
            ("datagouv_api", "prod", "https://www.data.gouv.fr/api/"),
            ("site", "demo", "https://demo.data.gouv.fr/"),
            ("site", "prod", "https://www.data.gouv.fr/"),
            ("tabular_api", "demo", "https://tabular-api.preprod.data.gouv.fr/api/"),
            ("tabular_api", "prod", "https://tabular-api.data.gouv.fr/api/"),
            ("metrics_api", "demo", "https://metric-api.data.gouv.fr/api/"),
            ("metrics_api", "prod", "https://metric-api.data.gouv.fr/api/"),
            ("crawler_api", "demo", "https://demo-crawler.data.gouv.fr/api/"),
            ("crawler_api", "prod", "https://crawler.data.gouv.fr/api/"),
        ],
    )
    def test_get_base_url_for_api_and_env(
        self, monkeypatch, api_name, env, expected_url
    ):
        """Test get_base_url returns correct URL for each API and environment."""
        monkeypatch.setenv("DATAGOUV_API_ENV", env)
        url = env_config.get_base_url(api_name)
        assert url == expected_url

    def test_get_base_url_invalid_api_name(self, monkeypatch):
        """Test that invalid api_name raises KeyError."""
        monkeypatch.setenv("DATAGOUV_API_ENV", "prod")
        with pytest.raises(KeyError, match="Invalid api_name"):
            env_config.get_base_url("invalid_api")


_CUSTOM_VARS = (
    "UDATA_API_URL",
    "UDATA_SITE_URL",
    "UDATA_PORTAL_NAME",
    "TABULAR_API_URL",
    "METRICS_API_URL",
    "CRAWLER_API_URL",
)


@pytest.fixture
def custom_instance(monkeypatch):
    """Point the server at a custom udata instance with no satellite services."""
    for var in _CUSTOM_VARS:
        monkeypatch.delenv(var, raising=False)
    monkeypatch.setenv("UDATA_API_URL", "https://dados.gov.pt/api/")


class TestDefaultInstance:
    """data.gouv.fr (demo|prod) behaviour is unchanged."""

    @pytest.mark.parametrize("env", ["demo", "prod"])
    def test_all_apis_available(self, monkeypatch, env):
        for var in _CUSTOM_VARS:
            monkeypatch.delenv(var, raising=False)
        monkeypatch.setenv("DATAGOUV_API_ENV", env)
        assert env_config.get_env_name() == env
        assert env_config.get_portal_name() == "data.gouv.fr"
        for api_name in env_config._ENV_TARGETS[env]:
            assert env_config.is_available(api_name)

    def test_custom_vars_ignored_without_udata_api_url(self, monkeypatch):
        """Satellite overrides alone do not switch away from data.gouv.fr."""
        monkeypatch.delenv("UDATA_API_URL", raising=False)
        monkeypatch.setenv("DATAGOUV_API_ENV", "prod")
        monkeypatch.setenv("TABULAR_API_URL", "https://tabular.example.org/api/")
        assert env_config.get_base_url("tabular_api") == (
            "https://tabular-api.data.gouv.fr/api/"
        )


class TestCustomInstance:
    """UDATA_API_URL selects a custom udata instance."""

    def test_env_name_is_custom(self, custom_instance):
        assert env_config.get_env_name() == "custom"

    def test_takes_precedence_over_datagouv_api_env(self, custom_instance, monkeypatch):
        monkeypatch.setenv("DATAGOUV_API_ENV", "demo")
        assert env_config.get_base_url("datagouv_api") == "https://dados.gov.pt/api/"

    def test_site_url_derived_from_api_url(self, custom_instance):
        assert env_config.get_base_url("site") == "https://dados.gov.pt/"

    def test_site_url_derived_without_api_suffix(self, custom_instance, monkeypatch):
        monkeypatch.setenv("UDATA_API_URL", "https://data.example.org/udata")
        assert env_config.get_base_url("site") == "https://data.example.org/"

    def test_urls_get_trailing_slash(self, custom_instance, monkeypatch):
        monkeypatch.setenv("UDATA_API_URL", "https://dados.gov.pt/api")
        monkeypatch.setenv("UDATA_SITE_URL", "https://dados.gov.pt")
        assert env_config.get_base_url("datagouv_api") == "https://dados.gov.pt/api/"
        assert env_config.get_base_url("site") == "https://dados.gov.pt/"

    @pytest.mark.parametrize("api_name", ["tabular_api", "metrics_api", "crawler_api"])
    def test_satellites_unavailable_by_default(self, custom_instance, api_name):
        assert env_config.is_available(api_name) is False
        with pytest.raises(env_config.ServiceUnavailableError, match="dados.gov.pt"):
            env_config.get_base_url(api_name)

    @pytest.mark.parametrize(
        "api_name,var_name",
        [
            ("tabular_api", "TABULAR_API_URL"),
            ("metrics_api", "METRICS_API_URL"),
            ("crawler_api", "CRAWLER_API_URL"),
        ],
    )
    def test_satellite_enabled_by_env(
        self, custom_instance, monkeypatch, api_name, var_name
    ):
        monkeypatch.setenv(var_name, "https://satellite.example.org/api")
        assert env_config.is_available(api_name) is True
        assert env_config.get_base_url(api_name) == "https://satellite.example.org/api/"

    def test_blank_satellite_url_is_unavailable(self, custom_instance, monkeypatch):
        monkeypatch.setenv("TABULAR_API_URL", "  ")
        assert env_config.is_available("tabular_api") is False

    def test_portal_name_from_host(self, custom_instance, monkeypatch):
        monkeypatch.setenv("UDATA_SITE_URL", "https://www.example.org/")
        assert env_config.get_portal_name() == "example.org"

    def test_portal_name_from_env(self, custom_instance, monkeypatch):
        monkeypatch.setenv("UDATA_PORTAL_NAME", "Portal de Dados Abertos")
        assert env_config.get_portal_name() == "Portal de Dados Abertos"

    def test_invalid_api_name(self, custom_instance):
        with pytest.raises(KeyError, match="Invalid api_name"):
            env_config.is_available("invalid_api")
