import sqlite3
import logging
from datetime import datetime
from typing import Optional, Dict, Tuple, Any, List
from zoneinfo import ZoneInfo

logger = logging.getLogger(__name__)
DB_TIME_FMT = "%Y-%m-%d %H:%M:%S"


class DataBase:
    def __init__(self, db_path: str = "energy.db"):
        self.db_path = db_path
        self._init_db()

    def _init_db(self):
        with sqlite3.connect(self.db_path) as conn:
            c = conn.cursor()

            # --- energy delta log ---
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

            # --- daily max table ---
            # Mỗi dòng đại diện cho (inverter_id, day, mppt_index, string_index)
            c.execute("""
                CREATE TABLE IF NOT EXISTS daily_max (
                    inverter_id TEXT NOT NULL,
                    day TEXT NOT NULL,                 -- YYYY-MM-DD (theo timezone VN)
                    mppt_index INTEGER NOT NULL,       -- 1..9
                    string_index INTEGER NOT NULL,     -- 1..18 (global)
                    max_v_mppt REAL NOT NULL DEFAULT 0,
                    max_i_mppt REAL NOT NULL DEFAULT 0,
                    max_p_mppt REAL NOT NULL DEFAULT 0,
                    max_i_string REAL NOT NULL DEFAULT 0,
                    updated_at DATETIME NOT NULL,
                    PRIMARY KEY (inverter_id, day, mppt_index, string_index)
                )
            """)
            c.execute("""
                CREATE INDEX IF NOT EXISTS idx_daily_max_day
                ON daily_max (inverter_id, day)
            """)

            conn.commit()

    # --------------------------
    # ENERGY LOG APIs
    # --------------------------
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
                float(e_total),
                float(e_delta),
                source
            ))
            conn.commit()

    def get_last_record(self, inverter_id: str) -> Optional[Dict[str, Any]]:
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
            "e_total": float(row["e_total"])
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
        params: List[Any] = [from_ts.strftime(DB_TIME_FMT), to_ts.strftime(DB_TIME_FMT)]

        if inverter_id:
            query += " AND inverter_id = ?"
            params.append(inverter_id)

        with sqlite3.connect(self.db_path) as conn:
            val = conn.execute(query, params).fetchone()[0]

        return round(float(val or 0.0), 6)

    def get_month_energy(self, inverter_id: Optional[str], year: int, month: int) -> float:
        start = datetime(year, month, 1)
        if month == 12:
            end = datetime(year + 1, 1, 1)
        else:
            end = datetime(year, month + 1, 1)
        return self.sum_energy(inverter_id, start, end)

    # --------------------------
    # DAILY MAX APIs
    # --------------------------
    def upsert_daily_max(
        self,
        inverter_id: str,
        day: str,
        mppt_index: int,
        string_index: int,
        max_v_mppt: float,
        max_i_mppt: float,
        max_p_mppt: float,
        max_i_string: float,
        ts: datetime
    ):
        with sqlite3.connect(self.db_path) as conn:
            conn.execute("""
                INSERT INTO daily_max
                (inverter_id, day, mppt_index, string_index,
                 max_v_mppt, max_i_mppt, max_p_mppt, max_i_string, updated_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(inverter_id, day, mppt_index, string_index)
                DO UPDATE SET
                    max_v_mppt   = MAX(daily_max.max_v_mppt, excluded.max_v_mppt),
                    max_i_mppt   = MAX(daily_max.max_i_mppt, excluded.max_i_mppt),
                    max_p_mppt   = MAX(daily_max.max_p_mppt, excluded.max_p_mppt),
                    max_i_string = MAX(daily_max.max_i_string, excluded.max_i_string),
                    updated_at   = excluded.updated_at
            """, (
                inverter_id, day, int(mppt_index), int(string_index),
                float(max_v_mppt), float(max_i_mppt), float(max_p_mppt), float(max_i_string),
                ts.strftime(DB_TIME_FMT)
            ))
            conn.commit()

    def get_daily_max(self, inverter_id: str, day: str) -> List[Dict[str, Any]]:
        with sqlite3.connect(self.db_path) as conn:
            conn.row_factory = sqlite3.Row
            rows = conn.execute("""
                SELECT inverter_id, day, mppt_index, string_index,
                       max_v_mppt, max_i_mppt, max_p_mppt, max_i_string
                FROM daily_max
                WHERE inverter_id = ? AND day = ?
                ORDER BY mppt_index ASC, string_index ASC
            """, (inverter_id, day)).fetchall()

        return [dict(r) for r in rows]


