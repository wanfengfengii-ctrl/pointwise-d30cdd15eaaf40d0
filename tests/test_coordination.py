from collections import Counter
from contextlib import closing
from dataclasses import replace

import sqlite3

from analysis.risk import plan
from exchange.intake import receive_coord
from exchange.issue import issue_coord, resolve_ticket
from exchange.joint import decode_card, encode_card
from ledger import InvalidInput, UnknownRecord, open_station
from register.revisions import revise
from report.retrieve import read_plan
from samples.coordination import (
    CASES, ONE_RACK, SHIFTS, build_design,
)
from sampling.design import freeze
from tests.fixtures import LedgerFixture, counts


class CoordFixture(LedgerFixture):
    def setUp(self):
        super().setUp()
        self.left_station = open_station(self.station_path.with_name('left.sqlite'))
        self.addCleanup(self.left_station.close)
        self.right_station = open_station(self.station_path.with_name('right.sqlite'))
        self.addCleanup(self.right_station.close)

    def pair(self, frames, quota):
        left_wire = issue_coord(
            self.left_station, build_design(self.left_station, SHIFTS[0], frames[0], quota)
        )
        right_issue = issue_coord(
            self.right_station, build_design(self.right_station, SHIFTS[1], frames[1], quota)
        )
        left = receive_coord(self.center, *left_wire)
        right = receive_coord(self.center, *right_issue)
        return left_wire, right_issue, left, right

    def matter(self, case):
        frames, quota = CASES[case][0], CASES[case][1]
        return frames, quota


def _shared_keys(station, local_ids):
    keys = set()
    for local_id in local_ids:
        row = station.execute(
            'SELECT shared_key FROM specimens WHERE local_id = ?', (local_id,)
        ).fetchone()
        if row is not None and row[0] is not None:
            keys.add(row[0])
    return keys


