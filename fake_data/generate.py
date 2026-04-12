#!/usr/bin/env python3
"""Deterministic fake labeling-data generator.

Produces a CSV with ~25 varied columns so different example flywheel.yaml
configs can pick and choose which columns to render in the labeling UI.
Run via `python fake_data/generate.py [out.csv]` — defaults to
fake_data/sample.csv. Reproducible (seeded).
"""
from __future__ import annotations

import csv
import os
import random
import sys
from datetime import datetime, timedelta

SEED = 42
N_ROWS = 40

REPORTERS = ["customer", "mechanic", "dealership", "fleet_operator"]
REGIONS = ["NA", "EU", "APAC", "LATAM"]
MAKES = ["Acme", "Brixton", "Cinder", "Delmar", "Emberline", "Falcor"]
MODELS = ["Starlight", "Ridgerunner", "Vortex", "Harbinger", "Nimbus", "Orion"]
ENGINES = ["gasoline", "diesel", "ev", "hybrid"]
ROADS = ["highway", "urban", "rural", "offroad", "parking_lot"]
WEATHERS = ["clear", "rain", "snow", "fog", "hail"]
CATEGORIES = ["safety", "comfort", "performance", "cosmetic", "reliability"]
COMPONENTS = [
    "BRK-A1", "BRK-B2", "STR-A1", "STR-B2", "PWR-A1", "PWR-B2",
    "SUS-A1", "HVAC-A1", "ADAS-A1", "BAT-A1", "BODY-A1",
]

NARRATIVES = [
    "While driving at {speed} km/h on a {road} road in {weather} weather, "
    "the {system} began to {symptom}. The vehicle {outcome}. No prior warning "
    "lights were observed. Reporter requests urgent review.",
    "Customer reports that the {system} has been intermittently failing for the "
    "past {weeks} weeks. The issue presents as a {symptom} and appears to worsen "
    "when {condition}. The vehicle has {mileage} km on the odometer.",
    "During routine operation the {system} produced an unusual {symptom}. The "
    "driver pulled over safely. Upon restart the issue {resolution}. No injuries "
    "or property damage reported.",
    "Repeated occurrence of {symptom} from the {system} during {condition}. "
    "Dealership has inspected twice with no fault code recorded. Customer "
    "requests escalation due to safety concern.",
]

SYMPTOMS = [
    "shudder", "grinding noise", "burning smell", "sudden loss of power",
    "unresponsive pedal", "erratic reading", "fluid leak", "smoke",
]
OUTCOMES = [
    "stopped safely", "required a tow", "continued with reduced performance",
    "was driven home cautiously",
]
CONDITIONS = ["cold starts", "highway speeds", "heavy braking", "wet weather"]
RESOLUTIONS = ["resolved temporarily", "recurred immediately", "did not recur"]
SYSTEMS_TEXT = [
    "braking system", "steering column", "powertrain", "suspension",
    "HVAC unit", "ADAS module", "infotainment display", "battery pack",
]

FOLLOWUPS = [
    "Awaiting customer callback.",
    "Parts ordered.",
    "No further action.",
    "Escalated to engineering.",
    "",
]


def make_narrative(rng: random.Random) -> str:
    return rng.choice(NARRATIVES).format(
        speed=rng.randint(20, 120),
        road=rng.choice(ROADS),
        weather=rng.choice(WEATHERS),
        system=rng.choice(SYSTEMS_TEXT),
        symptom=rng.choice(SYMPTOMS),
        outcome=rng.choice(OUTCOMES),
        weeks=rng.randint(1, 12),
        condition=rng.choice(CONDITIONS),
        mileage=rng.randint(5000, 250000),
        resolution=rng.choice(RESOLUTIONS),
    )


COLUMNS = [
    "id", "submitted_at", "reporter_type", "region", "title", "narrative",
    "vehicle_make", "vehicle_model", "vehicle_year", "vehicle_mileage",
    "engine_type", "has_injury", "reported_severity", "complaint_category",
    "component_codes", "fuel_level_pct", "ambient_temp_c",
    "road_condition", "weather", "speed_kmh", "recall_related",
    "dealer_id", "customer_email", "followup_notes", "priority_hint",
]


def main(out_path: str) -> None:
    rng = random.Random(SEED)
    base = datetime(2024, 1, 1)
    rows = []
    for i in range(1, N_ROWS + 1):
        rows.append({
            "id": i,
            "submitted_at": (base + timedelta(days=i * 2, hours=rng.randint(0, 23))).isoformat(),
            "reporter_type": rng.choice(REPORTERS),
            "region": rng.choice(REGIONS),
            "title": f"{rng.choice(SYMPTOMS).title()} in {rng.choice(SYSTEMS_TEXT)}",
            "narrative": make_narrative(rng),
            "vehicle_make": rng.choice(MAKES),
            "vehicle_model": rng.choice(MODELS),
            "vehicle_year": rng.randint(2015, 2024),
            "vehicle_mileage": rng.randint(1000, 250000),
            "engine_type": rng.choice(ENGINES),
            "has_injury": rng.choice([0, 1]),
            "reported_severity": rng.randint(1, 5),
            "complaint_category": rng.choice(CATEGORIES),
            "component_codes": ",".join(rng.sample(COMPONENTS, k=rng.randint(1, 3))),
            "fuel_level_pct": rng.randint(0, 100),
            "ambient_temp_c": rng.randint(-20, 40),
            "road_condition": rng.choice(ROADS),
            "weather": rng.choice(WEATHERS),
            "speed_kmh": rng.randint(0, 140),
            "recall_related": rng.choice([0, 1]),
            "dealer_id": f"D{rng.randint(1000, 9999)}",
            "customer_email": f"user{i}@example.com",
            "followup_notes": rng.choice(FOLLOWUPS),
            "priority_hint": rng.randint(1, 5),
        })

    os.makedirs(os.path.dirname(out_path) or ".", exist_ok=True)
    with open(out_path, "w", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=COLUMNS)
        writer.writeheader()
        writer.writerows(rows)
    print(f"wrote {len(rows)} rows × {len(COLUMNS)} cols → {out_path}")


if __name__ == "__main__":
    out = sys.argv[1] if len(sys.argv) > 1 else "fake_data/sample.csv"
    main(out)
