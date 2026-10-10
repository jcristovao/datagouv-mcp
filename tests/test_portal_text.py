"""Tests for portal-specific wording and search stop words."""

import pytest

from helpers import portal_text
from tools.search_datasets import clean_search_query


@pytest.fixture
def data_gouv(monkeypatch):
    monkeypatch.delenv("UDATA_API_URL", raising=False)
    monkeypatch.delenv("UDATA_LANGUAGE", raising=False)


@pytest.fixture
def dados_gov_pt(monkeypatch):
    monkeypatch.setenv("UDATA_API_URL", "https://dados.gov.pt/api/")
    monkeypatch.delenv("UDATA_PORTAL_NAME", raising=False)
    monkeypatch.delenv("UDATA_SITE_URL", raising=False)
    monkeypatch.delenv("UDATA_LANGUAGE", raising=False)


class TestGetLanguage:
    def test_defaults_to_fr_on_data_gouv(self, data_gouv):
        assert portal_text.get_language() == "fr"

    def test_defaults_to_en_on_custom_instance(self, dados_gov_pt):
        assert portal_text.get_language() == "en"

    def test_set_by_env(self, dados_gov_pt, monkeypatch):
        monkeypatch.setenv("UDATA_LANGUAGE", " PT ")
        assert portal_text.get_language() == "pt"

    def test_unknown_language_falls_back_to_default(self, dados_gov_pt, monkeypatch):
        monkeypatch.setenv("UDATA_LANGUAGE", "de")
        assert portal_text.get_language() == "en"


class TestCleanSearchQuery:
    def test_french_stop_words_on_data_gouv(self, data_gouv):
        assert clean_search_query("données transport csv") == "transport"

    def test_portuguese_stop_words(self, dados_gov_pt, monkeypatch):
        monkeypatch.setenv("UDATA_LANGUAGE", "pt")
        assert clean_search_query("dados contratos públicos") == "contratos públicos"
        assert clean_search_query("Ficheiro CSV contratos") == "contratos"

    def test_french_words_kept_on_portuguese_portal(self, dados_gov_pt, monkeypatch):
        monkeypatch.setenv("UDATA_LANGUAGE", "pt")
        assert clean_search_query("données contratos") == "données contratos"

    def test_format_words_dropped_in_every_language(self, dados_gov_pt):
        assert clean_search_query("contratos xlsx") == "contratos"


class TestDescribe:
    def _doc(self, **extra):
        @portal_text.describe(**extra)
        def tool():
            """On {portal}: avoid {generic_words}; try {org_example}. {extra}"""

        return tool.__doc__

    def test_data_gouv_wording(self, data_gouv):
        assert self._doc(extra="x") == (
            'On data.gouv.fr: avoid "données" or "fichier"; try "INSEE". x'
        )

    def test_portuguese_wording(self, dados_gov_pt, monkeypatch):
        monkeypatch.setenv("UDATA_LANGUAGE", "pt")
        assert self._doc(extra="y") == (
            'On dados.gov.pt: avoid "dados" or "ficheiro"; try "INE". y'
        )

    def test_unknown_placeholder_left_alone(self, data_gouv):
        assert self._doc().endswith("{extra}")
