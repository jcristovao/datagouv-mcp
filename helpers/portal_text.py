"""
Portal-specific wording: tool description placeholders and search stop words.

The language is read from UDATA_LANGUAGE (fr|pt|en). It defaults to fr on
data.gouv.fr and to en (neutral examples) on a custom udata instance.
"""

import os
from dataclasses import dataclass

from helpers import env_config

# Format names users add to queries but that rarely appear in dataset metadata
_FORMAT_STOP_WORDS = frozenset({"csv", "excel", "xlsx", "json", "xml"})


@dataclass(frozen=True)
class _Language:
    stop_words: frozenset[str]
    generic_words: str
    org_example: str


_LANGUAGES = {
    "fr": _Language(
        stop_words=frozenset(
            {
                "données",
                "donnee",
                "donnees",
                "fichier",
                "fichiers",
                "fichier de",
                "fichiers de",
                "tableau",
                "tableaux",
            }
        ),
        generic_words='"données" or "fichier"',
        org_example='"INSEE"',
    ),
    "pt": _Language(
        stop_words=frozenset(
            {
                "dados",
                "ficheiro",
                "ficheiros",
                "arquivo",
                "arquivos",
                "tabela",
                "tabelas",
            }
        ),
        generic_words='"dados" or "ficheiro"',
        org_example='"INE"',
    ),
    "en": _Language(
        stop_words=frozenset({"data", "file", "files", "table", "tables"}),
        generic_words='"data" or "file"',
        org_example='"Eurostat"',
    ),
}


def get_language() -> str:
    """Get the portal language (fr|pt|en) used for wording and stop words."""
    default = "en" if env_config.get_env_name() == "custom" else "fr"
    language = os.getenv("UDATA_LANGUAGE", "").strip().lower()
    return language if language in _LANGUAGES else default


def stop_words() -> frozenset[str]:
    """Generic query words that break AND-based searches on this portal."""
    return _LANGUAGES[get_language()].stop_words | _FORMAT_STOP_WORDS


def describe(**extra: str):
    """
    Decorator: fill placeholders in a tool's docstring (its MCP description).

    Always available: {portal}, {generic_words}, {org_example}. Tools can pass
    extra placeholders as keyword arguments.
    """
    language = _LANGUAGES[get_language()]
    values = {
        "portal": env_config.get_portal_name(),
        "generic_words": language.generic_words,
        "org_example": language.org_example,
        **extra,
    }

    def decorator(func):
        if func.__doc__:
            doc = func.__doc__
            for key, value in values.items():
                doc = doc.replace(f"{{{key}}}", value)
            func.__doc__ = doc
        return func

    return decorator
