import sqlite3

from exchange.catalog import load_by_issue, store_receipt
from exchange.codec import decode_packet
from ledger import InvalidInput, require_key, transaction


def receive_coord(db: sqlite3.Connection, issue_id: str, wire: bytes) -> str:
    from exchange.coord_intake import receive_coord as _receive_coord

    return _receive_coord(db, issue_id, wire)


def receive(db: sqlite3.Connection, issue_id: str, wire: bytes) -> int:
    issue_id = require_key(issue_id, 'issue_id')
    packet = decode_packet(wire)
    if issue_id != packet.issue_id:
        raise InvalidInput('the delivered issue ID differs from the packet issue ID')
    with transaction(db):
        existing = load_by_issue(db, issue_id)
        if existing is None:
            receipt_id = store_receipt(db, packet, wire)
        else:
            if existing.wire != wire:
                raise InvalidInput('a different packet already uses this issue ID')
            receipt_id = existing.receipt_id
    return receipt_id
