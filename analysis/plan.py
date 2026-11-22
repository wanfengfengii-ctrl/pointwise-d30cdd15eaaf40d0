"""Joint planning entry point for two different shifts."""
from uuid import uuid4

import sqlite3

from analysis.coordination import build_schedule
from exchange.coord_intake import CoordReceipt, load_coord_receipt
from exchange.joint import ShiftCard, encode_card
from ledger import InvalidInput, UnknownRecord, require_key, transaction
from report.plan import SignedPlan, store_plan


MAX_TRAY = 8


def _card_for(receipt: CoordReceipt, plan_id: str, tickets, domain: int) -> ShiftCard:
    packet = receipt.packet
    return ShiftCard(
        plan_id, packet.issue_id, packet.shift_id, packet.design_id,
        packet.revision_id, packet.protocol_version, domain, packet.evidence,
        tuple(ticket for ticket in tickets),
    )


def _samples_by_ticket(schedule, side: str) -> tuple:
    variants = schedule.left_variants if side == 'left' else schedule.right_variants
    index = 0 if side == 'left' else 1
    return tuple(variants[pair[index]] for pair in schedule.pairs)


def _validate_frame(receipt: CoordReceipt) -> None:
    evidence = receipt.packet.evidence
    total = sum(
        len(layer.shared) + layer.private_normal + layer.private_abnormal
        for layer in evidence.layers
    )
    if total > MAX_TRAY:
        raise InvalidInput('joint shipment frames must hold at most eight pieces')


def plan(db: sqlite3.Connection, left_receipt_id: str, right_receipt_id: str) -> dict | None:
    left_receipt_id = require_key(left_receipt_id, 'left receipt ID')
    right_receipt_id = require_key(right_receipt_id, 'right receipt ID')
    left = load_coord_receipt(db, left_receipt_id)
    right = load_coord_receipt(db, right_receipt_id)
    if left is None:
        raise UnknownRecord(f'unknown joint receipt {left_receipt_id}')
    if right is None:
        raise UnknownRecord(f'unknown joint receipt {right_receipt_id}')
    if left.packet.shift_id == right.packet.shift_id:
        raise InvalidInput('a joint plan requires receipts from two different shifts')
    _validate_frame(left)
    _validate_frame(right)
    schedule = build_schedule(left.packet.evidence, right.packet.evidence)
    if schedule is None:
        return None
    plan_id = 'plan-' + uuid4().hex
    left_card = _card_for(left, plan_id, _samples_by_ticket(schedule, 'left'), schedule.ticket_domain)
    right_card = _card_for(right, plan_id, _samples_by_ticket(schedule, 'right'), schedule.ticket_domain)
    left_bytes = encode_card(left_card)
    right_bytes = encode_card(right_card)
    with transaction(db):
        store_plan(
            db, plan_id, schedule.ticket_domain, left, right, left_bytes, right_bytes
        )
    return SignedPlan(
        plan_id, (left_receipt_id, right_receipt_id), schedule.ticket_domain,
        left_bytes, right_bytes,
    ).as_dict()
