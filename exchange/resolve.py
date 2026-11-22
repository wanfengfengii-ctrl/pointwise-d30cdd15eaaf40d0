"""Station-side joint shipment issuance and shift-card resolution."""
import sqlite3
from uuid import uuid4

from exchange.joint import JointPacket, decode_card, decode_joint, encode_joint
from exchange.issue import build_evidence
from ledger import InvalidInput, UnknownRecord, transaction
from sampling.design import FrozenDesign, load_design


MAX_TRAY = 8


def _tray_size(design: FrozenDesign) -> int:
    return sum(len(frame.specimens) for frame in design.layers)


def issue_coord(db: sqlite3.Connection, design_id: int) -> tuple[str, bytes]:
    with transaction(db, allow_nested=True):
        design = load_design(db, design_id)
        if _tray_size(design) > MAX_TRAY:
            raise InvalidInput('a joint shipment needs a frozen frame of at most eight pieces')
        issue_id = uuid4().hex
        packet = JointPacket(
            issue_id, design.shift_id, design.design_id, design.revision_id,
            design.protocol_version, build_evidence(design),
        )
        wire = encode_joint(packet)
        db.execute(
            'INSERT INTO coord_batches(issue_id, design_id, wire) VALUES (?, ?, ?)',
            (issue_id, design_id, wire),
        )
    return issue_id, wire


def private_slots(design: FrozenDesign) -> dict[str, tuple[str, ...]]:
    """Per layer, private local IDs with normal pieces before abnormal ones.

    Within each group the local number orders the slots.  The mapping depends only
    on the frozen design, so the same card ticket resolves to the same physical
    pieces after errata or a station reopen.
    """
    slots = {}
    for frame in design.layers:
        ordered = sorted(
            (member for member in frame.specimens
             if member.observation.specimen.shared_key is None),
            key=lambda member: (member.abnormal, member.observation.specimen.local_id),
        )
        slots[frame.name] = tuple(member.observation.specimen.local_id for member in ordered)
    return slots


def _local_for_shared(db: sqlite3.Connection, shift_id: str, shared_key: str) -> str | None:
    row = db.execute(
        'SELECT local_id FROM specimens WHERE shift_id = ? AND shared_key = ?',
        (shift_id, shared_key),
    ).fetchone()
    return None if row is None else row[0]


def _original(db: sqlite3.Connection, issue_id: str) -> tuple[FrozenDesign, JointPacket]:
    row = db.execute(
        'SELECT design_id, wire FROM coord_batches WHERE issue_id = ?', (issue_id,)
    ).fetchone()
    if row is None:
        raise UnknownRecord(f'unknown joint shipment {issue_id}')
    return load_design(db, row[0]), decode_joint(row[1])


def resolve_ticket(db: sqlite3.Connection, card: bytes, ticket: int) -> tuple[str, ...]:
    decoded = decode_card(card)
    if type(ticket) is not int or not 0 <= ticket < decoded.ticket_domain:
        raise InvalidInput('ticket must be an integer inside the card domain')
    design, original = _original(db, decoded.issue_id)
    if (design.shift_id != decoded.shift_id or design.design_id != decoded.design_id
            or design.revision_id != decoded.revision_id
            or design.protocol_version != decoded.protocol_version):
        raise InvalidInput('the card does not match the original frozen shipment')
    if decoded.evidence != original.evidence:
        raise InvalidInput('the card evidence does not match the original shipment')
    slots = private_slots(design)
    names = [frame.name for frame in design.layers]
    if [layer.name for layer in decoded.evidence.layers] != names:
        raise InvalidInput('the card layers do not match the frozen shipment')
    pulled = []
    for layer, layer_name, pick in zip(decoded.evidence.layers, names, decoded.tickets[ticket]):
        shared_index, private_index = pick
        layer_slots = slots[layer_name]
        for index in shared_index:
            local = _local_for_shared(db, design.shift_id, layer.shared[index].key)
            if local is None:
                raise InvalidInput('the card names a shared piece outside the frozen frame')
            pulled.append(local)
        for index in private_index:
            if index >= len(layer_slots):
                raise InvalidInput('the card names a private slot outside the frozen frame')
            pulled.append(layer_slots[index])
    return tuple(pulled)
