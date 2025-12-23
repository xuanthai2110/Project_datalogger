import sqlite3
import logging
from datetime import datetime
from typing import Optional, Dict, Tuple

logger = logging.getLogger(__name__)
DB_TIME_FMT = "%Y-%m-%d %H:%M:%S"

class EnergyDB:

    def __init__(self, db_path: str = "energy.db"):
        self.db_path = db_path
        self._init_db()

    def _init_db(self):
        with sqlite3.connect(self.db_path) as conn:
            c = conn.cursor()

            c.execute("""
                CREATE TABLE IF NOT EXISTS energy_log (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    inverter_id TEXT NOT NULL,
                    ts DATETIME NOT NULL,
                    e_total REAL NOT NULL,
                    e_delta REAL NOT NULL,
                    source TEXT
                )
            """)

            c.execute("""
                CREATE INDEX IF NOT EXISTS idx_energy_ts
                ON energy_log (inverter_id, ts)
            """)

            conn.commit()

    def insert_delta(
        self,
        inverter_id: str,
        ts: datetime,
        e_total: float,
        e_delta: float,
        source: str = ""
    ):
        with sqlite3.connect(self.db_path) as conn:
            conn.execute("""
                INSERT INTO energy_log
                (inverter_id, ts, e_total, e_delta, source)
                VALUES (?, ?, ?, ?, ?)
            """, (
                inverter_id,
                ts.strftime(DB_TIME_FMT),
                e_total,
                e_delta,
                source
            ))
            conn.commit()

        logger.debug(
            "[EnergyDB] Insert inverter=%s ts=%s e_total=%.3f e_delta=%.6f",
            inverter_id, ts, e_total, e_delta
        )

    def get_last_record(self, inverter_id: str) -> Optional[Dict]:
        with sqlite3.connect(self.db_path) as conn:
            conn.row_factory = sqlite3.Row
            row = conn.execute("""
                SELECT ts, e_total
                FROM energy_log
                WHERE inverter_id = ?
                ORDER BY ts DESC
                LIMIT 1
            """, (inverter_id,)).fetchone()

        if not row:
            return None

        return {
            "ts": datetime.strptime(row["ts"], DB_TIME_FMT),
            "e_total": row["e_total"]
        }

    def sum_energy(
        self,
        inverter_id: Optional[str],
        from_ts: datetime,
        to_ts: datetime
    ) -> float:

        query = """
            SELECT COALESCE(SUM(e_delta), 0)
            FROM energy_log
            WHERE ts >= ? AND ts < ?
        """
        params = [from_ts.strftime(DB_TIME_FMT), to_ts.strftime(DB_TIME_FMT)]

        if inverter_id:
            query += " AND inverter_id = ?"
            params.append(inverter_id)

        with sqlite3.connect(self.db_path) as conn:
            val = conn.execute(query, params).fetchone()[0]

        return round(val or 0.0, 6)
    
    def get_month_energy(
        self,
        inverter_id: Optional[str],
        year: int,
        month: int
    ) -> float:
        start = datetime(year, month, 1)
        if month == 12:
            end = datetime(year + 1, 1, 1)
        else:
            end = datetime(year, month + 1, 1)

        return self.sum_energy(inverter_id, start, end)

class EnergyTracker:


    def __init__(
        self,
        energy_db: EnergyDB,
        max_delta_kwh: float = 500.0,
        min_interval_sec: int = 5,
    ):
        self.db = energy_db
        self.max_delta_kwh = max_delta_kwh
        self.min_interval_sec = min_interval_sec

        self._last_e_total: Dict[str, float] = {}
        self._last_ts: Dict[str, datetime] = {}

    # ------------------------------
    # PUBLIC
    # ------------------------------
    def process_reading(
        self,
        inverter_id: str,
        e_total_now: float,
        ts: Optional[datetime] = None,
        source: str = "telemetry",
    ) -> float:

        ts = ts or datetime.utcnow()

        prev_e, prev_ts = self._get_last_state(inverter_id)

        # Lần đầu
        if prev_e is None:
            self._update_state(inverter_id, e_total_now, ts)
            logger.info(
                "[EnergyTracker] First reading inverter=%s E_total=%.3f",
                inverter_id, e_total_now
            )
            return 0.0

        # Chống ghi dày
        if prev_ts:
            dt = (ts - prev_ts).total_seconds()
            if dt < self.min_interval_sec:
                return 0.0

        delta = e_total_now - prev_e
        delta = self._validate_delta(inverter_id, delta)

        # GHI DB
        self.db.insert_delta(
            inverter_id=inverter_id,
            ts=ts,
            e_total=e_total_now,
            e_delta=delta,
            source=source
        )

        self._update_state(inverter_id, e_total_now, ts)
        return delta

    def reset_inverter(self, inverter_id: str):
        self._last_e_total.pop(inverter_id, None)
        self._last_ts.pop(inverter_id, None)

    # ------------------------------
    # INTERNAL
    # ------------------------------
    def _get_last_state(self, inverter_id: str) -> Tuple[Optional[float], Optional[datetime]]:
        if inverter_id in self._last_e_total:
            return self._last_e_total[inverter_id], self._last_ts.get(inverter_id)

        last = self.db.get_last_record(inverter_id)
        if last:
            self._last_e_total[inverter_id] = last["e_total"]
            self._last_ts[inverter_id] = last["ts"]
            return last["e_total"], last["ts"]

        return None, None

    def _update_state(self, inverter_id: str, e_total: float, ts: datetime):
        self._last_e_total[inverter_id] = e_total
        self._last_ts[inverter_id] = ts

    def _validate_delta(self, inverter_id: str, delta: float) -> float:
        if delta < 0:
            logger.warning(
                "[EnergyTracker] Counter reset inverter=%s delta=%.3f → ignore",
                inverter_id, delta
            )
            return 0.0

        if delta > self.max_delta_kwh:
            logger.warning(
                "[EnergyTracker] Spike inverter=%s delta=%.3f → clamp",
                inverter_id, delta
            )
            return self.max_delta_kwh

        return round(delta, 6)
