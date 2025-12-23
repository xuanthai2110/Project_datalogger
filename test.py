from profiles.loader import ProfileLoader
import json

def main():
    loader = ProfileLoader()

    # 1. Load project profile
    profile = loader.load_project("profile_project.yaml")
    project = profile["project"]

    print("=== PROJECT INFO ===")
    print("Name:", project["name"])
    print("Location:", project["location"])
    print("Inverter count (config):", project["inverter_count"])
    print()

    # 2. List all inverters
    print("=== ALL INVERTERS ===")
    for inv in project["inverters"]:
        print(
            f"index={inv['inverter_index']} | "
            f"id={inv['inverter_id']} | "
            f"sn={inv['serial_number']} | "
            f"state={inv['inverter_state']}"
        )

    print()

    # 3. Active inverters
    active = loader.get_active_inverters(project)
    print("=== ACTIVE INVERTERS ===")
    for inv in active:
        print(
            f"[ACTIVE] id={inv['inverter_id']} "
            f"sn={inv['serial_number']}"
        )

    print()

    # 4. Find inverter by inverter_id
    inv_id_1 = loader.find_inverter_by_id(project, 1)
    print("=== FIND inverter_id = 1 ===")
    print(json.dumps(inv_id_1, indent=2, ensure_ascii=False))

    print()

    # 5. Find inverter by inverter_index
    inv_index_5 = loader.find_inverter_by_index(project, 5)
    print("=== FIND inverter_index = 5 ===")
    print(json.dumps(inv_index_5, indent=2, ensure_ascii=False))


if __name__ == "__main__":
    main()
