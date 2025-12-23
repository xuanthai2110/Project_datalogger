from typing import List, Dict, Any

from inverters.models import InverterData
from data.energy import EnergyTracker


class TelemetryBuilder:

    def __init__(self, tracker: EnergyTracker):
        self.tracker = tracker

    def build(self, inverters: List[InverterData], project_cfg: Dict[str, Any]) -> Dict[str, Any]:
        total_P_ac = 0.0
        total_P_dc = 0.0
        total_E_daily = 0.0
        total_E_total = 0.0
        temps = []

        # =========================
        # PROJECT AGGREGATION
        # =========================
        for inv in inverters:
            t = inv.tele

            total_P_ac += t.get("p_total_w", 0.0)
            total_E_daily += t.get("e_day_kwh", 0.0)
            total_E_total += t.get("e_total_kwh", 0.0)
            temps.append(t.get("temperature", 0.0))

            for mp in inv.mppt:
                total_P_dc += mp["v"] * mp["i"]

        avg_temp = sum(temps) / len(temps) if temps else 0.0

        # =========================
        # ENERGY TRACKER
        # =========================
        e_month_true, _, _ = self.tracker.update(
            e_total_now=total_E_total,
            e_daily_now=total_E_daily,
        )

        project = {
            "Temp_C": R(avg_temp),
            "P_ac": R(total_P_ac),
            "P_dc": R(total_P_dc),
            "E_daily": R(total_E_daily),
            "E_monthly": R(e_month_true),
            "E_total": R(total_E_total),
            "severity": "STABLE"
        }

        # =========================
        # INVERTER DETAILS
        # =========================
        inverter_list = []
        cfg_map = {i["serial_number"]: i for i in project_cfg["project"]["inverters"]}

        for inv in inverters:
            tele = inv.tele
            cfg = cfg_map.get(inv.serial, {})

            mppt_count = cfg.get("mppt_count", len(inv.mppt))
            string_count = cfg.get("string_count", len(inv.strings))
            strings_per_mppt = string_count // mppt_count if mppt_count else 0

            ac = {
                "IR": R(tele.get("IR", 0.0)),
                "Temp_C": R(tele.get("temperature", 0.0)),
                "P_ac": R(tele.get("p_total_w", 0.0)),
                "Q_ac": R(tele.get("q_total_var", 0.0)),
                "V_a": R(tele.get("v_ab", 0.0)),
                "V_b": R(tele.get("v_bc", 0.0)),
                "V_c": R(tele.get("v_ca", 0.0)),
                "I_a": R(tele.get("i_a", 0.0)),
                "I_b": R(tele.get("i_b", 0.0)),
                "I_c": R(tele.get("i_c", 0.0)),
                "PF": R(tele.get("pf", 0.0)),
                "H": R(tele.get("F", 0.0)),
                "E_daily": R(tele.get("e_day_kwh", 0.0)),
                "E_monthly": R(tele.get("e_day_kwh", 0.0)),  # theo spec cũ
                "E_total": R(tele.get("e_total_kwh", 0.0)),
            }

            # =========================
            # MPPT + STRING
            # =========================
            mppts = []
            for mppt_idx in range(mppt_count):
                mp = inv.mppt[mppt_idx]

                start = mppt_idx * strings_per_mppt
                end = start + strings_per_mppt
                slice_strings = inv.strings[start:end]

                mppts.append({
                    "mppt_index": mppt_idx + 1,
                    "string_on_mppt": len(slice_strings),
                    "V_mppt": R(mp["v"]),
                    "I_mppt": R(mp["i"]),
                    "P_mppt": R(mp["v"] * mp["i"]),
                    "Max_I": 15,
                    "Max_V": 1100,
                    "Max_P": 6000,
                    "strings": [
                        {
                            "string_index": start + i + 1,
                            "I_mppt": R(val),
                            "Max_I": 15
                        }
                        for i, val in enumerate(slice_strings)
                    ]
                })

            inverter_list.append({
                "serial_number": inv.serial,
                "ac": ac,
                "mppts": mppts,
                "errors": [{
                    "fault_code": tele.get("fault_code", 0),
                    "fault_description": "",
                    "repair_instruction": "",
                    "severity": "STABLE"
                }]
            })

        return {
            "project": project,
            "inverters": inverter_list
        }
