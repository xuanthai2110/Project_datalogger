"""
fuzzy_pid.py
Fuzzy gain scheduler for PPC (Solar Power Plant Controller)

- Input  : normalized error e_norm, de_norm  ∈ [-1, 1]
- Output : gain adjustment ΔKp, ΔKi
- Usage  : Tune Kp, Ki of outer PI loop (plant power loop)
"""

from typing import Tuple

# =========================================================
# FUZZY SETS
# =========================================================
SETS = ["NB", "NS", "Z", "PS", "PB"]
KP_TABLE = [
# de:   NB     NS     Z      PS     PB
    ["PB","PB","PS","Z","Z"],    # e = NB  (over power)
    ["PB","PS","PS","Z","NS"],   # e = NS
    ["PS","Z","Z","Z","NS"],     # e = Z
    ["Z","NS","PS","PS","PB"],   # e = PS
    ["Z","Z","PS","PB","PB"],    # e = PB  (under power)
]

KI_TABLE = [
# de:   NB     NS     Z      PS     PB
    ["NB","NB","NS","Z","Z"],    # e = NB
    ["NB","NS","Z","Z","Z"],     # e = NS
    ["NS","Z","PS","PS","NS"],   # e = Z
    ["Z","Z","PS","PS","PB"],    # e = PS
    ["Z","Z","Z","PB","PB"],     # e = PB
]

OUTPUT_MAP = {
    "NB": -0.4,
    "NS": -0.2,
    "Z":   0.0,
    "PS":  0.2,
    "PB":  0.4,
}

# =========================================================
# HELPER FUNCTIONS
# =========================================================
def clamp(x: float, xmin: float, xmax: float) -> float:
    return max(xmin, min(xmax, x))


def quantize(x: float) -> int:
    if x <= -0.6: return 0  # NB
    if x <= -0.2: return 1  # NS
    if x <=  0.2: return 2  # Z
    if x <=  0.6: return 3  # PS
    return 4                # PB


# =========================================================
# MAIN FUZZY FUNCTION (FOR PPC)
# =========================================================
def fuzzy_pid_gain(e_norm: float, de_norm: float) -> Tuple[float, float]:

    # Safety clamp
    e_norm  = clamp(e_norm,  -1.0, 1.0)
    de_norm = clamp(de_norm, -1.0, 1.0)

    i = quantize(e_norm)
    j = quantize(de_norm)

    dkp = OUTPUT_MAP[KP_TABLE[i][j]]
    dki = OUTPUT_MAP[KI_TABLE[i][j]]

    return dkp, dki
