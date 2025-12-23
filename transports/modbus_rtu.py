import logging
from pymodbus.client import ModbusSerialClient
from pymodbus.exceptions import ModbusIOException
from .base import BaseTransport

logger = logging.getLogger(__name__)


class ModbusRTUTransport(BaseTransport):

    def __init__(
        self,
        port: str,
        baudrate: int = 9600,
        parity: str = "N",
        stopbits: int = 1,
        timeout: float = 1.0,
        retries: int = 3,
    ):
        self.retries = retries

        self.client = ModbusSerialClient(
            method="rtu",
            port=port,
            baudrate=baudrate,
            parity=parity,
            stopbits=stopbits,
            bytesize=8,
            timeout=timeout,
        )

    # ------------------------------------------------------
    def connect(self):
        if not self.client.connect():
            logger.error(f"[RTU] Cannot open port {self.port}")
            return False
        return True

    def close(self):
        try:
            self.client.close()
        except Exception as e:
            logger.error(f"[RTU] Close error: {e}")

    # ------------------------------------------------------
    # Retry wrapper
    # ------------------------------------------------------
    def _retry(self, func, *args, **kwargs):
        for attempt in range(1, self.retries + 1):
            resp = func(*args, **kwargs)
            if not isinstance(resp, ModbusIOException):
                return resp

            logger.warning(f"[RTU] Attempt {attempt}/{self.retries} failed")

        raise ConnectionError("RTU communication failed after retries")

    # ------------------------------------------------------
    # READ
    # ------------------------------------------------------
    def read_input_registers(self, address, count, slave=1):
        return self._retry(
            self.client.read_input_registers,
            address,
            count,
            slave=slave
        )

    def read_holding_registers(self, address, count, slave=1):
        return self._retry(
            self.client.read_holding_registers,
            address,
            count,
            slave=slave
        )

    # ------------------------------------------------------
    # WRITE (MATCH BASETRANSPORT)
    # ------------------------------------------------------
    def write_single_register(self, address, value, slave=1):
        return self._retry(
            self.client.write_register,
            address,
            value,
            slave=slave
        )

    def write_multiple_registers(self, address, values, slave=1):
        return self._retry(
            self.client.write_registers,
            address,
            values,
            slave=slave
        )
