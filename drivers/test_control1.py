from pymodbus.client import ModbusSerialClient
import time
from typing import Tuple

# ==========================
# MODBUS RTU
# ==========================
client = ModbusSerialClient(
    port="/dev/ttyUSB0",
    baudrate=9600,
    bytesize=8,
    parity='N',
    stopbits=1,
    timeout=1
)

# ==========================
# SYSTEM
# ==========================
SLAVE_IDS    = [1, 2, 3, 4]
READ_ADDR    = 5031   # IR U32
STATE_ADDR   = 5038   # IR U16
WRITE_ADDR   = 5039   # HR U16 (0.1 kW)
SCALE_FACTOR = 100

PN_KW = {1:110.0, 2:110.0, 3:110.0, 4:50.0}
PN_W  = {sid: int(PN_KW[sid]*1000) for sid in SLAVE_IDS}

# ==========================
# CONTROL PARAM
# ==========================
LOOP_PERIOD_S = 5.0

# Base PI (để fuzzy chỉnh)
Kp = 0.3
Ki = 0.05
Imin, Imax = -200.0, 200.0

# Ramp
BASE_RAMP_KW = 10.0
kR           = 0.6
RAMP_MIN_KW  = 5.0
RAMP_MAX_KW  = 40.0

# Headroom
H_MIN_KW = 5.0
H_MAX_KW = 40.0
kH       = 0.5

MIN_PCT_PN  = 3.0
BOOT_PCT_PN = 8.0

# ==========================
# STATE MAP
# ==========================
STATE_MAP = {
    0x0000: ("Run", True),
    0x8000: ("Stop", False),
    0x1300: ("Key stop", False),
    0x1500: ("Emergency stop", False),
    0x1400: ("Standby", False),
    0x1200: ("Initial standby", False),
    0x1600: ("Starting", False),
    0x9100: ("Alarm run", True),
    0x8100: ("Derating run", True),
    0x8200: ("Dispatch run", True),
    0x5500: ("Fault", False),
    0x2500: ("Communicate fault", False),
}

def decode_state(code: int):
    return STATE_MAP.get(code, (f"Unknown(0x{code:04X})", False))

# ==========================
# MODBUS HELPERS
# ==========================
def read_u32_input(addr, sid):
    rr = client.read_input_registers(addr-1, 2, slave=sid)
    if rr.isError(): return 0
    return (rr.registers[1] << 16) + rr.registers[0]

def read_u16_input(addr, sid):
    rr = client.read_input_registers(addr-1, 1, slave=sid)
    if rr.isError(): return 0
    return rr.registers[0]

def write_u16_holding(addr, sid, value_w):
    reg = int(value_w / SCALE_FACTOR)
    reg = max(0, min(0xFFFF, reg))
    rr = client.write_register(addr-1, reg, slave=sid)
    return not rr.isError()

# ==========================
# FUZZY LOGIC (THEO LUẬT BẠN GỬI)
# ==========================
KP_TABLE = [
    ["PB","PB","PS","Z","Z"],
    ["PB","PS","PS","Z","NS"],
    ["PS","Z","Z","Z","NS"],
    ["Z","NS","PS","PS","PB"],
    ["Z","Z","PS","PB","PB"],
]

KI_TABLE = [
    ["NB","NB","NS","Z","Z"],
    ["NB","NS","Z","Z","Z"],
    ["NS","Z","PS","PS","NS"],
    ["Z","Z","PS","PS","PB"],
    ["Z","Z","Z","PB","PB"],
]

OUTPUT_MAP = {
    "NB": -0.6,
    "NS": -0.3,
    "Z":   0.0,
    "PS":  0.3,
    "PB":  0.6,
}

E_MAX_KW  = 150.0
DE_MAX_KW = 80.0

def clamp(x, a, b):
    return max(a, min(b, x))

def quantize(x):
    if x <= -0.6: return 0
    if x <= -0.2: return 1
    if x <=  0.2: return 2
    if x <=  0.6: return 3
    return 4

