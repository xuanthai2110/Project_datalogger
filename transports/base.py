# datalogger_core/transport/base.py
from abc import ABC, abstractmethod

class BaseTransport(ABC):
    """
    Base class for all transport layers (Modbus RTU, Modbus TCP, Serial…)
    """

    @abstractmethod
    def connect(self):
        pass

    @abstractmethod
    def close(self):
        pass

    @abstractmethod
    def read_holding_registers(self, address: int, count: int, unit=1):
        pass

    @abstractmethod
    def read_input_registers(self, address: int, count: int, unit=1):
        pass

    @abstractmethod
    def write_single_register(self, address: int, value: int, unit=1):
        pass

    @abstractmethod
    def write_multiple_registers(self, address: int, values: list[int], unit=1):
        pass
