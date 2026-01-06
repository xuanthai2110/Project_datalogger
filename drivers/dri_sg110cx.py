# dri_sg110cx.py

import logging
import datetime
from typing import Optional

from transports.modbus_rtu import ModbusRTUTransport

logger = logging.getLogger(__name__)


class SungrowSG110CXInverter:
    @staticmethod
    def _scale(value: int | float | None, scale: float | int = 1.0):
        if value is None:
            return None
        if scale in (1, 1.0, None):
            return value

        v = value * scale

        if scale == 0.1:
            return round(v, 1)
        if scale == 0.001:
            return round(v, 3)

        s = f"{scale:.10f}".rstrip("0").rstrip(".")
        if "." in s:
            ndigits = len(s.split(".")[1])
        else:
            ndigits = 0

        return round(v, ndigits)

    @staticmethod
    def _work_state_text(code: int | None) -> str | None:
        if code is None:
            return None

        mapping = {
            0x0000: "run (grid-connected)",
            0x8000: "stop",
            0x1300: "key stop",
            0x1500: "emergency stop",
            0x1400: "standby (low DC input)",
            0x1200: "initial standby (power-on)",
            0x1600: "starting",
            0x9100: "alarm run",
            0x8100: "derating run",
            0x8200: "dispatch run",
            0x5500: "fault",
            0x2500: "communication fault",
            0x1111: "uninitialized",
        }
        return mapping.get(code, f"unknown (0x{code:04X})")

    @staticmethod
    def _u16(val: int | None):
        if val is None:
            return None
        return val & 0xFFFF

    @staticmethod
    def _s16(val: int | None):
        if val is None:
            return None
        v = val & 0xFFFF
        if v & 0x8000:
            v -= 0x10000
        return v

    @staticmethod
    def _u32(low: int | None, high: int | None):
        """
        U32 little-endian word (low address = low word)
        """
        if low is None or high is None:
            return None
        return ((high & 0xFFFF) << 16) | (low & 0xFFFF)

    @staticmethod
    def _s32(low: int | None, high: int | None):
        """
        S32 little-endian word
        """
        v = SungrowSG110CXInverter._u32(low, high)
        if v is None:
            return None
        if v & 0x80000000:
            v -= 0x100000000
        return v

    @staticmethod
    def _decode_string_from_regs(registers: list[int]) -> str:

        raw_bytes = bytearray()
        for reg in registers:
            high = (reg >> 8) & 0xFF
            low = reg & 0xFF
            raw_bytes.append(high)
            raw_bytes.append(low)
        return raw_bytes.decode("utf-8", errors="ignore").rstrip("\x00").strip()

    # ------------------------------------------------------------------
    # INIT / TRANSPORT
    # ------------------------------------------------------------------
    def __init__(
        self,
        mode: str = "rtu",
        unit_id: int = 1,
        # RTU params
        port: Optional[str] = None,
        baudrate: int = 9600,
        parity: str = "N",
        stopbits: int = 1,
        timeout: float = 1.0,
        rtu_retries: int = 3,
        # TCP params (để đó, chưa triển khai)
        host: Optional[str] = None,
        tcp_port: int = 502,
        # connect retry (ở lớp driver)
        connect_retries: int = 3,
    ):
        """
        :param mode: "rtu" hoặc "tcp" (hiện tại mới dùng "rtu")
        :param unit_id: Modbus slave ID (inverter address)
        :param port: cổng serial, ví dụ "/dev/ttyUSB0" hoặc "COM3"
        :param connect_retries: số lần thử connect() ở lớp driver
        """
        self.mode = mode.lower()
        self.unit_id = unit_id
        self.connect_retries = connect_retries

        if self.mode == "rtu":
            if not port:
                raise ValueError("RTU mode require 'port'")
            self.transport = ModbusRTUTransport(
                port=port,
                baudrate=baudrate,
                parity=parity,
                stopbits=stopbits,
                timeout=timeout,
                retries=rtu_retries,
            )
        elif self.mode == "tcp":
            # Có thể implement ModbusTCPTransport sau, tạm thời chưa cần
            raise NotImplementedError("TCP mode is not implemented yet")
        else:
            raise ValueError(f"Unsupported mode: {mode}")

    def connect(self) -> bool:
        for attempt in range(1, self.connect_retries + 1):
            logger.info(f"[SG110CX] Connect attempt {attempt}/{self.connect_retries}")
            if self.transport.connect():
                logger.info("[SG110CX] Connected to inverter")
                return True
            logger.warning("[SG110CX] Connect failed")

        logger.error("[SG110CX] Could not connect to inverter after retries")
        return False

    def disconnect(self):
        """
        Ngắt kết nối với inverter.
        """
        try:
            self.transport.close()
            logger.info("[SG110CX] Disconnected from inverter")
        except Exception as exc:
            logger.error(f"[SG110CX] Error while disconnecting: {exc}")

    # ------------------------------------------------------------------
    # INTERNAL READ BLOCK HELPER
    # ------------------------------------------------------------------
    def _read_ir_block(self, start_manual: int, end_manual: int) -> dict[int, int]:

        count = end_manual - start_manual + 1
        wire_addr = start_manual - 1  # 0-based

        resp = self.transport.read_input_registers(
            address=wire_addr,
            count=count,
            slave=self.unit_id,
        )
        if resp is None or not hasattr(resp, "registers"):
            raise RuntimeError(
                f"[SG110CX] No response when reading IR block {start_manual}-{end_manual}"
            )

        regs: dict[int, int] = {}
        for offset, val in enumerate(resp.registers):
            regs[start_manual + offset] = val

        if len(resp.registers) < count:
            logger.warning(
                f"[SG110CX] Expected {count} regs for block {start_manual}-{end_manual}, "
                f"got {len(resp.registers)}. Using what we have."
            )

        return regs

    def read_info(self) -> dict:

        regs = self._read_ir_block(4990, 5002)

        # SN: 4990–4999 (10 regs)
        sn_regs = [regs.get(addr, 0) for addr in range(4990, 5000)]
        sn = self._decode_string_from_regs(sn_regs)

        # type_code: 5000
        type_code = self._u16(regs.get(5000))

        # rated_power: 5001, scale 0.1 kW
        rated_power_raw = self._u16(regs.get(5001))
        rated_power = self._scale(rated_power_raw, 0.1)

        # output_type: 5002
        output_type_raw = self._u16(regs.get(5002))
        output_type_map = {
            0: "two phase",
            1: "3P4L",
            2: "3P3L",
        }
        output_type = (
            output_type_map.get(output_type_raw)
            if output_type_raw is not None
            else None
        )

        info = {
            "sn": sn,
            "type_code": type_code,
            "rated_power": rated_power,        # kW
            "output_type_code": output_type_raw,
            "output_type": output_type,        # "two phase" / "3P4L" / "3P3L"
        }

        logger.info(f"[SG110CX] Inverter info: {info}")
        return info

    def read_state(self) -> dict:

        regs = self._read_ir_block(5038, 5045)

        work_state_code = self._u16(regs.get(5038))
        year = self._u16(regs.get(5039))
        month = self._u16(regs.get(5040))
        day = self._u16(regs.get(5041))
        hour = self._u16(regs.get(5042))
        minute = self._u16(regs.get(5043))
        second = self._u16(regs.get(5044))
        fault_code = self._u16(regs.get(5045))

        ts_dict = {
            "year": year,
            "month": month,
            "day": day,
            "hour": hour,
            "minute": minute,
            "second": second,
        }

        ts_iso = None
        try:
            if all(v is not None for v in (year, month, day, hour, minute, second)):
                dt = datetime.datetime(
                    year=year,
                    month=month,
                    day=day,
                    hour=hour,
                    minute=minute,
                    second=second,
                )
                ts_iso = dt.isoformat()
        except Exception:
            ts_iso = None

        state = {
            "work_state_code": work_state_code,
            "work_state": self._work_state_text(work_state_code),
            "timestamp": ts_dict,
            "timestamp_iso": ts_iso,
            "fault_code": fault_code,
        }

        logger.info(f"[SG110CX] Inverter state: {state}")
        return state

    def read_telemetry(self) -> dict:

        regs: dict[int, int] = {}
        regs.update(self._read_ir_block(5003, 5024))   # counters + temp + Vabc + Iabc
        regs.update(self._read_ir_block(5031, 5036))   # P, Q, PF, F
        regs.update(self._read_ir_block(5071, 5071))   # IR
        regs.update(self._read_ir_block(5148, 5148))   # grid_hz

        g = regs.get

        # ---- Energy counters ----
        e_day_raw = self._u16(g(5003))
        e_day_kwh = self._scale(e_day_raw, 0.1)

        e_total_kwh = self._u32(g(5004), g(5005))       # kWh
        t_total_h = self._u32(g(5006), g(5007))         # h
        s_total_va = self._u32(g(5009), g(5010))        # VA

        # ---- Temperature ----
        temp_raw = self._s16(g(5008))
        temperature = self._scale(temp_raw, 0.1)        # °C

        # ---- AC Voltages ----
        v_ab_raw = self._u16(g(5019))
        v_bc_raw = self._u16(g(5020))
        v_ca_raw = self._u16(g(5021))

        v_ab = self._scale(v_ab_raw, 0.1)               # V
        v_bc = self._scale(v_bc_raw, 0.1)
        v_ca = self._scale(v_ca_raw, 0.1)

        # ---- AC Currents ----
        i_a_raw = self._u16(g(5022))
        i_b_raw = self._u16(g(5023))
        i_c_raw = self._u16(g(5024))

        i_a = self._scale(i_a_raw, 0.1)                 # A
        i_b = self._scale(i_b_raw, 0.1)
        i_c = self._scale(i_c_raw, 0.1)

        # ---- Powers ----
        p_inv_w = self._u32(g(5031), g(5032))           # W
        q_inv_var = self._s32(g(5033), g(5034))         # Var

        # ---- Power factor ----
        pf_raw = self._s16(g(5035))
        pf = self._scale(pf_raw, 0.001)

        # ---- Grid frequency ----
        F_raw = self._u16(g(5036))
        F = self._scale(F_raw, 0.1)                     # Hz

        ir_raw = self._u16(g(5071))
        IR = self._scale(ir_raw, 1)                     # đơn vị theo manual

        grid_hz_raw = self._u16(g(5148))
        grid_hz = self._scale(grid_hz_raw, 0.01)        # Hz

        telemetry = {
            # Energy
            "e_day_kwh": e_day_kwh,
            "e_total_kwh": e_total_kwh,
            "t_total_h": t_total_h,
            "s_total_va": s_total_va,

            # Temperature
            "temperature": temperature,

            # AC Voltages
            "v_ab": v_ab,
            "v_bc": v_bc,
            "v_ca": v_ca,

            # AC Currents
            "i_a": i_a,
            "i_b": i_b,
            "i_c": i_c,

            # Power
            "p_inv_w": p_inv_w,
            "q_inv_var": q_inv_var,

            # Power factor & frequency
            "pf": pf,
            "F": F,              # Hz, từ addr 5036 (0.1)
            "IR": IR,
            "grid_hz": grid_hz,  # Hz, từ addr 5148 (0.01)
        }

        logger.info(f"[SG110CX] Telemetry: {telemetry}")
        return telemetry

    def read_power(self) -> dict:
        regs = self._read_ir_block(5031, 5034)

        p_inv_w = self._u32(regs.get(5031), regs.get(5032))
        q_inv_var = self._s32(regs.get(5033), regs.get(5034))

        power = {
            "p_inv_w": p_inv_w,
            "q_inv_var": q_inv_var,
        }
        logger.info(f"[SG110CX] Power: {power}")
        return power

    def read_mppt(self) -> dict:
        """
        Đọc điện áp / dòng / công suất của 9 MPPT.

        Trả về:
            {
                "mppt_1": {"v": V1, "i": I1, "p_w": P1_W, "p_kw": P1_kW},
                ...
                "mppt_9": {...}
            }
        """
        regs: dict[int, int] = {}
        regs.update(self._read_ir_block(5011, 5016))   # MPPT 1–3
        regs.update(self._read_ir_block(5115, 5124))   # MPPT 4–8
        regs.update(self._read_ir_block(5130, 5131))   # MPPT 9

        value = regs.get

        def _mppt_pair(v_addr: int, i_addr: int) -> dict:
            v_raw = self._u16(value(v_addr))
            i_raw = self._u16(value(i_addr))

            v = self._scale(v_raw, 0.1)   # V
            i = self._scale(i_raw, 0.1)   # A

            if v is not None and i is not None:
                p_w = round(v * i, 1)          # W, làm tròn 1 số lẻ
                p_kw = round(p_w / 1000.0, 3)  # kW, làm tròn 3 số lẻ
            else:
                p_w = None
                p_kw = None

            return {
                "v": v,
                "i": i,
                "p_w": p_w,
                "p_kw": p_kw,
            }

        mppt = {
            "mppt_1": _mppt_pair(5011, 5012),
            "mppt_2": _mppt_pair(5013, 5014),
            "mppt_3": _mppt_pair(5015, 5016),
            "mppt_4": _mppt_pair(5115, 5116),
            "mppt_5": _mppt_pair(5117, 5118),
            "mppt_6": _mppt_pair(5119, 5120),
            "mppt_7": _mppt_pair(5121, 5122),
            "mppt_8": _mppt_pair(5123, 5124),
            "mppt_9": _mppt_pair(5130, 5131),
        }

        logger.info(f"[SG110CX] MPPT: {mppt}")
        return mppt

    def read_string(self) -> dict:

        base_addr = 7013
        count = 18
        regs = self._read_ir_block(base_addr, base_addr + count - 1)

        strings: dict[str, float | None] = {}

        for idx in range(count):
            addr = base_addr + idx
            raw = self._u16(regs.get(addr))
            current = self._scale(raw, 0.01)  # A
            strings[f"string_{idx + 1}"] = current

        logger.info(f"[SG110CX] String currents: {strings}")
        return strings

    from typing import Dict, Any, List

    def read_all(self) -> Dict[str, Any]:
        def R(x, ndigits=3):
            if x is None:
                return 0
            try:
                return round(float(x), ndigits)
            except Exception:
                return x

        # --- 1) Read block A ---
        regs: Dict[int, int] = {}
        regs.update(self._read_ir_block(4990, 5100))   # block A (see manual). :contentReference[oaicite:13]{index=13}

        # --- 2) Read block B ---
        regs.update(self._read_ir_block(5101, 5193))   # block B: MPPT4..MPPT16 region. :contentReference[oaicite:14]{index=14}

        # --- 3) Read block C (strings) ---
        regs.update(self._read_ir_block(7013, 7030))   # strings 7013..7030. note reserved 5194..7012. :contentReference[oaicite:15]{index=15}

        g = regs.get

        # --- Parse info (4990..5002) ---
        sn_regs = [g(addr, 0) for addr in range(4990, 5000)]
        serial_number = self._decode_string_from_regs(sn_regs)

        device_type = self._u16(g(5000))
        rated_power = self._scale(self._u16(g(5001)), 0.1)
        output_type_raw = self._u16(g(5002))
        output_type_map = {0: "two phase", 1: "3P4L", 2: "3P3L"}
        output_type = output_type_map.get(output_type_raw, None)

        # --- Telemetry & counters (from block A/B) ---
        e_day_kwh = self._scale(self._u16(g(5003)), 0.1)
        e_total_kwh = self._u32(g(5004), g(5005))
        t_total_h = self._u32(g(5006), g(5007))
        s_total_va = self._u32(g(5009), g(5010))

        temp = self._scale(self._s16(g(5008)), 0.1)

        v_ab = self._scale(self._u16(g(5019)), 0.1)
        v_bc = self._scale(self._u16(g(5020)), 0.1)
        v_ca = self._scale(self._u16(g(5021)), 0.1)

        i_a = self._scale(self._u16(g(5022)), 0.1)
        i_b = self._scale(self._u16(g(5023)), 0.1)
        i_c = self._scale(self._u16(g(5024)), 0.1)

        p_inv_w = self._u32(g(5031), g(5032))
        q_inv_var = self._s32(g(5033), g(5034))

        pf = self._scale(self._s16(g(5035)), 0.001)
        F = self._scale(self._u16(g(5036)), 0.1)

        work_state_code = self._u16(g(5038))
        work_state_text = self._work_state_text(work_state_code)

        fault_code = self._u16(g(5045))  # fault code (if any). :contentReference[oaicite:16]{index=16}

        ir = self._scale(self._u16(g(5071)), 1)
        grid_hz = self._scale(self._u16(g(5148)), 0.01)  # fine-grained Hz at 5148. :contentReference[oaicite:17]{index=17}

        # --- Build AC block according to your schema ---
        ac = {
            "IR": R(ir, ndigits=3),
            "Temp_C": R(temp, ndigits=1),
            "P_ac": R(p_inv_w or 0, ndigits=1),
            "Q_ac": R(q_inv_var or 0, ndigits=1),
            "V_a": R(v_ab, ndigits=1),
            "V_b": R(v_bc, ndigits=1),
            "V_c": R(v_ca, ndigits=1),
            "I_a": R(i_a, ndigits=2),
            "I_b": R(i_b, ndigits=2),
            "I_c": R(i_c, ndigits=2),
            "PF": R(pf, ndigits=3),
            "H": R(F, ndigits=2),
            "E_daily": R(e_day_kwh, ndigits=3),
            "E_monthly": None, 
            "E_total": R(e_total_kwh, ndigits=3)
        }

        # --- Determine MPPT defs from manual layout (addresses) ---
        # MPPT1..3 in 5011..5016 (block A), MPPT4..8 in 5115..5124, MPPT9 in 5130..5131 (block B)
        mppt_addrs = [
            (5011, 5012), (5013, 5014), (5015, 5016),  # MPPT 1..3
            (5115, 5116), (5117, 5118), (5119, 5120), (5121, 5122), (5123, 5124),  # MPPT 4..8
            (5130, 5131)  # MPPT 9 (SG110CX supports 9 MPPT per Appendix 5). :contentReference[oaicite:18]{index=18}
        ]

        mppts: List[Dict[str, Any]] = []
        for idx, (va, ia) in enumerate(mppt_addrs, start=1):
            v = self._scale(self._u16(g(va)), 0.1)
            i = self._scale(self._u16(g(ia)), 0.1)
            p_calc = round(v * i, 1) if (v is not None and i is not None) else 0
            mppts.append({
                "mppt_index": idx,
                "string_on_mppt": 0,   # will fill after strings
                "V_mppt": R(v, ndigits=1),
                "I_mppt": R(i, ndigits=2),
                "P_mppt": R(p_calc, ndigits=1),
                "Max_I": None,
                "Max_V": None,
                "Max_P": None,
                "strings": []
            })

        # --- Read string currents (7013..7030) and distribute to MPPTs ---
        string_base = 7013
        # For SG110CX Appendix 5 shows there are 9 MPPT and typically 2 strings/mppt (-> 18 strings total). :contentReference[oaicite:19]{index=19}
        strings_values: List[float] = []
        for offset in range(0, 18):  # read 18 strings for SG110CX (7013..7030)
            addr = string_base + offset
            raw = self._u16(g(addr))
            val = self._scale(raw, 0.01)  # unit 0.01 A per manual. :contentReference[oaicite:20]{index=20}
            strings_values.append(val)

        total_mppts = len(mppts)
        per_mppt = (len(strings_values) // total_mppts) if total_mppts else 0

        for i in range(total_mppts):
            start = i * per_mppt
            end = start + per_mppt
            slice_vals = strings_values[start:end]
            mppts[i]["string_on_mppt"] = len(slice_vals)
            mppt_strings = []
            for j, sval in enumerate(slice_vals):
                mppt_strings.append({
                    "string_index": start + j + 1,
                    "I_mppt": R(sval, ndigits=2),
                    "Max_I": None
                })
            mppts[i]["strings"] = mppt_strings

        # --- Errors (single element list as schema) ---
        error_obj = {
            "fault_code": int(fault_code or 0),
            "fault_description": work_state_text or "",
            "repair_instruction": "",
            "severity": "STABLE"
        }

        inverter_obj = {
            "serial_number": serial_number or "",
            "ac": ac,
            "mppts": mppts,
            "errors": [error_obj]
        }

        return {"inverters": [inverter_obj]}

    def _write_hr_u16(self, addr: int, value: int):
        """
        Write single holding register (U16).
        addr: manual address (1-based)
        """
        wire_addr = addr - 1  # Modbus 0-based
        resp = self.transport.write_register(
            address=wire_addr,
            value=value & 0xFFFF,
            slave=self.unit_id,
        )
        if resp is None or resp.isError():
            raise RuntimeError(
                f"[SG110CX] Write HR failed at {addr}, value={value}"
            )
    def enable_power_limit(self):
        logger.info("[SG110CX] Enable power limitation")
        self._write_hr_u16(5007, 0xAA)
    def disable_power_limit(self):
        logger.info("[SG110CX] Disable power limitation")
        self._write_hr_u16(5007, 0x55)
    def write_power_limit_kw(self, p_kw: float):
        if p_kw < 0:
            p_kw = 0.0

        # Rated power protection (optional but recommended)
        try:
            info = self.read_info()
            rated_kw = info.get("rated_power")
            if rated_kw and p_kw > rated_kw:
                logger.warning(
                    f"[SG110CX] p_kw {p_kw} > rated {rated_kw}, clamp"
                )
                p_kw = rated_kw
        except Exception:
            # Nếu không đọc được info thì bỏ qua clamp
            pass

        reg_value = int(round(p_kw * 10))  # 0.1 kW resolution

        logger.info(
            f"[SG110CX] Write power limit: {p_kw:.1f} kW (reg={reg_value})"
        )

        self._write_hr_u16(5039, reg_value)
    def write_power(self, p_kw: float, enable: bool = True):
        if enable:
            self.enable_power_limit()

        self.write_power_limit_kw(p_kw)
