"""Center-side intake for joint shipments."""
from dataclasses import dataclass
import sqlite3

from exchange.joint import JointPacket, decode_joint, joint_digest
from ledger import InvalidInput, require_key, transaction


@dataclass(frozen=True)
class CoordReceipt:
    receipt_id: str
    packet: JointPacket
    wire: bytes
    digest: str


def _receipt(row) -> CoordReceipt | None:
    if row is None:
        return None
    receipt_id, wire, digest = row
    return CoordReceipt(receipt_id, decode_joint(wire), wire, digest)


def receive_coord(db: sqlite3.Connection, issue_id: str, wire: bytes) -> str:
    issue_id = require_key(issue_id, 'issue_id')
    packet = decode_joint(wire)
    if issue_id != packet.issue_id:
        raise InvalidInput('the delivered issue ID differs from the joint packet issue ID')
    with transaction(db):
        row = db.execute(
            'SELECT receipt_id, wire FROM coord_intake WHERE issue_id = ?', (issue_id,)
        ).fetchone()
        if row is None:
            receipt_id = 'coord-' + issue_id
            db.execute(
                'INSERT INTO coord_intake(receipt_id, issue_id, wire, digest) VALUES (?, ?, ?, ?)',
                (receipt_id, issue_id, wire, joint_digest(packet)),
            )
        else:
            if row[1] != wire:
                raise InvalidInput('a different joint packet already uses this issue ID')
            receipt_id = row[0]
    return receipt_id


def load_coord_receipt(db: sqlite3.Connection, receipt_id: str) -> CoordReceipt | None:
    receipt_id = require_key(receipt_id, 'receipt_id')
    row = db.execute(
        'SELECT receipt_id, wire, digest FROM coord_intake WHERE receipt_id = ?', (receipt_id,)
    ).fetchone()
    return _receipt(row)


def load_coord_by_issue(db: sqlite3.Connection, issue_id: str) -> CoordReceipt | None:
    issue_id = require_key(issue_id, 'issue_id')
    row = db.execute(
        'SELECT receipt_id, wire, digest FROM coord_intake WHERE issue_id = ?', (issue_id,)
    ).fetchone()
    return _receipt(row)
