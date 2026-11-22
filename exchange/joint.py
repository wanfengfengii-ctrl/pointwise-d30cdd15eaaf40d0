"""Version-2 joint shipment and shift-card encodings.

A joint shipment carries the same anonymised sampling evidence as a V1 batch but
is tagged version 2 so that the center can tell the two contract families apart.
A shift card always binds to one *original* shipment (``issue_id``) and names, for
every ticket in ``0 .. N-1``, which shared keys and which private slots that shift
must pull.  Cards never carry local specimen numbers or measurements: shared
pieces appear through the existing registration-derived keys and private pieces
only through their slot index within their layer.
"""
from dataclasses import dataclass
import hashlib
import json

from exchange.codec import canonical, evidence_from_payload, evidence_payload
from exchange.evidence import SamplingEvidence, validate_evidence
from ledger import InvalidInput, require_id, require_key

VERSION = 2


@dataclass(frozen=True)
class JointPacket:
    issue_id: str
    shift_id: str
    design_id: int
    revision_id: int
    protocol_version: str
    evidence: SamplingEvidence


@dataclass(frozen=True)
class ShiftCard:
    plan_id: str
    issue_id: str
    shift_id: str
    design_id: int
    revision_id: int
    protocol_version: str
    ticket_domain: int
    evidence: SamplingEvidence
    tickets: tuple[tuple[tuple[tuple[int, ...], tuple[int, ...]], ...], ...]


def _digest(payload: dict) -> str:
    return hashlib.sha256(canonical(payload)).hexdigest()


def _wrap(payload: dict) -> bytes:
    return canonical({'payload': payload, 'sha256': _digest(payload)})


def _require_ticket_domain(value: object) -> int:
    if type(value) is not int or value <= 0:
        raise InvalidInput('ticket domain must be a positive integer')
    return value


def joint_payload(packet: JointPacket) -> dict:
    validate_evidence(packet.evidence)
    return {
        'version': VERSION,
        'issue_id': require_key(packet.issue_id, 'issue_id'),
        'shift_id': require_key(packet.shift_id, 'shift_id'),
        'design_id': require_id(packet.design_id, 'design_id'),
        'revision_id': require_id(packet.revision_id, 'revision_id'),
        'protocol_version': require_key(packet.protocol_version, 'protocol version'),
        'layers': evidence_payload(packet.evidence),
    }


def encode_joint(packet: JointPacket) -> bytes:
    return _wrap(joint_payload(packet))


def joint_digest(packet: JointPacket) -> str:
    return _digest(joint_payload(packet))


def decode_joint(wire: bytes) -> JointPacket:
    if not isinstance(wire, bytes):
        raise InvalidInput('joint packet must be bytes')
    try:
        envelope = json.loads(wire.decode('utf-8'))
        payload = envelope['payload']
        if type(payload['version']) is not int or payload['version'] != VERSION:
            raise InvalidInput('unsupported joint packet version')
        packet = JointPacket(
            payload['issue_id'], payload['shift_id'], payload['design_id'],
            payload['revision_id'], payload['protocol_version'],
            evidence_from_payload(payload['layers']),
        )
        if encode_joint(packet) != wire:
            raise InvalidInput('joint packet checksum or canonical encoding is invalid')
    except InvalidInput:
        raise
    except (KeyError, TypeError, ValueError, RecursionError) as error:
        raise InvalidInput('invalid joint packet structure or encoding') from error
    return packet


def _ticket_payload(ticket) -> list:
    return [[list(shared), list(private)] for shared, private in ticket]


def card_payload(card: ShiftCard) -> dict:
    validate_evidence(card.evidence)
    return {
        'version': VERSION,
        'plan_id': require_key(card.plan_id, 'plan_id'),
        'issue_id': require_key(card.issue_id, 'issue_id'),
        'shift_id': require_key(card.shift_id, 'shift_id'),
        'design_id': require_id(card.design_id, 'design_id'),
        'revision_id': require_id(card.revision_id, 'revision_id'),
        'protocol_version': require_key(card.protocol_version, 'protocol version'),
        'N': _require_ticket_domain(card.ticket_domain),
        'layers': evidence_payload(card.evidence),
        'tickets': [_ticket_payload(ticket) for ticket in card.tickets],
    }


def encode_card(card: ShiftCard) -> bytes:
    return _wrap(card_payload(card))


def card_digest(card: ShiftCard) -> str:
    return _digest(card_payload(card))


def _index_list(value: object, upper: int, field: str) -> tuple[int, ...]:
    if type(value) is not list:
        raise InvalidInput(f'a card ticket {field} must be an array')
    seen = []
    for entry in value:
        if type(entry) is not int or not 0 <= entry < upper:
            raise InvalidInput(f'a card ticket {field} index is out of range')
        seen.append(entry)
    return tuple(seen)


def _decode_tickets(value: object, evidence: SamplingEvidence, domain: int):
    if type(value) is not list or len(value) != domain:
        raise InvalidInput('a card must name exactly one sample for each ticket')
    tickets = []
    for ticket in value:
        if type(ticket) is not list or len(ticket) != len(evidence.layers):
            raise InvalidInput('each card ticket must cover every layer once')
        picks = []
        for layer, entry in zip(evidence.layers, ticket):
            if type(entry) is not list or len(entry) != 2:
                raise InvalidInput('each layer pick needs shared indices and private indices')
            shared = _index_list(entry[0], len(layer.shared), 'shared')
            private = _index_list(entry[1], layer.private_normal + layer.private_abnormal, 'private')
            if len(shared) + len(private) != layer.quota:
                raise InvalidInput('a card layer pick must fill the frozen quota')
            if len(set(shared)) != len(shared) or len(set(private)) != len(private):
                raise InvalidInput('a card layer pick must not repeat a piece')
            picks.append((shared, private))
        tickets.append(tuple(picks))
    return tuple(tickets)


def decode_card(wire: bytes) -> ShiftCard:
    if not isinstance(wire, bytes):
        raise InvalidInput('card must be bytes')
    try:
        envelope = json.loads(wire.decode('utf-8'))
        payload = envelope['payload']
        if type(payload['version']) is not int or payload['version'] != VERSION:
            raise InvalidInput('unsupported card version')
        evidence = evidence_from_payload(payload['layers'])
        card = ShiftCard(
            payload['plan_id'], payload['issue_id'], payload['shift_id'],
            payload['design_id'], payload['revision_id'], payload['protocol_version'],
            payload['N'], evidence, _decode_tickets(payload['tickets'], evidence, payload['N']),
        )
        if encode_card(card) != wire:
            raise InvalidInput('card checksum or canonical encoding is invalid')
    except InvalidInput:
        raise
    except (KeyError, TypeError, ValueError, RecursionError) as error:
        raise InvalidInput('invalid card structure or encoding') from error
    return card
