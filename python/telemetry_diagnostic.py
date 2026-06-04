"""
Telemetry Diagnostic — Sector Says What
========================================
Run this while connected to an iRacing session to verify which
telemetry variables from the official API are readable.

Usage:
    python telemetry_diagnostic.py

Outputs:
    - Console report of all variables (accessible / missing / error)
    - telemetry_diagnostic_report.json  (full results saved to disk)
"""

import os
import sys
import json
import time
from datetime import datetime

# --- FMOD path fix (same as engine) so irsdk import doesn't choke ---
from pathlib import Path
PROJECT_ROOT = Path(__file__).resolve().parent.parent
FMOD_PATH = str(PROJECT_ROOT / "audio" / "fmod")
if os.path.isdir(FMOD_PATH):
    os.environ["PATH"] = FMOD_PATH + os.pathsep + os.environ.get("PATH", "")

import irsdk

# ============================================================
# COMPLETE VARIABLE CATALOG  (from telemetry_11_23_15.pdf)
# ============================================================

# --- Always-available live variables (Appendix A, pages 1-5) ---
CORE_LIVE_VARS = [
    # Environmental
    "AirDensity", "AirPressure", "AirTemp",
    # Vehicle basics
    "Alt", "Brake", "BrakeRaw", "Clutch", "Gear", "RPM", "Speed",
    "Throttle", "ThrottleRaw",
    # Camera
    "CamCameraNumber", "CamCameraState", "CamCarIdx", "CamGroupNumber",
    # System
    "CpuUsageBG", "DisplayUnits", "DriverMarker",
    "EnterExitReset", "FrameRate",
    # Driver change
    "DCDriversSoFar", "DCLapStatus",
    # Engine
    "EngineWarnings", "FuelLevel", "FuelLevelPct", "FuelPress",
    "FuelUsePerHour", "ManifoldPress", "OilLevel", "OilPress", "OilTemp",
    "Voltage", "WaterLevel", "WaterTemp",
    # Disk logging
    "IsDiskLoggingActive", "IsDiskLoggingEnabled",
    # State
    "IsInGarage", "IsOnTrack", "IsOnTrackCar", "IsReplayPlaying",
    # Lap data
    "Lap", "LapBestLap", "LapBestLapTime", "LapBestNLapLap",
    "LapBestNLapTime", "LapCurrentLapTime",
    "LapDeltaToBestLap", "LapDeltaToBestLap_DD", "LapDeltaToBestLap_OK",
    "LapDeltaToOptimalLap", "LapDeltaToOptimalLap_DD", "LapDeltaToOptimalLap_OK",
    "LapDeltaToSessionBestLap", "LapDeltaToSessionBestLap_DD", "LapDeltaToSessionBestLap_OK",
    "LapDeltaToSessionLastlLap", "LapDeltaToSessionLastlLap_DD", "LapDeltaToSessionLastlLap_OK",
    "LapDeltaToSessionOptimalLap", "LapDeltaToSessionOptimalLap_DD", "LapDeltaToSessionOptimalLap_OK",
    "LapDist", "LapDistPct", "LapLasNLapSeq",
    "LapLastLapTime", "LapLastNLapTime",
    # Location
    "Lat", "Lon",
    # Acceleration
    "LatAccel", "LongAccel", "VertAccel",
    # Orientation
    "Pitch", "PitchRate", "Roll", "RollRate", "Yaw", "YawNorth", "YawRate",
    # Pit
    "OnPitRoad", "PitOptRepairLeft", "PitRepairLeft",
    "PitSvFlags", "PitSvFuel",
    "PitSvLFP", "PitSvLRP", "PitSvRFP", "PitSvRRP",
    # Player position
    "PlayerCarClassPosition", "PlayerCarPosition",
    # Race
    "RaceLaps",
    # Radio
    "RadioTransmitCarIdx", "RadioTransmitFrequencyIdx", "RadioTransmitRadioIdx",
    # Weather
    "FogLevel", "RelativeHumidity", "Skies", "TrackTemp", "TrackTempCrew",
    "WeatherType", "WindDir", "WindVel",
    # Replay
    "ReplayFrameNum", "ReplayFrameNumEnd", "ReplayPlaySlowMotion",
    "ReplayPlaySpeed", "ReplaySessionNum", "ReplaySessionTime",
    # Session
    "SessionFlags", "SessionLapsRemain", "SessionNum", "SessionState",
    "SessionTime", "SessionTimeRemain", "SessionUniqueID",
    # Steering
    "SteeringWheelAngle", "SteeringWheelAngleMax",
    "SteeringWheelPctDamper", "SteeringWheelPctTorque",
    "SteeringWheelPctTorqueSign", "SteeringWheelPctTorqueSignStops",
    "SteeringWheelPeakForceNm", "SteeringWheelTorque",
    # Shift
    "ShiftGrindRPM", "ShiftIndicatorPct", "ShiftPowerPct",
    # Velocity
    "VelocityX", "VelocityY", "VelocityZ",
]

