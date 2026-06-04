"""
mock_ir.py — Fallback mock for iRacing SDK when iRacing is not running.

Provides enough of the irsdk.IRSDK interface to let the dashboard
and engine start without crashing. All telemetry values return safe
defaults. Useful for testing the web dashboard and commentary pipeline
without a live iRacing session.
"""

import time
import random


class MockIR:
    """Minimal iRacing SDK mock that returns safe default values."""

    def __init__(self):
        self.is_initialized = True
        self.is_connected = True
        self._session_info_str = self._build_session_info()
        self._vars = self._build_default_vars()
        print("[MockIR] Running in MOCK mode — no live iRacing data.")

    # ── Telemetry var access ──
    def __getitem__(self, key):
        return self._vars.get(key, 0)

    def __contains__(self, key):
        return key in self._vars

    # ── SDK lifecycle ──
    def startup(self):
        pass

    def shutdown(self):
        pass

    def freeze_var_buffer_latest(self):
        """Simulate a telemetry tick — nudge session time forward."""
        self._vars["SessionTime"] = time.time() % 3600
        self._vars["SessionTimeRemain"] = max(0, 1800 - self._vars["SessionTime"])

    # ── Session info (YAML string) ──
    @property
    def session_info(self):
        return self._session_info_str

    # ── Default telemetry values ──
    def _build_default_vars(self):
        num_cars = 20
        return {
            "SessionTime": 0.0,
            "SessionTimeRemain": 1800.0,
            "SessionFlags": 0x10000004,  # GREEN flag bits
            "SessionNum": 0,
            "SessionLapsRemain": 30,
            "CarIdxPosition": list(range(1, num_cars + 1)) + [0] * (64 - num_cars),
            "CarIdxClassPosition": list(range(1, num_cars + 1)) + [0] * (64 - num_cars),
            "CarIdxLap": [3] * num_cars + [0] * (64 - num_cars),
            "CarIdxLapCompleted": [2] * num_cars + [0] * (64 - num_cars),
            "CarIdxLapDistPct": [round(random.uniform(0.0, 1.0), 4) for _ in range(num_cars)] + [0.0] * (64 - num_cars),
            "CarIdxBestLapTime": [round(random.uniform(78.0, 85.0), 3) for _ in range(num_cars)] + [0.0] * (64 - num_cars),
            "CarIdxLastLapTime": [round(random.uniform(79.0, 86.0), 3) for _ in range(num_cars)] + [0.0] * (64 - num_cars),
            "CarIdxTrackSurface": [3] * num_cars + [-1] * (64 - num_cars),  # 3 = OnTrack
            "CarIdxTrackSurfaceMaterial": [1] * num_cars + [0] * (64 - num_cars),
            "CarIdxOnPitRoad": [False] * 64,
            "CarIdxEstTime": [round(random.uniform(78.0, 86.0), 3) for _ in range(num_cars)] + [0.0] * (64 - num_cars),
            "CarIdxF2Time": [0.0] * 64,
            "CarIdxSteer": [0.0] * 64,
            "IsOnTrack": True,
            "IsOnTrackCar": True,
            "PlayerCarIdx": 0,
            "SessionState": 4,  # StateRacing
        }

    def _build_session_info(self):
        """Build a minimal YAML session info string with mock drivers."""
        drivers_yaml = ""
        teams = ["Buzz Racing", "Apex Motorsport", "Vortex Racing", "Thunder GP",
                 "Eclipse Racing", "Nitro Speed", "Turbo Elite", "Phantom Racing"]
        first_names = ["Alex", "Sam", "Jordan", "Taylor", "Morgan", "Riley", "Casey", "Drew",
                       "Blake", "Avery", "Quinn", "Reese", "Dakota", "Skyler", "Jamie", "Rowan",
                       "Hayden", "Parker", "Finley", "Emery"]
        last_names = ["Walker", "Mitchell", "Carter", "Brooks", "Torres", "Reed", "Bell",
                      "Cooper", "Morgan", "Bailey", "Rivera", "Howard", "Ward", "Price",
                      "Jenkins", "Perry", "Russell", "Griffin", "Hayes", "Sullivan"]

        for i in range(20):
            name = f"{first_names[i]} {last_names[i]}"
            team = teams[i % len(teams)]
            number = str(i + 1)
            drivers_yaml += f"""   - CarIdx: {i}
     UserName: {name}
     TeamName: {team}
     CarNumber: "{number}"
     CarScreenName: Mock Car
     IRating: {random.randint(1200, 4500)}
     LicLevel: {random.randint(1, 4)}
     LicString: "A {random.randint(1,4)}.{random.randint(0,9)}{random.randint(0,9)}"
     IsSpectator: 0
     CarIsPaceCar: 0
"""

        return f"""---
WeekendInfo:
 TrackName: Mock Speedway
 TrackID: 1
 TrackLength: 3.20 km
 TrackDisplayName: Mock International Circuit
 TrackCity: Testville
 TrackCountry: MockLand
 TrackDisplayShortName: Mock GP
 EventType: Race
 Category: Road
 SessionID: 999999
 SubSessionID: 888888
 SeasonID: 1234
 SeriesID: 5678
 WeekendOptions:
  NumStarters: 20
  StandingStart: 0
DriverInfo:
 DriverCarIdx: 0
 Drivers:
{drivers_yaml}
SessionInfo:
 Sessions:
  - SessionNum: 0
    SessionType: Race
    SessionLaps: 30
    ResultsPositions:
"""
