"""Kleine, abhängigkeitsfreie Modell-Prüfung (Ersatz für Pydantic).

Grund: Die Windows-EXE darf keinen unsignierten, kompilierten Fremdcode enthalten – Smart App Control blockiert ihn
(Pydantic bringt mit ``pydantic_core`` eine solche DLL mit). Unterstützt genau das, was Flip 7 Live braucht: strenge
ganze Zahlen, Wahrheitswerte, Texte (trimmen, Länge, Muster), feste Auswahl, Listen (Länge, eindeutig, verschachtelte
Modelle), Pflicht-/optionale Felder mit Standardwerten und unbekannte Felder verbieten oder ignorieren.

Fehler haben dieselbe Form wie bei Pydantic (``loc``/``type``/``ctx``), damit die deutsche Übersetzung gleich bleibt.
"""

from __future__ import annotations

import re
from collections.abc import Callable
from typing import Any, ClassVar

_MISSING = object()


class ValidationError(ValueError):
    def __init__(self, errors: list[dict]):
        super().__init__(f"{len(errors)} Prüffehler")
        self._errors = errors

    def errors(self) -> list[dict]:
        return list(self._errors)


class F:
    """Beschreibung eines Feldes. ``kind``: "int" | "bool" | "str" | "choice" | "list" | ein Model."""

    def __init__(self, kind: Any, *, default: Any = _MISSING, factory: Callable[[], Any] | None = None,
                 optional: bool = False, ge: int | None = None, le: int | None = None,
                 min_len: int | None = None, max_len: int | None = None, strip: bool = False,
                 pattern: str | None = None, choices: tuple = (), item: Any = None, unique: bool = False):
        self.kind, self.default, self.factory, self.optional = kind, default, factory, optional
        self.ge, self.le, self.min_len, self.max_len, self.strip = ge, le, min_len, max_len, strip
        self.pattern = re.compile(pattern) if pattern else None
        self.choices, self.item, self.unique = choices, item, unique

    @property
    def required(self) -> bool:
        return self.default is _MISSING and self.factory is None

    def make_default(self) -> Any:
        return self.factory() if self.factory else self.default

    def check(self, value: Any, loc: tuple, errors: list[dict]) -> Any:
        if value is None:
            if self.optional:
                return None
            kind = self.kind if isinstance(self.kind, str) else "model"
            errors.append({"loc": loc, "type": {"int": "int_type", "bool": "bool_type", "list": "list_type",
                                                "model": "model_type"}.get(kind, "string_type"), "ctx": {}})
            return None
        if self.kind == "int":
            return self._int(value, loc, errors)
        if self.kind == "bool":
            if not isinstance(value, bool):
                errors.append({"loc": loc, "type": "bool_type", "ctx": {}})
            return value
        if self.kind == "str":
            return self._str(value, loc, errors)
        if self.kind == "choice":
            if value not in self.choices:
                errors.append({"loc": loc, "type": "literal_error", "ctx": {}})
            return value
        if self.kind == "list":
            return self._list(value, loc, errors)
        return self.kind._validate(value, loc, errors)  # verschachteltes Modell

    def _int(self, value, loc, errors):
        if isinstance(value, bool) or not isinstance(value, int):
            errors.append({"loc": loc, "type": "int_type", "ctx": {}})
            return value
        if self.ge is not None and value < self.ge:
            errors.append({"loc": loc, "type": "greater_than_equal", "ctx": {"ge": self.ge}})
        if self.le is not None and value > self.le:
            errors.append({"loc": loc, "type": "less_than_equal", "ctx": {"le": self.le}})
        return value

    def _str(self, value, loc, errors):
        if not isinstance(value, str):
            errors.append({"loc": loc, "type": "string_type", "ctx": {}})
            return value
        if self.strip:
            value = value.strip()
        if self.min_len is not None and len(value) < self.min_len:
            errors.append({"loc": loc, "type": "string_too_short", "ctx": {}})
        elif self.max_len is not None and len(value) > self.max_len:
            errors.append({"loc": loc, "type": "string_too_long", "ctx": {}})
        elif self.pattern and not self.pattern.search(value):
            errors.append({"loc": loc, "type": "string_pattern_mismatch", "ctx": {}})
        return value

    def _list(self, value, loc, errors):
        if not isinstance(value, list):
            errors.append({"loc": loc, "type": "list_type", "ctx": {}})
            return value
        if self.min_len is not None and len(value) < self.min_len:
            errors.append({"loc": loc, "type": "too_short", "ctx": {}})
            return value
        if self.max_len is not None and len(value) > self.max_len:
            errors.append({"loc": loc, "type": "too_long", "ctx": {}})
            return value
        out = [self.item.check(v, (*loc, i), errors) for i, v in enumerate(value)]
        if self.unique and len(out) == len(value) and all(isinstance(v, int) for v in out) and len(set(out)) != len(out):
            errors.append({"loc": loc, "type": "value_error", "ctx": {"error": "Ein Spieler ist doppelt aufgeführt."}})
        return out


class Model:
    """Basisklasse: Felder als Klassenattribute vom Typ :class:`F`."""

    _fields: ClassVar[dict[str, F]] = {}
    extra: ClassVar[str] = "forbid"  # oder "ignore"

    def __init_subclass__(cls, **kw):
        super().__init_subclass__(**kw)
        fields: dict[str, F] = {}
        for base in reversed(cls.__mro__[1:]):
            fields.update(getattr(base, "_fields", {}))
        fields.update({k: v for k, v in vars(cls).items() if isinstance(v, F)})
        cls._fields = fields

    def __init__(self, **values):
        data = type(self)._validate(values, (), errors := [], build=False)
        if errors:
            raise ValidationError(errors)
        self.__dict__.update(data)

    @classmethod
    def model_validate(cls, raw: Any):
        errors: list[dict] = []
        obj = cls._validate(raw, (), errors)
        if errors:
            raise ValidationError(errors)
        return obj

    @classmethod
    def _validate(cls, raw: Any, loc: tuple, errors: list[dict], build: bool = True):
        if not isinstance(raw, dict):
            errors.append({"loc": loc, "type": "model_type", "ctx": {}})
            return None
        if cls.extra == "forbid":
            for key in raw:
                if key not in cls._fields:
                    errors.append({"loc": (*loc, key), "type": "extra_forbidden", "ctx": {}})
        data = {}
        for name, spec in cls._fields.items():
            if name in raw:
                data[name] = spec.check(raw[name], (*loc, name), errors)
            elif spec.required:
                errors.append({"loc": (*loc, name), "type": "missing", "ctx": {}})
            else:
                data[name] = spec.make_default()
        if not build:
            return data
        obj = cls.__new__(cls)
        obj.__dict__.update(data)
        return obj

    def model_dump(self, exclude_none: bool = False) -> dict:
        out = {}
        for name in self._fields:
            value = _dump(getattr(self, name))
            if exclude_none and value is None:
                continue
            out[name] = value
        return out

    def __repr__(self) -> str:
        return f"{type(self).__name__}({', '.join(f'{k}={getattr(self, k)!r}' for k in self._fields)})"

    def __eq__(self, other) -> bool:
        return type(self) is type(other) and self.model_dump() == other.model_dump()


def _dump(value):
    if isinstance(value, Model):
        return value.model_dump()
    if isinstance(value, list):
        return [_dump(v) for v in value]
    return value