# --- Per-car array variables (page 9 + post-2015 additions used by engine) ---
CAR_IDX_VARS = [
    # Official from PDF (page 9)
    "CarIdxClassPosition", "CarIdxEstTime", "CarIdxF2Time",
    "CarIdxGear", "CarIdxLap", "CarIdxLapDistPct",
    "CarIdxOnPitRoad", "CarIdxPosition", "CarIdxRPM",
    "CarIdxSteer", "CarIdxTrackSurface",
    # Post-2015 additions (used by engine — verify availability)
    "CarIdxBestLapTime", "CarIdxLastLapTime",
    "CarIdxContact", "CarIdxYaw", "CarIdxF2Flags",
    "CarIdxSpeed", "CarIdxLapCompleted",
]

# --- Car-specific sensor variables (pages 5-9, may not exist for all cars) ---
OPTIONAL_CAR_VARS = [
    # Suspension
    "CFshockDefl", "CFshockVel", "CRshockDefl", "CRshockVel",
    # In-car adjustments
    "dcABS", "dcAntiRollFront", "dcAntiRollRear", "dcBoostLevel",
    "dcBrakeBias", "dcDiffEntry", "dcDiffExit", "dcDiffMiddle",
    "dcEngineBraking", "dcEnginePower", "dcFuelMixture",
    "dcRevLimiter", "dcThrottleShape", "dcTractionControl",
    "dcTractionControl2", "dcTractionControlToggle",
    "dcWeightJackerLeft", "dcWeightJackerRight",
    "dcWingFront", "dcWingRear",
    # Pitstop adjustments
    "dpFNOMKnobSetting", "dpFUFangleIndex", "dpFWingAngle", "dpFWingIndex",
    "dpLrWedgeAdj", "dpPSSetting", "dpQtape", "dpRBarSetting",
    "dpRFTruckarmP1Dz", "dpRRDamperPerchOffsetm", "dpRrPerchOffsetm",
    "dpRrWedgeAdj", "dpRWingAngle", "dpRWingIndex", "dpRWingSetting",
    "dpTruckarmP1Dz", "dpWedgeAdj",
]

# --- Per-corner tire/brake variables (pages 7-9) ---
TIRE_CORNERS = ["LF", "LR", "RF", "RR"]
TIRE_SUFFIXES = [
    "brakeLinePress", "coldPressure", "shockDefl", "shockVel", "speed",
    "tempCL", "tempCM", "tempCR",
    "wearL", "wearM", "wearR",
]
TIRE_VARS = [f"{corner}{suffix}" for corner in TIRE_CORNERS for suffix in TIRE_SUFFIXES]

# --- Session YAML sections (Appendix B) ---
YAML_SECTIONS = [
    "WeekendInfo", "SessionInfo", "DriverInfo",
    "CameraInfo", "RadioInfo", "SplitTimeInfo",
    "QualifyResultsInfo",
]


def try_read(ir_conn, var_name):
    """Attempt to read a single telemetry variable. Returns (value, error_str|None)."""
    try:
        val = ir_conn[var_name]
        if val is None:
            # None can be legitimate (e.g. PitRepairLeft when not pitting)
            # but also means the var doesn't exist for this session/car.
            # Mark as "ok_none" so we don't count it as a hard failure.
            return None, None
        return val, None
    except KeyError:
        return None, "KeyError (not available)"
    except Exception as e:
        return None, str(e)


def format_value(val):
    """Compact display of a telemetry value."""
    if isinstance(val, (list, tuple)):
        # Show first 5 entries of arrays
        preview = val[:5]
        suffix = f"  ...({len(val)} total)" if len(val) > 5 else ""
        return str(preview) + suffix
    if isinstance(val, float):
        return f"{val:.4f}"
    if isinstance(val, dict):
        return f"<dict with {len(val)} keys>"
    return str(val)


