"""Application settings loaded from a local TOML file."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
import tomllib


@dataclass(frozen=True)
class Settings:
    max_actions_per_run: int = 5
    preferred_authors_enabled: bool = False
    blacklist_enabled: bool = False
    preferred_authors: frozenset[int] = frozenset()
    blacklist: frozenset[int] = frozenset()

    def is_blacklisted(self, user_id: int) -> bool:
        return self.blacklist_enabled and user_id in self.blacklist


def load_settings(path: Path) -> Settings:
    if not path.exists():
        return Settings()

    with path.open("rb") as stream:
        raw = tomllib.load(stream)

    limits = raw.get("limits", {})
    features = raw.get("features", {})
    authors = raw.get("authors", {})
    max_actions = int(limits.get("max_actions_per_run", 5))
    if max_actions < 1:
        raise ValueError("limits.max_actions_per_run must be at least 1")

    return Settings(
        max_actions_per_run=max_actions,
        preferred_authors_enabled=bool(
            features.get("preferred_authors_enabled", False)
        ),
        blacklist_enabled=bool(features.get("blacklist_enabled", False)),
        preferred_authors=frozenset(int(value) for value in authors.get("preferred", [])),
        blacklist=frozenset(int(value) for value in authors.get("blacklist", [])),
    )
