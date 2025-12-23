from typing import Dict
from fuzzy_pid import fuzzy_pid_gain


# =========================================================
# Utility
# =========================================================
def clamp(x: float, xmin: float, xmax: float) -> float:
    return max(xmin, min(xmax, x))


# =========================================================
# PPC Controller
# =========================================================
class PPCController:
    def __init__(
        self,
        inverter_pn_kw: Dict[int, float],
        kp_base: float,
        ki_base: float,
        loop_period_s: float = 5.0,
        e_max_kw: float = 150.0,
        de_max_kw: float = 80.0,
        i_min: float = -200.0,
        i_max: float = 200.0,
        base_ramp_kw: float = 10.0,
        ramp_min_kw: float = 5.0,
        ramp_max_kw: float = 40.0,
        min_pct_pn: float = 3.0,
        boot_pct_pn: float = 8.0,
    ):
        # Inverter info
        self.inverter_pn_kw = inverter_pn_kw
        self.inverter_pn_w = {
            sid: int(pn * 1000) for sid, pn in inverter_pn_kw.items()
        }

        # PI base gains
        self.kp_base = kp_base
        self.ki_base = ki_base
        self.kp = kp_base
        self.ki = ki_base

        # Timing
        self.dt = loop_period_s

        # Fuzzy normalization
        self.e_max_kw = e_max_kw
        self.de_max_kw = de_max_kw

        # Integral
        self.integral = 0.0
        self.i_min = i_min
        self.i_max = i_max

        # Ramp
        self.base_ramp_kw = base_ramp_kw
        self.ramp_min_kw = ramp_min_kw
        self.ramp_max_kw = ramp_max_kw

        # Min power protection
        self.min_pct_pn = min_pct_pn
        self.boot_pct_pn = boot_pct_pn

        # State
        self.prev_error_kw = 0.0
        self.prev_cmd_w = {
            sid: 0.0 for sid in inverter_pn_kw.keys()
        }

    # -----------------------------------------------------
    # Plant-level fuzzy PI
    # -----------------------------------------------------
    def _plant_fuzzy_pi(self, p_set_w: float, p_meas_w: float) -> float:
        error_kw = (p_set_w - p_meas_w) / 1000.0
        d_error_kw = error_kw - self.prev_error_kw

        # Normalize
        e_norm = clamp(error_kw / self.e_max_kw, -1.0, 1.0)
        de_norm = clamp(d_error_kw / self.de_max_kw, -1.0, 1.0)

        # Fuzzy gain scheduling
        dkp, dki = fuzzy_pid_gain(e_norm, de_norm)

        self.kp = clamp(
            self.kp_base * (1.0 + dkp),
            0.3 * self.kp_base,
            2.0 * self.kp_base
        )

        self.ki = clamp(
            self.ki_base * (1.0 + dki),
            0.1 * self.ki_base,
            1.5 * self.ki_base
        )

        # Integral
        self.integral = clamp(
            self.integral + self.ki * error_kw,
            self.i_min,
            self.i_max
        )

        # PI output (plant reference)
        plant_ref_kw = (
            p_set_w / 1000.0
            + self.kp * error_kw
            + self.integral
        )

        self.prev_error_kw = error_kw

        return max(0.0, plant_ref_kw * 1000.0)

    # -----------------------------------------------------
    # PPC allocation (water-filling + ramp + limits)
    # -----------------------------------------------------
    def _allocate_power(
        self,
        plant_ref_w: float,
        meas_w: Dict[int, float],
        grid_on: Dict[int, bool],
    ) -> Dict[int, int]:

        # Available power (simple headroom model)
        avail_w = {}
        for sid, pn_w in self.inverter_pn_w.items():
            avail_w[sid] = min(
                pn_w,
                meas_w.get(sid, 0.0) + self.base_ramp_kw * 1000
            )

        # Water-filling
        remaining = plant_ref_w
        cmd = {sid: 0.0 for sid in avail_w.keys()}

        total_avail = sum(avail_w.values())
        if total_avail <= 0:
            return cmd

        for sid in avail_w.keys():
            cmd[sid] = avail_w[sid] * remaining / total_avail

        # Ramp + min power protection
        ramp_w = clamp(
            self.base_ramp_kw * 1000,
            self.ramp_min_kw * 1000,
            self.ramp_max_kw * 1000,
        )

        for sid in cmd.keys():
            prev = self.prev_cmd_w[sid]
            target = cmd[sid]

            # Ramp limit
            if target > prev + ramp_w:
                target = prev + ramp_w
            elif target < prev - ramp_w:
                target = prev - ramp_w

            # Min power (avoid standby)
            pct = self.min_pct_pn if grid_on.get(sid, False) else self.boot_pct_pn
            floor_w = pct * self.inverter_pn_w[sid] / 100.0

            cmd[sid] = int(
                clamp(target, floor_w, self.inverter_pn_w[sid])
            )

            self.prev_cmd_w[sid] = cmd[sid]

        return cmd

    # -----------------------------------------------------
    # Public step
    # -----------------------------------------------------
    def step(
        self,
        p_set_w: float,
        p_meas_total_w: float,
        meas_w_by_inv: Dict[int, float],
        grid_on_by_inv: Dict[int, bool],
    ) -> Dict[int, int]:
        """
        One PPC control step

        Returns
        -------
        Dict[inverter_id, P_cmd_w]
        """

        plant_ref_w = self._plant_fuzzy_pi(
            p_set_w,
            p_meas_total_w
        )

        return self._allocate_power(
            plant_ref_w,
            meas_w_by_inv,
            grid_on_by_inv
        )