def fuzzy_pid_gain(e_norm, de_norm):
    i = quantize(e_norm)
    j = quantize(de_norm)
    return OUTPUT_MAP[KP_TABLE[i][j]], OUTPUT_MAP[KI_TABLE[i][j]]

# ==========================
# PPC UTILITIES
# ==========================
def estimate_available(meas_w, e_kw):
    headroom_kw = clamp(kH * abs(e_kw), H_MIN_KW, H_MAX_KW)
    headroom_w  = int(headroom_kw * 1000)
    return {
        sid: min(PN_W[sid], meas_w[sid] + headroom_w)
        for sid in SLAVE_IDS
    }

def water_filling_allocate(target_w, avail):
    R = int(max(0, target_w))
    S = set(avail.keys())
    out = {sid:0 for sid in avail}
    while R > 0 and S:
        sumA = sum(avail[sid] for sid in S)
        if sumA <= 0: break
        for sid in list(S):
            share = avail[sid] * R // sumA
            out[sid] += share
        R -= sum(out[sid] for sid in S)
        break
    return out

def apply_ramp(prev, target, step_kw):
    step = step_kw * 1000
    if target > prev + step: return prev + step
    if target < prev - step: return prev - step
    return target

def min_nonzero_for_state(sid, grid_on):
    pct = MIN_PCT_PN if grid_on else BOOT_PCT_PN
    return int(pct * PN_W[sid] / 100)

# ==========================
# MAIN
# ==========================
client.connect()
TARGET_W = int(input("TARGET W: ") or "20000")

prev_cmd = {sid:0.0 for sid in SLAVE_IDS}
pi_int = 0.0
prev_e_kw = 0.0

while True:
    meas_w, states = {}, {}
    total_meas = 0

    for sid in SLAVE_IDS:
        meas_w[sid] = read_u32_input(READ_ADDR, sid)
        states[sid] = read_u16_input(STATE_ADDR, sid)
        total_meas += meas_w[sid]

    # ===== FUZZY PI TOTAL POWER =====
    e_kw  = (TARGET_W - total_meas) / 1000
    de_kw = e_kw - prev_e_kw

    e_norm  = clamp(e_kw / E_MAX_KW, -1, 1)
    de_norm = clamp(de_kw / DE_MAX_KW, -1, 1)

    dkp, dki = fuzzy_pid_gain(e_norm, de_norm)

    Kp_eff = clamp(Kp * (1 + dkp), 0.3*Kp, 2.0*Kp)
    Ki_eff = clamp(Ki * (1 + dki), 0.1*Ki, 1.5*Ki)

    pi_int = clamp(pi_int + Ki_eff * e_kw, Imin, Imax)

    plant_ref_w = max(
        0,
        (TARGET_W/1000 + Kp_eff*e_kw + pi_int) * 1000
    )

    prev_e_kw = e_kw

    # ===== PPC =====
    P_avail = estimate_available(meas_w, e_kw)
    set_raw = water_filling_allocate(plant_ref_w, P_avail)

    ramp_kw = clamp(BASE_RAMP_KW + kR*abs(e_kw), RAMP_MIN_KW, RAMP_MAX_KW)

    for sid in SLAVE_IDS:
        _, grid_on = decode_state(states[sid])
        floor_w = min_nonzero_for_state(sid, grid_on)

        cmd = apply_ramp(prev_cmd[sid], set_raw[sid], ramp_kw)
        cmd = clamp(cmd, floor_w, PN_W[sid])

        if write_u16_holding(WRITE_ADDR, sid, int(cmd)):
            prev_cmd[sid] = cmd

    print(
        f"Pmeas={total_meas:.0f}W "
        f"e={e_kw:.2f}kW "
        f"Kp={Kp_eff:.2f} Ki={Ki_eff:.2f} "
        f"Pref={plant_ref_w:.0f}W"
    )

    time.sleep(LOOP_PERIOD_S)
