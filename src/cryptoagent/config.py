"""Settings loaded from the environment and an optional .env file.

Secrets are wrapped in `Secret` so they never appear in repr, str or log output.
Providers look up their own keys with `settings.secret("NAME")`, so adding a
provider never requires a change here.
"""

from __future__ import annotations

import os
from collections.abc import Mapping
from dataclasses import dataclass, field
from pathlib import Path

DEFAULT_DB_PATH = Path("data") / "cryptoagent.db"


class Secret:
    __slots__ = ("_value",)

    def __init__(self, value: str):
        self._value = value

    def reveal(self) -> str:
        return self._value

    def __bool__(self) -> bool:
        return bool(self._value)

    def __repr__(self) -> str:
        return "Secret('***')" if self._value else "Secret('')"

    __str__ = __repr__


@dataclass(frozen=True)
class Settings:
    db_path: Path
    _values: Mapping[str, str] = field(default_factory=dict, repr=False)

    def secret(self, name: str) -> Secret:
        return Secret(self._values.get(name, ""))


def read_env_file(path: Path) -> dict[str, str]:
    """Parse KEY=VALUE lines; ignores blanks, comments and surrounding quotes."""
    values: dict[str, str] = {}
    if not path.is_file():
        return values
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        values[key.strip()] = value.strip().strip("'\"")
    return values


def load_settings(env_file: str | Path = ".env", environ: Mapping[str, str] | None = None) -> Settings:
    """Real environment variables take precedence over the .env file."""
    merged = read_env_file(Path(env_file))
    merged.update(os.environ if environ is None else environ)
    return Settings(db_path=Path(merged.get("CRYPTOAGENT_DB_PATH") or DEFAULT_DB_PATH), _values=merged)
