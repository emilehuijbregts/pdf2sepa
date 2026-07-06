"""Central UI string lookup with language selection and NL fallback."""

from __future__ import annotations

from ui.i18n.languages import en, nl

_DEFAULT_LANGUAGE = "nl"
_SUPPORTED = frozenset({"nl", "en"})

_REGISTRIES: dict[str, dict[str, str]] = {
    "nl": dict(nl.STRINGS),
    "en": dict(en.STRINGS),
}


class MissingTranslationKeyError(KeyError):
    """Raised when a translation key is absent from all registries."""

    def __init__(self, key: str, language: str) -> None:
        self.key = key
        self.language = language
        super().__init__(f"Missing translation key '{key}' (language='{language}')")


class UiStrings:
    """Class-level UI string resolver; no instances required."""

    _language: str = _DEFAULT_LANGUAGE

    @classmethod
    def language(cls) -> str:
        return cls._language

    @classmethod
    def set_language(cls, code: str) -> None:
        if code not in _SUPPORTED:
            raise ValueError(f"Unsupported language: {code!r}")
        cls._language = code

    @classmethod
    def has(cls, key: str) -> bool:
        return cls._lookup_template(key, cls._language) is not None

    @classmethod
    def translate(cls, key: str, **kwargs: object) -> str:
        template = cls._lookup_template(key, cls._language)
        if template is None:
            raise MissingTranslationKeyError(key, cls._language)
        if not kwargs:
            return template
        return template.format(**kwargs)

    @classmethod
    def _lookup_template(cls, key: str, language: str) -> str | None:
        registry = _REGISTRIES.get(language, {})
        if key in registry:
            return registry[key]
        if language != _DEFAULT_LANGUAGE and key in _REGISTRIES[_DEFAULT_LANGUAGE]:
            return _REGISTRIES[_DEFAULT_LANGUAGE][key]
        return None


def tr(key: str, /, **kwargs: object) -> str:
    """Shortcut for UiStrings.translate."""
    return UiStrings.translate(key, **kwargs)


def tr_or_code(key: str, fallback: str = "", /, **kwargs: object) -> str:
    """Translate *key* when registered; otherwise return *fallback* or the key."""
    if UiStrings.has(key):
        return tr(key, **kwargs)
    return fallback or key
