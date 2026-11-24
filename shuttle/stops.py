"""East stops from the resort's 2026/27 VillageMap_2026-27_WEB.png.

Pins are checked against the resort map and local stop-location corrections.
The route follows road centre lines; curbside pins are not snapped to it.
"""
import json
import math
from functools import lru_cache
from pathlib import Path

OUR_STOP = "E6"

# Loop order. ``label``: name drawn on the map (others show on tap).
STOPS = (
    {"id": "VDL", "name": "Village Day Lodge", "lat": 50.88405, "lon": -119.88640, "label": True},
    {"id": "E1", "name": "Village Walk", "lat": 50.884250910130966, "lon": -119.8781202809673, "label": True},
    {"id": "E2", "name": "McGillvray Creek", "lat": 50.8838549240345, "lon": -119.87570532056989, "label": False},
    {"id": "E3", "name": "Orient Chair", "lat": 50.882981745393366, "lon": -119.87266369552958, "label": True},
    {"id": "E4", "name": "P5 Entrance", "lat": 50.882554, "lon": -119.866334, "label": True},
    {"id": "E5", "name": "Switchback Creek", "lat": 50.882704, "lon": -119.862778, "label": "left"},
    {"id": "E6", "name": "Altitude", "lat": 50.884889981815334, "lon": -119.8647617402362, "label": True},
    {"id": "E7", "name": "Lookout Ridge", "lat": 50.88345, "lon": -119.86200, "label": True},
    {"id": "E8", "name": "Stones Throw", "lat": 50.883206, "lon": -119.867700, "label": False, "side": "left"},
    {"id": "E9", "name": "Trappers Landing", "lat": 50.88367244784997, "lon": -119.87362588960312, "label": False},
    {"id": "E10", "name": "Ice Rink", "lat": 50.88465648796544, "lon": -119.8783275104009, "label": "left"},
)


@lru_cache(maxsize=1)
def route():
    """[[lat, lon], ...] of the East loop, VDL round to VDL."""
    path = Path(__file__).with_name("data") / "east_loop.json"
    return json.loads(path.read_text())["route"]


def distance_m(lat1, lon1, lat2, lon2):
    r = 6371000.0
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dp, dl = p2 - p1, math.radians(lon2 - lon1)
    a = math.sin(dp / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2
    return 2 * r * math.asin(math.sqrt(a))


def nearest(lat, lon):
    """(stop, metres) for the closest stop."""
    best = min(STOPS, key=lambda s: distance_m(lat, lon, s["lat"], s["lon"]))
    return best, distance_m(lat, lon, best["lat"], best["lon"])
