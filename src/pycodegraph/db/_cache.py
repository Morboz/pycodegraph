"""Internal LRU cache for QueryBuilder."""

from __future__ import annotations

from collections import OrderedDict
from typing import Generic, TypeVar

T = TypeVar("T")


class LRUCache(Generic[T]):
    """Simple LRU cache keyed by string."""

    def __init__(self, max_size: int = 1000) -> None:
        self._cache: OrderedDict[str, T] = OrderedDict()
        self._max_size = max_size

    def get(self, key: str) -> T | None:
        if key in self._cache:
            self._cache.move_to_end(key)
            return self._cache[key]
        return None

    def put(self, key: str, value: T) -> None:
        if key in self._cache:
            self._cache.move_to_end(key)
        else:
            if len(self._cache) >= self._max_size:
                self._cache.popitem(last=False)
        self._cache[key] = value

    def invalidate(self, key: str) -> None:
        self._cache.pop(key, None)

    def invalidate_by_attr(self, attr: str, value: object) -> None:
        """Remove all entries whose ``entry.attr == value``."""
        keys_to_remove = [
            k for k, v in self._cache.items() if getattr(v, attr, None) == value
        ]
        for k in keys_to_remove:
            del self._cache[k]

    def invalidate_by_attr_in(self, attr: str, values: set) -> None:
        """Remove all entries whose ``entry.attr`` is in *values*."""
        keys_to_remove = [
            k for k, v in self._cache.items() if getattr(v, attr, None) in values
        ]
        for k in keys_to_remove:
            del self._cache[k]

    def clear(self) -> None:
        self._cache.clear()