class InverterTracker:
    """
    1) Track energy delta từ E_total
    2) Track daily max: I_mppt, V_mppt, P_mppt, I_string
    """

    def __init__(
        self,
        db: DataBase,
        tz: str = "Asia/Ho_Chi_Minh",
        max_delta_kwh: float = 500.0,
        min_interval_sec: int = 5,
    ):
        self.db = db
        self.max_delta_kwh = max_delta_kwh
        self.min_interval_sec = min_interval_sec
        self.tz = ZoneInfo(tz)

        # energy cache
        self._last_e_total: Dict[str, float] = {}
        self._last_ts: Dict[str, datetime] = {}

        # daily max cache (RAM) để giảm query DB
        self._last_day: Dict[str, str] = {}  # inverter_id -> YYYY-MM-DD
        self._mppt_max: Dict[tuple, Dict[str, float]] = {}   # (inv, day, mppt) -> max dict
        self._string_max: Dict[tuple, float] = {}            # (inv, day, string) -> max I

    # ------------------------------
    # ENERGY DELTA
    # ------------------------------
    def process_energy_total(
        self,
        inverter_id: str,
        e_total_now: float,
        ts: Optional[datetime] = None,
        source: str = "telemetry",
    ) -> float:
        ts = ts or datetime.utcnow()
        prev_e, prev_ts = self._get_last_state(inverter_id)

        # Lần đầu: vẫn ghi DB với delta=0 để có mốc
        if prev_e is None:
            self.db.insert_delta(inverter_id, ts, e_total_now, 0.0, source)
            self._update_state(inverter_id, e_total_now, ts)
            return 0.0

        # Chống ghi dày
        if prev_ts:
            dt = (ts - prev_ts).total_seconds()
            if dt < self.min_interval_sec:
                return 0.0

        delta = float(e_total_now) - float(prev_e)
        delta = self._validate_delta(inverter_id, delta)

        self.db.insert_delta(inverter_id, ts, e_total_now, delta, source)
        self._update_state(inverter_id, e_total_now, ts)
        return delta

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
        self._last_e_total[inverter_id] = float(e_total)
        self._last_ts[inverter_id] = ts

    def _validate_delta(self, inverter_id: str, delta: float) -> float:
        if delta < 0:
            logger.warning("[Energy] Counter reset inverter=%s delta=%.3f -> ignore", inverter_id, delta)
            return 0.0

        if delta > self.max_delta_kwh:
            logger.warning("[Energy] Spike inverter=%s delta=%.3f -> clamp", inverter_id, delta)
            return float(self.max_delta_kwh)

        return round(float(delta), 6)

    # ------------------------------
    # DAILY MAX TRACKING
    # ------------------------------
    def _day_key(self, ts: datetime) -> str:
        if ts.tzinfo is None:
            ts = ts.replace(tzinfo=ZoneInfo("UTC"))
        return ts.astimezone(self.tz).date().isoformat()

    def process_daily_max(
        self,
        inverter_id: str,
        mppts: List[Dict[str, Any]],
        ts: Optional[datetime] = None
    ):
        """
        mppts: [
          {
            "mppt_index": 1..9,
            "V_mppt": float,
            "I_mppt": float,
            "P_mppt": float,
            "strings":[ {"string_index":1..18, "I_string": float}, ... ]
          }, ...
        ]
        """
        ts = ts or datetime.utcnow()
        day = self._day_key(ts)

        # sang ngày mới: clear cache của inverter
        if self._last_day.get(inverter_id) != day:
            self._last_day[inverter_id] = day
            self._mppt_max = {k: v for k, v in self._mppt_max.items() if k[0] != inverter_id}
            self._string_max = {k: v for k, v in self._string_max.items() if k[0] != inverter_id}

        for m in mppts:
            mppt_index = int(m.get("mppt_index") or 0)
            v = float(m.get("V_mppt") or 0)
            i = float(m.get("I_mppt") or 0)
            p = float(m.get("P_mppt") or 0)

            mk = (inverter_id, day, mppt_index)
            cur = self._mppt_max.get(mk)
            if cur is None:
                cur = {"max_v": 0.0, "max_i": 0.0, "max_p": 0.0}
                self._mppt_max[mk] = cur

            if v > cur["max_v"]: cur["max_v"] = v
            if i > cur["max_i"]: cur["max_i"] = i
            if p > cur["max_p"]: cur["max_p"] = p

            for s in (m.get("strings") or []):
                string_index = int(s.get("string_index") or 0)
                i_str = float(s.get("I_string") or 0)

                sk = (inverter_id, day, string_index)
                prev = self._string_max.get(sk, 0.0)
                if i_str > prev:
                    self._string_max[sk] = i_str

                # upsert DB cho từng string (để bền khi reboot)
                self.db.upsert_daily_max(
                    inverter_id=inverter_id,
                    day=day,
                    mppt_index=mppt_index,
                    string_index=string_index,
                    max_v_mppt=cur["max_v"],
                    max_i_mppt=cur["max_i"],
                    max_p_mppt=cur["max_p"],
                    max_i_string=self._string_max.get(sk, 0.0),
                    ts=ts
                )

    def apply_daily_max_to_mppts(
        self,
        inverter_id: str,
        mppts: List[Dict[str, Any]],
        ts: Optional[datetime] = None
    ):
        """
        Lấy daily max (từ DB) và gán vào payload:
          mppt.Max_V/Max_I/Max_P
          string.Max_I
        """
        ts = ts or datetime.utcnow()
        day = self._day_key(ts)

        rows = self.db.get_daily_max(inverter_id, day)

        mppt_map: Dict[int, Tuple[float, float, float]] = {}
        str_map: Dict[int, float] = {}

        for r in rows:
            mi = int(r["mppt_index"])
            si = int(r["string_index"])
            mppt_map[mi] = (float(r["max_v_mppt"]), float(r["max_i_mppt"]), float(r["max_p_mppt"]))
            str_map[si] = float(r["max_i_string"])

        for m in mppts:
            mi = int(m.get("mppt_index") or 0)
            mv, mi_max, mp = mppt_map.get(mi, (0.0, 0.0, 0.0))
            m["Max_V"] = mv
            m["Max_I"] = mi_max
            m["Max_P"] = mp

            for s in (m.get("strings") or []):
                si = int(s.get("string_index") or 0)
                s["Max_I"] = str_map.get(si, 0.0)
