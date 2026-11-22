"""Frozen-frame inputs for joint sampling and a V1 readback check."""
from contextlib import closing
from fractions import Fraction
import json

from analysis.risk import publish
from exchange.intake import receive
from exchange.issue import issue
from ledger import open_center, open_station
from register.revisions import revise
from report.retrieve import read
from sampling.design import freeze
from sampling.protocols import register_protocol

SHIFTS = ("甲班", "乙班")
ONE_RACK = (
    {"rack": (("A-x", "x"), ("A-a", None), ("A-b", None))},
    {"rack": (("B-x", "x"), ("B-c", None), ("B-d", None))},
)
LAYERED = (
    {
        "lower": ONE_RACK[0]["rack"],
        "upper": (("A-y", "y"), ("A-u", None)),
    },
    {
        "lower": (("B-x", "x"), ("B-c", None)),
        "upper": (("B-y", "y"), ("B-v", None), ("B-w", None), ("B-z", None)),
    },
)
LAYER_CONFLICT = (
    {"rack": (("A-x", "x"), ("A-y", "y"))},
    {
        "lower": LAYERED[1]["lower"],
        "upper": (("B-y", "y"), ("B-v", None)),
    },
)

# The last two columns are the distinct count and expected V1 condition mass.
CASES = {
    "one_each": (ONE_RACK, 1, 2, Fraction(8, 9)),
    "two_each": (ONE_RACK, 2, 4, Fraction(5, 9)),
    "layered": (LAYERED, 1, 4, Fraction(35, 48)),
    "layer_conflict": (LAYER_CONFLICT, 1, 3, Fraction(1, 2)),
    "empty": (ONE_RACK, 0, 0, Fraction(1)),
}


def build_design(db, shift_id, layers, quota):
    """Load one sample frame in an existing station and return its design ID."""
    readings = {local: (shared, 0) for layer in layers.values() for local, shared in layer}
    revision = revise(db, shift_id, readings)
    register_protocol(db, shift_id, "sample", 10)
    members = {name: tuple(local for local, _ in layer) for name, layer in layers.items()}
    return freeze(db, shift_id, revision, "sample", members, dict.fromkeys(members, quota))


def check_v1():
    for case_name, (frames, quota, distinct_count, expected) in CASES.items():
        wires = []
        for shift, layers in zip(SHIFTS, frames):
            with closing(open_station(":memory:")) as station:
                wires.append(issue(station, build_design(station, shift, layers, quota)))
        with closing(open_center(":memory:")) as center:
            left, right = (receive(center, *wire) for wire in wires)
            signed = publish(center, left, right, distinct_count)
            result = read(center, signed.record.report_id)
            if result != signed or result.record.condition_mass != expected:
                raise AssertionError(f"{case_name}: V1 expected condition mass {expected}, got {result!r}")
            if result.record.zero_probability != 1 or tuple(p.receipt_id for p in result.parents) != (left, right):
                raise AssertionError(f"{case_name}: V1 provenance or zero probability changed")
            print(json.dumps({
                "case": case_name,
                "distinct_count": distinct_count,
                "v1_condition_mass": str(result.record.condition_mass),
                "v1_zero_probability": str(result.record.zero_probability),
            }, sort_keys=True))


if __name__ == "__main__":
    check_v1()
