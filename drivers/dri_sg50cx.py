# dri_sg50cx.py

import logging
import datetime
from typing import Dict, Any

from transports.modbus_rtu import ModbusRTUTransport

logger = logging.getLogger(__name__)


class SungrowSG50CXInverter:
    def __init__(
        self,
        port: str,
        unit_id: int = 1,
        baudrate: int = 9600,
        parity: str = "N",
        stopbits: int = 1,
        timeout: float = 1.0,
        rtu_retries: int = 3,
        connect_retries: int = 3,
    ):
        self.unit_id = unit_id
        self.connect_retries = connect_retries

        self.transport = ModbusRTUTransport(
            port=port,
            baudrate=baudrate,
            parity=parity,
            stopbits=stopbits,
            timeout=timeout,
            retries=rtu_retries,
        )

    # =========================================================
    # helpers
    # =========================================================

    @staticmethod
    def _u16(v: int | None):
        return None if v is None else v & 0xFFFF

    @staticmethod
    def _s16(v: int | None):
        if v is None:
            return None
        v &= 0xFFFF
        return v - 0x10000 if v & 0x8000 else v

    @staticmethod
    def _u32(lo: int | None, hi: int | None):
        if lo is None or hi is None:
            return None
        return ((hi & 0xFFFF) << 16) | (lo & 0xFFFF)

    @staticmethod
    def _s32(lo: int | None, hi: int | None):
        v = SungrowSG50CXInverter._u32(lo, hi)
        if v is None:
            return None
        return v - 0x100000000 if v & 0x80000000 else v

    @staticmethod
    def _scale(v: int | float | None, scale: float):
        if v is None:
            return None
        digits = len(str(scale).split(".")[-1])
        return round(v * scale, digits)

    @staticmethod
    def _decode_string(regs: list[int]) -> str:
        b = bytearray()
        for r in regs:
            b.append((r >> 8) & 0xFF)
            b.append(r & 0xFF)
        return b.decode("utf-8", errors="ignore").rstrip("\x00").strip()

    # =========================================================
    # low-level read/write
    # =========================================================

    def _read_ir_block(self, start: int, end: int) -> Dict[int, int]:
        count = end - start + 1
        resp = self.transport.read_input_registers(
            address=start - 1,
            count=count,
            slave=self.unit_id,
        )
        if resp is None or not hasattr(resp, "registers"):
            raise RuntimeError(f"[SG50CX] IR read failed {start}-{end}")

        return {start + i: v for i, v in enumerate(resp.registers)}

    def _write_hr_u16(self, addr: int, value: int):
        resp = self.transport.write_register(
            address=addr - 1,
            value=value & 0xFFFF,
            slave=self.unit_id,
        )
        if resp is None or resp.isError():
            raise RuntimeError(f"[SG50CX] HR write failed {addr}")

    # =========================================================
    # connection
    # =========================================================

    def connect(self) -> bool:
        for _ in range(self.connect_retries):
            if self.transport.connect():
                return True
        return False

    def disconnect(self):
        self.transport.close()

    # =========================================================
    # READ APIs
    # =========================================================

    def read_info(self) -> dict:
        regs = self._read_ir_block(4990, 5002)

        sn_regs = [regs.get(addr, 0) for addr in range(4990, 5000)]
        sn = self._decode_string(sn_regs)

        type_code = self._u16(regs.get(5000))

        rated_power_raw = self._u16(regs.get(5001))
        rated_power = self._scale(rated_power_raw, 0.1)

        output_type_code = self._u16(regs.get(5002))
        output_type_map = {
            0: "two phase",
            1: "3P4L",
            2: "3P3L",
        }
        output_type = (
            output_type_map.get(output_type_code)
            if output_type_code is not None
            else None
        )

        info = {
            "sn": sn,
            "type_code": type_code,
            "rated_power": rated_power,
            "output_type_code": output_type_code,
            "output_type": output_type,
        }

        logger.info(f"[SG110CX] Inverter info: {info}")
        return info


    def read_state(self) -> Dict[str, Any]:
        regs = self._read_ir_block(5038, 5045)
        ts = [self._u16(regs.get(a)) for a in range(5039, 5045)]

        ts_iso = None
        if all(ts):
            ts_iso = datetime.datetime(*ts).isoformat()

        return {
            "work_state_code": self._u16(regs.get(5038)),
            "timestamp": ts_iso,
            "fault_code": self._u16(regs.get(5045)),
        }

    def read_telemetry(self) -> Dict[str, Any]:
        regs = {}
        regs.update(self._read_ir_block(5003, 5024))
        regs.update(self._read_ir_block(5031, 5036))

        return {
            "E_day_kWh": self._scale(self._u16(regs.get(5003)), 0.1),
            "E_total_kWh": self._u32(regs.get(5004), regs.get(5005)),
            "Temp_C": self._scale(self._s16(regs.get(5008)), 0.1),
            "V_ab": self._scale(self._u16(regs.get(5019)), 0.1),
            "I_a": self._scale(self._u16(regs.get(5022)), 0.1),
            "P_W": self._u32(regs.get(5031), regs.get(5032)),
            "Q_var": self._s32(regs.get(5033), regs.get(5034)),
            "PF": self._scale(self._s16(regs.get(5035)), 0.001),
            "F_Hz": self._scale(self._u16(regs.get(5036)), 0.1),
        }

    # =========================================================
    # MPPT & STRING (SG50CX)
    # =========================================================

    def read_mppt(self) -> Dict[str, Any]:
        regs = self._read_ir_block(5011, 5016)

        mppt_addrs = [
            (5011, 5012),
            (5013, 5014),
            (5015, 5016),
        ]

        mppts = {}
        for i, (v, c) in enumerate(mppt_addrs, 1):
            V = self._scale(self._u16(regs.get(v)), 0.1)
            I = self._scale(self._u16(regs.get(c)), 0.1)
            mppts[f"mppt_{i}"] = {
                "V": V,
                "I": I,
                "P_W": round(V * I, 1) if V and I else 0,
            }

        return mppts

    def read_string(self) -> Dict[str, Any]:
        regs = self._read_ir_block(7013, 7022)  # 10 strings

        strings = {}
        for i in range(10):
            addr = 7013 + i
            strings[f"string_{i+1}"] = {
                "I_A": self._scale(self._u16(regs.get(addr)), 0.1)
            }

        return strings

    def read_all(self) -> Dict[str, Any]:
        data = {}
        data.update(self.read_info())
        data.update(self.read_state())
        data.update(self.read_telemetry())
        data["mppt"] = self.read_mppt()
        data["string"] = self.read_string()
        return data

    # =========================================================
    # WRITE (PPC / Dispatch)
    # =========================================================

    def enable_power_limit(self):
        self._write_hr_u16(5007, 0xAA)

    def disable_power_limit(self):
        self._write_hr_u16(5007, 0x55)

    def write_power_limit_kw(self, p_kw: float):
        self._write_hr_u16(5039, int(round(max(p_kw, 0) * 10)))

    def write_power(self, p_kw: float, enable: bool = True):
        if enable:
            self.enable_power_limit()
        self.write_power_limit_kw(p_kw)