class TestJointPlan(CoordFixture):
    def test_one_each_uniform_and_disjoint(self):
        frames, quota = self.matter('one_each')
        left_issue, right_issue, left, right = self.pair(frames, quota)
        signed = plan(self.center, left, right)
        self.assertIsNotNone(signed)
        self.assertEqual(signed['receipt_ids'], (left, right))
        domain = signed['N']
        self.assertIs(type(domain), int)
        left_counts = Counter()
        right_counts = Counter()
        for ticket in range(domain):
            l_pull = resolve_ticket(self.left_station, signed['left_card'], ticket)
            r_pull = resolve_ticket(self.right_station, signed['right_card'], ticket)
            left_counts[tuple(sorted(l_pull))] += 1
            right_counts[tuple(sorted(r_pull))] += 1
            self.assertFalse(_shared_keys(self.left_station, l_pull) & _shared_keys(self.right_station, r_pull))
        self.assertEqual(domain, 3)
        self.assertEqual(set(left_counts.values()), {1})
        self.assertEqual(set(right_counts.values()), {1})
        self.assertEqual(len(left_counts), 3)
        self.assertEqual(len(right_counts), 3)

    def test_layered_uniform(self):
        frames, quota = self.matter('layered')
        _, _, left, right = self.pair(frames, quota)
        signed = plan(self.center, left, right)
        left_counts = Counter()
        right_counts = Counter()
        for ticket in range(signed['N']):
            left_counts[tuple(sorted(resolve_ticket(self.left_station, signed['left_card'], ticket)))] += 1
            right_counts[tuple(sorted(resolve_ticket(self.right_station, signed['right_card'], ticket)))] += 1
        self.assertEqual(len(left_counts), 6)
        self.assertEqual(len(right_counts), 8)
        self.assertEqual(set(left_counts.values()), {signed['N'] // 6})
        self.assertEqual(set(right_counts.values()), {signed['N'] // 8})
        self.assertEqual(signed['N'] % 6, 0)
        self.assertEqual(signed['N'] % 8, 0)

    def test_no_plan(self):
        for case in ('two_each', 'layer_conflict'):
            with self.subTest(case=case):
                frames, quota = self.matter(case)
                before = counts(self.center, 'plans', 'coord_intake')
                _, _, left, right = self.pair(frames, quota)
                self.assertIsNone(plan(self.center, left, right))
                # only intake rows were added by delivery; no plan row appears
                self.assertEqual(counts(self.center, 'plans')['plans'], before['plans'])

    def test_empty_plan(self):
        frames, quota = self.matter('empty')
        _, _, left, right = self.pair(frames, quota)
        signed = plan(self.center, left, right)
        self.assertEqual(signed['N'], 1)
        self.assertEqual(resolve_ticket(self.left_station, signed['left_card'], 0), ())
        self.assertEqual(resolve_ticket(self.right_station, signed['right_card'], 0), ())

    def test_readback_after_reopen(self):
        frames, quota = self.matter('one_each')
        _, _, left, right = self.pair(frames, quota)
        signed = plan(self.center, left, right)
        self.reopen_center()
        self.assertEqual(read_plan(self.center, signed['plan_id']), signed)
        self.assertIsNone(read_plan(self.center, 'missing-plan'))
        with self.assertRaises(InvalidInput):
            read_plan(self.center, '   ')

    def test_receipts_split_after_stations_close(self):
        frames, quota = self.matter('one_each')
        left_issue, right_issue, left, right = self.pair(frames, quota)
        self.left_station.close()
        self.right_station.close()
        self.reopen_center()
        signed = plan(self.center, left, right)
        self.assertIsNotNone(signed)
        self.assertEqual(read_plan(self.center, signed['plan_id']), signed)
        # Reopen the station and resolve from the signed plan alone.
        self.left_station = open_station(self.station_path.with_name('left.sqlite'))
        self.addCleanup(self.left_station.close)
        self.right_station = open_station(self.station_path.with_name('right.sqlite'))
        self.addCleanup(self.right_station.close)
        for ticket in range(signed['N']):
            self.assertTrue(resolve_ticket(self.left_station, signed['left_card'], ticket) is not None)
            self.assertTrue(resolve_ticket(self.right_station, signed['right_card'], ticket) is not None)

    def test_missing_and_same_shift(self):
        frames, quota = self.matter('one_each')
        left_issue, right_issue, left, right = self.pair(frames, quota)
        with self.assertRaises(UnknownRecord):
            plan(self.center, left, 'missing-receipt')
        with self.assertRaises(UnknownRecord):
            plan(self.center, 'missing-receipt', right)
        second = receive_coord(self.center, *left_issue)
        with self.assertRaises(InvalidInput):
            plan(self.center, left, second)
        self.assertEqual(counts(self.center, 'plans')['plans'], 0)

    def test_invalid_ticket(self):
        frames, quota = self.matter('one_each')
        _, _, left, right = self.pair(frames, quota)
        signed = plan(self.center, left, right)
        card = signed['left_card']
        for ticket in (-1, signed['N'], True, 1.0, '0'):
            with self.subTest(ticket=ticket), self.assertRaises(InvalidInput):
                resolve_ticket(self.left_station, card, ticket)

    def test_bad_card(self):
        frames, quota = self.matter('one_each')
        _, _, left, right = self.pair(frames, quota)
        signed = plan(self.center, left, right)
        card = signed['left_card']
        for damaged in (card[:-1], card.replace(b'"N":3', b'"N":4', 1), b'not json'):
            with self.subTest(damaged=damaged[:12]), self.assertRaises(InvalidInput):
                resolve_ticket(self.left_station, damaged, 0)

    def test_card_bound_to_original_issue(self):
        frames, quota = self.matter('one_each')
        _, _, left, right = self.pair(frames, quota)
        signed = plan(self.center, left, right)
        original = resolve_ticket(self.left_station, signed['left_card'], 0)
        # A fresh station with identical names/frames but a new issue cannot stand in.
        with closing(open_station(':memory:')) as other:
            build_design(other, SHIFTS[0], frames[0], quota)
            with self.assertRaises(UnknownRecord):
                resolve_ticket(other, signed['left_card'], 0)
        self.assertEqual(resolve_ticket(self.left_station, signed['left_card'], 0), original)

    def test_erratum_keeps_card(self):
        frames, quota = self.matter('one_each')
        left_issue = issue_coord(
            self.left_station, build_design(self.left_station, SHIFTS[0], frames[0], quota)
        )
        right_issue = issue_coord(
            self.right_station, build_design(self.right_station, SHIFTS[1], frames[1], quota)
        )
        left = receive_coord(self.center, *left_issue)
        right = receive_coord(self.center, *right_issue)
        signed = plan(self.center, left, right)
        before = [resolve_ticket(self.left_station, signed['left_card'], t) for t in range(signed['N'])]
        changed = revise(self.left_station, SHIFTS[0], {'A-x': ('x', 0)})
        freeze(self.left_station, SHIFTS[0], changed, 'sample',
               {'rack': ('A-x', 'A-a', 'A-b')}, {'rack': 1})
        after = [resolve_ticket(self.left_station, signed['left_card'], t) for t in range(signed['N'])]
        self.assertEqual(before, after)

    def test_failed_signing_leaves_nothing(self):
        frames, quota = self.matter('one_each')
        _, _, left, right = self.pair(frames, quota)
        self.center.execute(
            "CREATE TRIGGER reject_plan BEFORE INSERT ON plans "
            "BEGIN SELECT RAISE(ABORT, 'plan ledger is closed'); END"
        )
        with self.assertRaisesRegex(sqlite3.IntegrityError, 'plan ledger is closed'):
            plan(self.center, left, right)
        self.reopen_center()
        self.assertEqual(counts(self.center, 'plans')['plans'], 0)
        self.center.execute('DROP TRIGGER reject_plan')
        signed = plan(self.center, left, right)
        self.assertIsNotNone(signed)
        self.assertEqual(counts(self.center, 'plans')['plans'], 1)

    def test_repeat_intake_original_receipt(self):
        frames, quota = self.matter('one_each')
        left_issue, _, left, _ = self.pair(frames, quota)
        self.assertEqual(receive_coord(self.center, *left_issue), left)
        altered = left_issue[1].replace(b'"quota":1', b'"quota":0', 1)
        with self.assertRaises(InvalidInput):
            receive_coord(self.center, left_issue[0], altered)

    def test_v1_v2_cross_rejection(self):
        from exchange.intake import receive
        from exchange.issue import issue
        design = build_design(self.station, SHIFTS[0], ONE_RACK[0], 1)
        v1_number, v1_wire = issue(self.station, design)
        v2_number, v2_wire = issue_coord(self.station, design)
        with self.assertRaises(InvalidInput):
            receive_coord(self.center, v1_number, v1_wire)
        with self.assertRaises(InvalidInput):
            receive(self.center, v2_number, v2_wire)

    def test_card_round_trip(self):
        frames, quota = self.matter('layered')
        _, _, left, right = self.pair(frames, quota)
        signed = plan(self.center, left, right)
        decoded = decode_card(signed['left_card'])
        self.assertEqual(encode_card(decoded), signed['left_card'])
        self.assertEqual(decoded.plan_id, signed['plan_id'])
        self.assertEqual(decoded.ticket_domain, signed['N'])

    def test_layered_disjoint_every_ticket(self):
        frames, quota = self.matter('layered')
        _, _, left, right = self.pair(frames, quota)
        signed = plan(self.center, left, right)
        for ticket in range(signed['N']):
            l_pull = resolve_ticket(self.left_station, signed['left_card'], ticket)
            r_pull = resolve_ticket(self.right_station, signed['right_card'], ticket)
            self.assertFalse(
                _shared_keys(self.left_station, l_pull)
                & _shared_keys(self.right_station, r_pull)
            )

    def test_tray_limit(self):
        wide = {'rack': tuple((f'A-{index}', None) for index in range(9))}
        with self.assertRaises(InvalidInput):
            issue_coord(self.left_station, build_design(self.left_station, SHIFTS[0], wide, 1))
        self.assertEqual(counts(self.left_station, 'coord_batches')['coord_batches'], 0)

    def test_card_cannot_be_reissued_elsewhere(self):
        frames, quota = self.matter('one_each')
        _, _, left, right = self.pair(frames, quota)
        signed = plan(self.center, left, right)
        # Re-encode the card with a foreign issue id: resolution must fail, not drift.
        decoded = decode_card(signed['left_card'])
        forged = encode_card(replace(decoded, issue_id='forged-issue'))
        with self.assertRaises(UnknownRecord):
            resolve_ticket(self.left_station, forged, 0)
