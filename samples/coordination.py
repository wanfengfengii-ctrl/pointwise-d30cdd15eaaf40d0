"""Frozen-frame inputs for joint sampling and a V1 readback check."""
from collections import Counter
from contextlib import closing
from fractions import Fraction
import json

from analysis.risk import plan, publish
from exchange.coord_intake import receive_coord
from exchange.intake import receive
from exchange.issue import issue, issue_coord, resolve_ticket
from ledger import open_center, open_station
from register.revisions import revise
from report.retrieve import read, read_plan
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


def _expected_samples(layers, quota):
    """Every complete sample the original layered draw can produce, one entry each."""
    from itertools import combinations

    samples = [dict()]
    for name, entries in layers.items():
        locals_ = tuple(local for local, _ in entries)
        samples = [
            {**base, name: frozenset(picked)}
            for base in samples
            for picked in combinations(locals_, quota)
        ]
    return samples


def check_joint(verbose=True):
    """Verify each joint plan against the original layered draw for every case."""
    results = []
    for case_name, (frames, quota, _distinct, _expected) in CASES.items():
        with closing(open_station(":memory:")) as left_station, \
                closing(open_station(":memory:")) as right_station, \
                closing(open_center(":memory:")) as center:
            left_issue = issue_coord(left_station, build_design(left_station, SHIFTS[0], frames[0], quota))
            right_issue = issue_coord(right_station, build_design(right_station, SHIFTS[1], frames[1], quota))
            left = receive_coord(center, *left_issue)
            right = receive_coord(center, *right_issue)
            signed = plan(center, left, right)
            if signed is None:
                results.append({"case": case_name, "plan": "none"})
                if verbose:
                    print(json.dumps({"case": case_name, "plan": "none"}, ensure_ascii=False, sort_keys=True))
                continue
            if read_plan(center, signed["plan_id"]) != signed:
                raise AssertionError(f"{case_name}: signed plan does not read back unchanged")
            left_counter = Counter()
            right_counter = Counter()
            for ticket in range(signed["N"]):
                l_pull = resolve_ticket(left_station, signed["left_card"], ticket)
                r_pull = resolve_ticket(right_station, signed["right_card"], ticket)
                left_counter[frozenset(l_pull)] += 1
                right_counter[frozenset(r_pull)] += 1
                if _shared_keys(left_station, l_pull) & _shared_keys(right_station, r_pull):
                    raise AssertionError(f"{case_name}: ticket {ticket} shares a specimen across shifts")
            _assert_uniform(case_name, "left", left_counter, signed["N"], quota, frames[0])
            _assert_uniform(case_name, "right", right_counter, signed["N"], quota, frames[1])
            entry = {"case": case_name, "plan": "signed", "N": signed["N"],
                     "left_samples": len(left_counter), "right_samples": len(right_counter)}
            results.append(entry)
            if verbose:
                print(json.dumps(entry, ensure_ascii=False, sort_keys=True))
    return results


def _assert_uniform(case, side, counter, domain, quota, layers):
    expected = Counter()
    for sample in _expected_samples(layers, quota):
        expected[frozenset(local for picked in sample.values() for local in picked)] += 1
    if set(counter) != set(expected):
        raise AssertionError(f"{case}/{side}: sample set differs from original layered draw")
    frequencies = set(counter.values())
    if len(frequencies) != 1:
        raise AssertionError(f"{case}/{side}: samples are not equiprobable: {sorted(frequencies)}")
    if domain % len(expected) != 0:
        raise AssertionError(f"{case}/{side}: domain {domain} is not a uniform multiple of {len(expected)}")


def _shared_keys(station, local_ids):
    keys = set()
    for local_id in local_ids:
        row = station.execute(
            "SELECT shared_key FROM specimens WHERE local_id = ?", (local_id,)
        ).fetchone()
        if row is not None and row[0] is not None:
            keys.add(row[0])
    return keys


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
    check_joint()
