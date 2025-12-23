import time
import logging
from datetime import datetime
from typing import Dict, Any, List

from datalogger_core.config.loader import load_project_config
from datalogger_core.inverter.models import InverterData
from datalogger_core.energy.energy_db import EnergyDB
from datalogger_core.energy.energy_tracker import EnergyTracker
from datalogger_core.telemetry.telemetry_builder import TelemetryBuilder
from datalogger_core.transport.http_client import send_telemetry

# Drivers
from drivers.sungrow.dri_sg110cx import SungrowSG110CXInverter

logger = logging.getLogger(__name__)


# ==========================================================
# DRIVER FACTORY
# ==========================================================
def create_driver(inv_cfg: Dict[str, Any]):
    vendor = inv_cfg.get("vendor")
    model = inv_cfg.get("model")

    if vendor == "sungrow" and model == "SG110CX":
        return SungrowSG110CXInverter(
            mode=inv_cfg.get("protocol", "rtu"),
            unit_id=inv_cfg["unit_id"],
            port=inv_cfg["port"],
            baudrate=inv_cfg.get("baudrate", 9600),
            parity=inv_cfg.get("parity", "N"),
            stopbits=inv_cfg.get("stopbits", 1),
            timeout=inv_cfg.get("timeout", 1.0),
        )

    raise ValueError(f"Unsupported inverter: {vendor} {model}")


# ==========================================================
# SCHEDULE LOOP
# ==========================================================
def run_scheduler(
    config_path: str,
    poll_interval_sec: int = 60,
    db_flush_interval_sec: int = 1800,
):
    """
    poll_interval_sec: chu kỳ đọc inverter (vd: 60s)
    db_flush_interval_sec: chu kỳ ghi DB (vd: 1800s = 30p)
    """

    logger.info("Starting scheduler")

    # ------------------------------------------------------
    # STEP 1: LOAD PROJECT CONFIG
    # ------------------------------------------------------
    project_cfg = load_project_config(config_path)
    inverter_cfgs = project_cfg["project"]["inverters"]

    # ------------------------------------------------------
    # INIT ENERGY DB + TRACKER
    # ------------------------------------------------------
    energy_db = EnergyDB(project_id=project_cfg["project"]["project_id"])
    tracker = EnergyTracker(energy_db)
    telemetry_builder = TelemetryBuilder(tracker)

    # ------------------------------------------------------
    # INIT DRIVERS
    # ------------------------------------------------------
    drivers = {}
    for cfg in inverter_cfgs:
        try:
            drv = create_driver(cfg)
            if drv.connect():
                drivers[cfg["inverter_id"]] = drv
                logger.info(
                    f"Connected inverter_id={cfg['inverter_id']} sn={cfg['serial_number']}"
                )
            else:
                logger.error(f"Failed to connect inverter {cfg['serial_number']}")
        except Exception as exc:
            logger.exception(f"Driver init error: {exc}")

    last_db_flush = time.time()

    # ======================================================
    # MAIN LOOP
    # ======================================================
    while True:
        now = datetime.now()
        inverter_data_list: List[InverterData] = []

        # --------------------------------------------------
        # STEP 2: READ INVERTERS
        # --------------------------------------------------
        for cfg in inverter_cfgs:
            inverter_id = cfg["inverter_id"]
            serial = cfg["serial_number"]
            drv = drivers.get(inverter_id)

            if not drv:
                inverter_data_list.append(
                    InverterData.offline(
                        inverter_id=inverter_id,
                        serial=serial,
                    )
                )
                continue

            try:
                snapshot = drv.read_all()

                inv = InverterData.from_snapshot(
                    inverter_id=inverter_id,
                    serial=serial,
                    snapshot=snapshot,
                )

                inverter_data_list.append(inv)

            except Exception as exc:
                logger.warning(f"Inverter {serial} offline: {exc}")
                inverter_data_list.append(
                    InverterData.offline(
                        inverter_id=inverter_id,
                        serial=serial,
                    )
                )

        # --------------------------------------------------
        # STEP 3: UPDATE ENERGY TRACKER (RAM)
        # --------------------------------------------------
        for inv in inverter_data_list:
            if inv.severity != "OFFLINE":
                tracker.update_inverter(
                    inverter_id=inv.inverter_id,
                    inverter_sn=inv.serial,
                    e_total_now=inv.tele.get("e_total_kwh", 0.0),
                    now=now,
                )

        # --------------------------------------------------
        # PERIODIC DB FLUSH
        # --------------------------------------------------
        if time.time() - last_db_flush >= db_flush_interval_sec:
            tracker.flush_to_db()
            last_db_flush = time.time()
            logger.info("Energy state flushed to database")

        # --------------------------------------------------
        # STEP 4: BUILD TELEMETRY + SEND SERVER
        # --------------------------------------------------
        telemetry = telemetry_builder.build(
            inverters=inverter_data_list,
            project_cfg=project_cfg,
            now=now,
        )

        try:
            send_telemetry(telemetry)
            logger.info("Telemetry sent to server")
        except Exception as exc:
            logger.error(f"Send telemetry failed: {exc}")

        # --------------------------------------------------
        time.sleep(poll_interval_sec)
