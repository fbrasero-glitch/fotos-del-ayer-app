from __future__ import annotations

import sqlite3
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path


class ApiQuotaExceeded(RuntimeError):
    pass


@dataclass(frozen=True, slots=True)
class UsageSnapshot:
    provider: str
    used: int
    limit: int
    period: str

    @property
    def remaining(self) -> int:
        return max(0, self.limit - self.used)


class ApiUsageStore:
    """Contador local conservador para no sobrepasar cuotas configuradas."""

    def __init__(self, path: str | Path = "data/fotos_de_ayer.db") -> None:
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self._connect() as connection:
            connection.execute(
                """
                CREATE TABLE IF NOT EXISTS api_usage (
                    provider TEXT NOT NULL,
                    period TEXT NOT NULL,
                    used INTEGER NOT NULL DEFAULT 0,
                    updated_at TEXT NOT NULL,
                    PRIMARY KEY(provider, period)
                )
                """
            )

    def _connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self.path, timeout=5)
        connection.execute("PRAGMA busy_timeout=5000")
        return connection

    @staticmethod
    def month_period() -> str:
        return datetime.now(timezone.utc).strftime("%Y-%m")

    def snapshot(self, provider: str, limit: int) -> UsageSnapshot:
        period = self.month_period()
        with self._connect() as connection:
            row = connection.execute(
                "SELECT used FROM api_usage WHERE provider = ? AND period = ?",
                (provider, period),
            ).fetchone()
        return UsageSnapshot(provider, int(row[0]) if row else 0, limit, period)

    def reserve(self, provider: str, limit: int) -> UsageSnapshot:
        if limit <= 0:
            raise ApiQuotaExceeded(f"{provider}: el límite local está desactivado.")
        period = self.month_period()
        now = datetime.now(timezone.utc).isoformat()
        with self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            row = connection.execute(
                "SELECT used FROM api_usage WHERE provider = ? AND period = ?",
                (provider, period),
            ).fetchone()
            used = int(row[0]) if row else 0
            if used >= limit:
                raise ApiQuotaExceeded(
                    f"{provider}: alcanzado el límite local mensual ({used}/{limit})."
                )
            connection.execute(
                """
                INSERT INTO api_usage(provider, period, used, updated_at)
                VALUES (?, ?, 1, ?)
                ON CONFLICT(provider, period) DO UPDATE SET
                    used=api_usage.used + 1,
                    updated_at=excluded.updated_at
                """,
                (provider, period, now),
            )
        return self.snapshot(provider, limit)

    def release(self, provider: str) -> None:
        """Devuelve una reserva cuando la petición falla antes de producir resultados."""
        period = self.month_period()
        with self._connect() as connection:
            connection.execute(
                """
                UPDATE api_usage
                SET used = CASE WHEN used > 0 THEN used - 1 ELSE 0 END,
                    updated_at = ?
                WHERE provider = ? AND period = ?
                """,
                (datetime.now(timezone.utc).isoformat(), provider, period),
            )
