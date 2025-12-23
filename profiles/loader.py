# datalogger_core/profiles/profile_api.py

import yaml
from pathlib import Path
from typing import Dict, Any, List, Optional


class ProfileLoader:

    def __init__(self, base_path: Optional[str] = None):
        self.base_path = Path(base_path) if base_path else Path(__file__).parent
    # FILE RESOLVE
    def _resolve(self, filename: str) -> Path:
        p = Path(filename)
        if p.exists():
            return p
        p2 = self.base_path / filename
        if p2.exists():
            return p2
        raise FileNotFoundError(f"Profile not found: {filename}")
    # PROJECT PROFILE
    def load_project(self, filename: str) -> Dict[str, Any]:
        path = self._resolve(filename)
        with open(path, "r", encoding="utf-8") as f:
            data = yaml.safe_load(f) or {}

        if "project" not in data:
            raise ValueError("Invalid project profile: missing 'project'")

        self.validate_project(data["project"])
        data["__source_path"] = str(path)
        return data

    def save_project(self, data: Dict[str, Any]) -> None:
        path = Path(data.get("__source_path"))
        if not path:
            raise ValueError("Missing __source_path, cannot save")

        data = dict(data)
        data.pop("__source_path", None)

        with open(path, "w", encoding="utf-8") as f:
            yaml.safe_dump(data, f, allow_unicode=True, sort_keys=False)

    def validate_project(self, project: Dict[str, Any]) -> None:
        required = [
            "name", "location", "capacity_kwp",
            "ac_capacity_kw", "inverter_count", "inverters"
        ]
        for k in required:
            if k not in project:
                raise ValueError(f"Project missing key: {k}")

        if not isinstance(project["inverters"], list):
            raise ValueError("project.inverters must be list")

        for inv in project["inverters"]:
            self._validate_inverter(inv)

    def _validate_inverter(self, inv: Dict[str, Any]) -> None:
        required = [
            "inverter_index",
            "inverter_id",
            "serial_number",
            "brand",
            "model",
            "mppt_count",
            "string_count",
            "inverter_state",
        ]
        for k in required:
            if k not in inv:
                raise ValueError(f"Inverter missing key: {k}")

    # =========================
    # QUERY HELPERS
    # =========================
    def get_active_inverters(self, project: Dict[str, Any]) -> List[Dict[str, Any]]:
        return [
            inv for inv in project["inverters"]
            if inv.get("inverter_state") == "active"
        ]

    def find_inverter_by_id(
        self, project: Dict[str, Any], inverter_id: int
    ) -> Optional[Dict[str, Any]]:
        for inv in project["inverters"]:
            if inv.get("inverter_id") == inverter_id:
                return inv
        return None

    def find_inverter_by_index(
        self, project: Dict[str, Any], inverter_index: int
    ) -> Optional[Dict[str, Any]]:
        for inv in project["inverters"]:
            if inv.get("inverter_index") == inverter_index:
                return inv
        return None

    # =========================
    # MAPPING PROFILE (LEGACY)
    # =========================
    def load_mapping(self, brand: str, model: str) -> Dict[str, Any]:
        fname = f"{brand.lower()}_{model.lower()}.yaml"
        path = self._resolve(fname)
        with open(path, "r", encoding="utf-8") as f:
            return yaml.safe_load(f)
