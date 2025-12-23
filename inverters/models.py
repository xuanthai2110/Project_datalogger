from typing import Dict, Any, List


class InverterData:
    """
    Container chuẩn cho 1 inverter sau khi đọc dữ liệu.
    TelemetryBuilder / EnergyTracker chỉ làm việc với class này,
    KHÔNG làm việc trực tiếp với driver.
    """

    # -----------------------------
    def __init__(
        self,
        inverter_id: int,
        serial: str,
        tele: Dict[str, Any],
        mppt: List[Dict[str, Any]],
        strings: List[float],
        severity: str = "STABLE",
        fault_code: int = 0,
        fault_description: str = "",
    ):
        self.inverter_id = inverter_id
        self.serial = serial
        self.tele = tele
        self.mppt = mppt
        self.strings = strings
        self.severity = severity
        self.fault_code = fault_code
        self.fault_description = fault_description

    # ======================================================
    # FACTORY METHODS
    # ======================================================
    @classmethod
    def from_snapshot(
        cls,
        inverter_id: int,
        serial: str,
        snapshot: Dict[str, Any],
    ) -> "InverterData":
        """
        Tạo InverterData từ snapshot driver.read_all()
        """

        # snapshot = {"inverters": [ {...} ]}
        inv = snapshot["inverters"][0]

        ac = inv.get("ac", {})
        mppts = inv.get("mppts", [])

        # gom strings từ mppts
        strings: List[float] = []
        for mp in mppts:
            for s in mp.get("strings", []):
                strings.append(s.get("I_mppt", 0.0))

        errors = inv.get("errors", [{}])
        err = errors[0] if errors else {}

        tele = {
            # AC
            "IR": ac.get("IR", 0.0),
            "temperature": ac.get("Temp_C", 0.0),
            "p_total_w": ac.get("P_ac", 0.0),
            "q_total_var": ac.get("Q_ac", 0.0),
            "v_ab": ac.get("V_a", 0.0),
            "v_bc": ac.get("V_b", 0.0),
            "v_ca": ac.get("V_c", 0.0),
            "i_a": ac.get("I_a", 0.0),
            "i_b": ac.get("I_b", 0.0),
            "i_c": ac.get("I_c", 0.0),
            "pf": ac.get("PF", 0.0),
            "F": ac.get("H", 0.0),
            # Energy
            "e_day_kwh": ac.get("E_daily", 0.0),
            "e_total_kwh": ac.get("E_total", 0.0),
            # Error
            "fault_code": err.get("fault_code", 0),
            "fault_description": err.get("fault_description", ""),
            "severity": err.get("severity", "STABLE"),
        }

        # MPPT list chuẩn hoá
        mppt_list = []
        for mp in mppts:
            mppt_list.append({
                "v": mp.get("V_mppt", 0.0),
                "i": mp.get("I_mppt", 0.0),
                "p": mp.get("P_mppt", 0.0),
            })

        return cls(
            inverter_id=inverter_id,
            serial=serial,
            tele=tele,
            mppt=mppt_list,
            strings=strings,
            severity=tele.get("severity", "STABLE"),
            fault_code=tele.get("fault_code", 0),
            fault_description=tele.get("fault_description", ""),
        )

    # -----------------------------
    @classmethod
    def offline(
        cls,
        inverter_id: int,
        serial: str,
    ) -> "InverterData":
        """
        Inverter mất kết nối
        """

        return cls(
            inverter_id=inverter_id,
            serial=serial,
            tele={
                "IR": 0.0,
                "temperature": 0.0,
                "p_total_w": 0.0,
                "q_total_var": 0.0,
                "v_ab": 0.0,
                "v_bc": 0.0,
                "v_ca": 0.0,
                "i_a": 0.0,
                "i_b": 0.0,
                "i_c": 0.0,
                "pf": 0.0,
                "F": 0.0,
                "e_day_kwh": 0.0,
                "e_total_kwh": 0.0,
                "fault_code": 0,
                "fault_description": "OFFLINE",
                "severity": "OFFLINE",
            },
            mppt=[],
            strings=[],
            severity="OFFLINE",
            fault_code=0,
            fault_description="OFFLINE",
        )
