"""Center-side joint plan signing and readback."""
from dataclasses import dataclass
import sqlite3

from exchange.coord_intake import CoordReceipt, load_coord_receipt
from ledger import require_key


@dataclass(frozen=True)
class SignedPlan:
    plan_id: str
    receipt_ids: tuple[str, str]
    domain: int
    left_card: bytes
    right_card: bytes

    def as_dict(self) -> dict:
        return {
            'plan_id': self.plan_id,
            'receipt_ids': self.receipt_ids,
            'N': self.domain,
            'left_card': self.left_card,
            'right_card': self.right_card,
        }


def store_plan(
    db: sqlite3.Connection,
    plan_id: str,
    domain: int,
    left: CoordReceipt,
    right: CoordReceipt,
    left_card: bytes,
    right_card: bytes,
) -> None:
    db.execute(
        'INSERT INTO plans(plan_id, domain, left_receipt_id, right_receipt_id, '
        'left_card, right_card) VALUES (?, ?, ?, ?, ?, ?)',
        (plan_id, domain, left.receipt_id, right.receipt_id, left_card, right_card),
    )


def load_plan(db: sqlite3.Connection, plan_id: str) -> SignedPlan | None:
    plan_id = require_key(plan_id, 'plan_id')
    row = db.execute(
        'SELECT domain, left_receipt_id, right_receipt_id, left_card, right_card '
        'FROM plans WHERE plan_id = ?',
        (plan_id,),
    ).fetchone()
    if row is None:
        return None
    domain, left_id, right_id, left_card, right_card = row
    if load_coord_receipt(db, left_id) is None or load_coord_receipt(db, right_id) is None:
        raise sqlite3.IntegrityError(f'plan {plan_id} refers to a missing receipt')
    return SignedPlan(plan_id, (left_id, right_id), domain, left_card, right_card)
