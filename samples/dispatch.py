"""End-to-end joint dispatch demo.

Sequence:
1. Each shift's frame is frozen, issued and delivered while both stations and
   the center are open.
2. Both station databases are closed; the center signs the plan from the two
   receipts alone and reads it back by number.
3. The center is closed; the stations are reopened and resolve the same tickets
   from the signed cards alone, printing each shift's pull list.
"""
from contextlib import closing
import json
from collections import Counter
from pathlib import Path
from tempfile import TemporaryDirectory

from analysis.risk import plan
from exchange.coord_intake import receive_coord
from exchange.issue import issue_coord, resolve_ticket
from ledger import open_center, open_station
from report.retrieve import read_plan
from samples.coordination import CASES, SHIFTS, build_design


def _shared_map(station):
    rows = station.execute(
        'SELECT local_id, shared_key FROM specimens WHERE shared_key IS NOT NULL'
    )
    return dict(rows)


def main() -> None:
    case_name = 'layered'
    frames, quota = CASES[case_name][0], CASES[case_name][1]
    with TemporaryDirectory(prefix='thin-plate-coord-') as directory:
        root = Path(directory)
        left_path = root / 'shift-a.sqlite'
        right_path = root / 'shift-b.sqlite'
        center_path = root / 'center.sqlite'

        with (
            closing(open_station(left_path)) as left_station,
            closing(open_station(right_path)) as right_station,
            closing(open_center(center_path)) as center,
        ):
            left_issue = issue_coord(
                left_station, build_design(left_station, SHIFTS[0], frames[0], quota)
            )
            right_issue = issue_coord(
                right_station, build_design(right_station, SHIFTS[1], frames[1], quota)
            )
            left = receive_coord(center, *left_issue)
            right = receive_coord(center, *right_issue)
            left_shared = _shared_map(left_station)
            right_shared = _shared_map(right_station)
        # Both station databases are now closed.

        with closing(open_center(center_path)) as center:
            signed = plan(center, left, right)
            if signed is None:
                raise RuntimeError('the demo case must sign a plan')
            archived = read_plan(center, signed['plan_id'])
            if archived != signed:
                raise RuntimeError('the signed plan changed after the stations closed')
            print(json.dumps({
                'stage': 'sign',
                'plan_id': archived['plan_id'],
                'receipt_ids': list(archived['receipt_ids']),
                'N': archived['N'],
                'left_card_bytes': len(archived['left_card']),
                'right_card_bytes': len(archived['right_card']),
            }, ensure_ascii=False, sort_keys=True))
        # The center is now closed; only the signed plan and cards remain known.

        with (
            closing(open_station(left_path)) as left_station,
            closing(open_station(right_path)) as right_station,
        ):
            left_counts = Counter()
            right_counts = Counter()
            for ticket in range(signed['N']):
                left_pull = resolve_ticket(left_station, signed['left_card'], ticket)
                right_pull = resolve_ticket(right_station, signed['right_card'], ticket)
                left_keys = {left_shared[local] for local in left_pull if local in left_shared}
                right_keys = {right_shared[local] for local in right_pull if local in right_shared}
                if left_keys & right_keys:
                    raise RuntimeError(f'ticket {ticket} shares a specimen across shifts')
                left_counts[tuple(sorted(left_pull))] += 1
                right_counts[tuple(sorted(right_pull))] += 1
                print(json.dumps({
                    'stage': 'ticket',
                    'ticket': ticket,
                    'left_pull': list(left_pull),
                    'right_pull': list(right_pull),
                }, ensure_ascii=False, sort_keys=True))
            print(json.dumps({
                'stage': 'summary',
                'left_distinct': len(left_counts),
                'right_distinct': len(right_counts),
                'left_frequencies': sorted(set(left_counts.values())),
                'right_frequencies': sorted(set(right_counts.values())),
            }, ensure_ascii=False, sort_keys=True))


if __name__ == '__main__':
    main()