def run_diagnostic():
    print("=" * 60)
    print("  SECTOR SAYS WHAT — Telemetry Diagnostic")
    print("=" * 60)

    # Connect
    ir_conn = irsdk.IRSDK()
    if not ir_conn.is_initialized:
        ir_conn.startup()

    dots = 0
    while not ir_conn.is_connected:
        dots = (dots % 5) + 1
        sys.stdout.write(f"\rWaiting for iRacing{'.' * dots:<5}")
        sys.stdout.flush()
        time.sleep(0.5)

    print("\rConnected to iRacing.                ")
    time.sleep(1)  # let telemetry stabilize
    ir_conn.freeze_var_buffer_latest()

    results = {
        "timestamp": datetime.now().isoformat(),
        "core_live": {},
        "car_idx_arrays": {},
        "optional_car": {},
        "tire_vars": {},
        "yaml_sections": {},
        "summary": {},
    }

    # ---- CORE LIVE VARIABLES ----
    print(f"\n{'-' * 60}")
    print(f"  CORE LIVE VARIABLES ({len(CORE_LIVE_VARS)} vars)")
    print(f"{'-' * 60}")
    ok, fail = 0, 0
    for var in sorted(CORE_LIVE_VARS):
        val, err = try_read(ir_conn, var)
        if err:
            print(f"  [MISS] {var:<45} {err}")
            results["core_live"][var] = {"status": "missing", "error": err}
            fail += 1
        elif val is None:
            print(f"  [ OK ] {var:<45} = None (valid but empty)")
            results["core_live"][var] = {"status": "ok_none"}
            ok += 1
        else:
            print(f"  [ OK ] {var:<45} = {format_value(val)}")
            results["core_live"][var] = {"status": "ok", "value": format_value(val)}
            ok += 1
    results["summary"]["core_live"] = {"ok": ok, "missing": fail, "total": ok + fail}
    print(f"\n  Result: {ok}/{ok+fail} accessible")

    # ---- CAR IDX ARRAYS ----
    print(f"\n{'-' * 60}")
    print(f"  PER-CAR ARRAY VARIABLES ({len(CAR_IDX_VARS)} vars)")
    print(f"{'-' * 60}")
    ok, fail = 0, 0
    for var in sorted(CAR_IDX_VARS):
        val, err = try_read(ir_conn, var)
        if err:
            print(f"  [MISS] {var:<45} {err}")
            results["car_idx_arrays"][var] = {"status": "missing", "error": err}
            fail += 1
        else:
            arr_len = len(val) if isinstance(val, (list, tuple)) else "?"
            print(f"  [ OK ] {var:<45} = [{arr_len} entries] {format_value(val)}")
            results["car_idx_arrays"][var] = {"status": "ok", "entries": arr_len}
            ok += 1
    results["summary"]["car_idx_arrays"] = {"ok": ok, "missing": fail, "total": ok + fail}
    print(f"\n  Result: {ok}/{ok+fail} accessible")

    # ---- OPTIONAL CAR-SPECIFIC ----
    print(f"\n{'-' * 60}")
    print(f"  OPTIONAL CAR-SPECIFIC VARS ({len(OPTIONAL_CAR_VARS)} vars)")
    print(f"{'-' * 60}")
    ok, fail = 0, 0
    for var in sorted(OPTIONAL_CAR_VARS):
        val, err = try_read(ir_conn, var)
        if err:
            print(f"  [----] {var:<45} not available for this car")
            results["optional_car"][var] = {"status": "not_available", "error": err}
            fail += 1
        else:
            print(f"  [ OK ] {var:<45} = {format_value(val)}")
            results["optional_car"][var] = {"status": "ok", "value": format_value(val)}
            ok += 1
    results["summary"]["optional_car"] = {"ok": ok, "missing": fail, "total": ok + fail}
    print(f"\n  Result: {ok}/{ok+fail} accessible (car-dependent — missing is normal)")

    # ---- TIRE VARIABLES ----
    print(f"\n{'-' * 60}")
    print(f"  TIRE/BRAKE VARIABLES ({len(TIRE_VARS)} vars)")
    print(f"{'-' * 60}")
    ok, fail = 0, 0
    for var in sorted(TIRE_VARS):
        val, err = try_read(ir_conn, var)
        if err:
            print(f"  [----] {var:<45} not available")
            results["tire_vars"][var] = {"status": "not_available", "error": err}
            fail += 1
        else:
            print(f"  [ OK ] {var:<45} = {format_value(val)}")
            results["tire_vars"][var] = {"status": "ok", "value": format_value(val)}
            ok += 1
    results["summary"]["tire_vars"] = {"ok": ok, "missing": fail, "total": ok + fail}
    print(f"\n  Result: {ok}/{ok+fail} accessible (car-dependent)")

    # ---- YAML SESSION STRING SECTIONS ----
    print(f"\n{'-' * 60}")
    print(f"  SESSION YAML SECTIONS ({len(YAML_SECTIONS)} sections)")
    print(f"{'-' * 60}")
    ok, fail = 0, 0
    for section in YAML_SECTIONS:
        val, err = try_read(ir_conn, section)
        if err:
            print(f"  [MISS] {section:<45} {err}")
            results["yaml_sections"][section] = {"status": "missing", "error": err}
            fail += 1
        else:
            if isinstance(val, dict):
                keys = list(val.keys())[:8]
                print(f"  [ OK ] {section:<45} keys: {keys}")
            elif isinstance(val, list):
                print(f"  [ OK ] {section:<45} [{len(val)} entries]")
            else:
                print(f"  [ OK ] {section:<45} = {format_value(val)}")
            results["yaml_sections"][section] = {"status": "ok"}
            ok += 1
    results["summary"]["yaml_sections"] = {"ok": ok, "missing": fail, "total": ok + fail}
    print(f"\n  Result: {ok}/{ok+fail} accessible")

    # ---- DRIVER LIST PREVIEW ----
    print(f"\n{'-' * 60}")
    print(f"  DRIVER LIST PREVIEW")
    print(f"{'-' * 60}")
    try:
        driver_info = ir_conn["DriverInfo"]
        if isinstance(driver_info, dict):
            drivers = driver_info.get("Drivers", [])
            owner_idx = driver_info.get("DriverCarIdx", -1)
            print(f"  Your CarIdx: {owner_idx}")
            print(f"  Total drivers: {len(drivers)}")
            print(f"  {'Idx':<5} {'#':<5} {'Name':<30} {'Car':<25} {'iRating':<8}")
            print(f"  {'-'*5} {'-'*5} {'-'*30} {'-'*25} {'-'*8}")
            for d in drivers[:20]:
                marker = " <-- YOU" if d.get("CarIdx") == owner_idx else ""
                print(f"  {d.get('CarIdx','?'):<5} {str(d.get('CarNumber','')):<5} "
                      f"{d.get('UserName','?'):<30} {d.get('CarScreenNameShort', d.get('CarPath','')):<25} "
                      f"{d.get('IRating','?'):<8}{marker}")
            if len(drivers) > 20:
                print(f"  ... and {len(drivers) - 20} more")
    except Exception as e:
        print(f"  [ERROR] Could not read DriverInfo: {e}")

    # ---- BITFIELD FLAG CHECK ----
    print(f"\n{'-' * 60}")
    print(f"  SESSION FLAGS (bitfield decode)")
    print(f"{'-' * 60}")
    try:
        flags = ir_conn["SessionFlags"] or 0
        flag_names = {
            "checkered": 0x00000001, "white": 0x00000002,
            "green": 0x00000004, "yellow": 0x00000008,
            "red": 0x00000010, "blue": 0x00000020,
            "debris": 0x00000040, "crossed": 0x00000080,
            "yellowWaving": 0x00000100, "oneLapToGreen": 0x00000200,
            "greenHeld": 0x00000400, "tenToGo": 0x00000800,
            "fiveToGo": 0x00001000, "randomWaving": 0x00002000,
            "caution": 0x00004000, "cautionWaving": 0x00008000,
            "black": 0x00010000, "disqualify": 0x00020000,
            "servicible": 0x00040000, "furled": 0x00080000,
            "repair": 0x00100000,
            "startHidden": 0x10000000, "startReady": 0x20000000,
            "startSet": 0x40000000, "startGo": 0x80000000,
        }
        print(f"  Raw value: {flags} (0x{flags:08X})")
        active = [name for name, mask in flag_names.items() if flags & mask]
        print(f"  Active flags: {active if active else 'none'}")
    except Exception as e:
        print(f"  [ERROR] {e}")

    # ---- FINAL SUMMARY ----
    total_ok = sum(s["ok"] for s in results["summary"].values())
    total_all = sum(s["total"] for s in results["summary"].values())
    total_miss = total_all - total_ok

    print(f"\n{'=' * 60}")
    print(f"  SUMMARY")
    print(f"{'=' * 60}")
    print(f"  Core live vars:    {results['summary']['core_live']['ok']}/{results['summary']['core_live']['total']}")
    print(f"  Per-car arrays:    {results['summary']['car_idx_arrays']['ok']}/{results['summary']['car_idx_arrays']['total']}")
    print(f"  Optional car vars: {results['summary']['optional_car']['ok']}/{results['summary']['optional_car']['total']} (car-dependent)")
    print(f"  Tire/brake vars:   {results['summary']['tire_vars']['ok']}/{results['summary']['tire_vars']['total']} (car-dependent)")
    print(f"  YAML sections:     {results['summary']['yaml_sections']['ok']}/{results['summary']['yaml_sections']['total']}")
    print(f"  {'-' * 40}")
    print(f"  TOTAL ACCESSIBLE:  {total_ok}/{total_all}")
    print(f"{'=' * 60}")

    # ---- ENGINE COVERAGE CHECK ----
    # Variables the Sector Says What engine currently reads
    ENGINE_USES = {
        # Leaderboard & position
        "CarIdxPosition", "CarIdxLap", "CarIdxLapDistPct",
        "CarIdxLastLapTime", "CarIdxBestLapTime", "CarIdxTrackSurface",
        "CarIdxRPM", "CarIdxSpeed", "CarIdxF2Time", "CarIdxLapCompleted",
        # Collision & offtrack trackers
        "CarIdxContact", "CarIdxYaw", "CarIdxF2Flags",
        # Session
        "SessionFlags", "SessionTime", "SessionTimeRemain",
        "SessionLapsRemain", "SessionLapsTotal",
        # YAML metadata
        "DriverInfo", "WeekendInfo", "SessionInfo",
    }
    # Additional vars the engine COULD use for richer commentary
    UNTAPPED = {
        "FuelLevel", "FuelLevelPct", "FuelUsePerHour",
        "CarIdxOnPitRoad", "CarIdxGear", "CarIdxEstTime",
        "CarIdxClassPosition", "CarIdxSteer",
        "AirTemp", "TrackTemp", "WindVel", "RelativeHumidity",
        "PitRepairLeft", "PitOptRepairLeft", "PitSvFlags",
        "EngineWarnings", "OilTemp", "WaterTemp",
        "LatAccel", "LongAccel", "Speed",
        "Brake", "Throttle", "Gear", "SteeringWheelAngle",
        "PlayerCarPosition", "PlayerCarClassPosition",
        "LapCurrentLapTime", "LapDeltaToSessionBestLap",
    }

    print(f"\n{'-' * 60}")
    print(f"  ENGINE COVERAGE vs AVAILABLE TELEMETRY")
    print(f"{'-' * 60}")
    print(f"  Currently used by engine:     {len(ENGINE_USES)} vars")
    print(f"  Untapped (could enrich AI):   {len(UNTAPPED)} vars")
    untapped_available = []
    for var in sorted(UNTAPPED):
        val, err = try_read(ir_conn, var)
        status = "available" if not err else "N/A"
        if not err:
            untapped_available.append(var)
        print(f"    {status:<12} {var}")
    print(f"\n  {len(untapped_available)}/{len(UNTAPPED)} untapped vars are live & readable right now")

    # Save report
    report_path = os.path.join(os.path.dirname(__file__), "telemetry_diagnostic_report.json")
    try:
        # Convert any non-serializable values
        with open(report_path, "w", encoding="utf-8") as f:
            json.dump(results, f, indent=2, default=str)
        print(f"\n  Report saved: {report_path}")
    except Exception as e:
        print(f"\n  [WARN] Could not save report: {e}")

    print(f"\n  Done. You can now run the full engine (sector_says_engine.py)")
    print(f"  knowing which variables are accessible in this session.\n")


if __name__ == "__main__":
    run_diagnostic()
