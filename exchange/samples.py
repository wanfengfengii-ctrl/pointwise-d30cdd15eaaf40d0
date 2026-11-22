"""Canonical enumeration of complete samples from layered sampling evidence.

Both the center (while planning) and a station (while resolving a card) must agree
on the index of every complete sample.  A complete sample is, for each layer in
name order, an unordered selection of ``quota`` distinct pieces.  Within a layer
the selection is represented by the indices of the chosen shared marks (sorted by
key) together with the indices of the chosen private slots.  Private slots number
the normal private pieces before the abnormal ones; the center only tracks the
slots while a station maps them to its own specimen rows.
"""
from itertools import combinations, product
from math import comb

from exchange.evidence import LayerEvidence, SamplingEvidence


def layer_descriptor(layer: LayerEvidence) -> tuple[int, int, int, int]:
    """Return ``(shared_count, normal_private, abnormal_private, quota)``."""
    return len(layer.shared), layer.private_normal, layer.private_abnormal, layer.quota


def descriptors(evidence: SamplingEvidence) -> tuple[tuple[int, int, int, int], ...]:
    return tuple(layer_descriptor(layer) for layer in evidence.layers)


def _layer_variants(shared_count: int, private_normal: int, private_abnormal: int, quota: int):
    private_total = private_normal + private_abnormal
    least_shared = max(0, quota - private_total)
    most_shared = min(shared_count, quota)
    for shared_count_taken in range(least_shared, most_shared + 1):
        for shared_index in combinations(range(shared_count), shared_count_taken):
            for private_index in combinations(range(private_total), quota - shared_count_taken):
                yield shared_index, private_index


def variant_count(layer_descriptors) -> int:
    total = 1
    for shared_count, private_normal, private_abnormal, quota in layer_descriptors:
        total *= comb(shared_count + private_normal + private_abnormal, quota)
    return total


def enumerate_variants(layer_descriptors):
    """Return every complete sample in canonical order as a tuple of layer picks."""
    per_layer = [list(_layer_variants(*descriptor)) for descriptor in layer_descriptors]
    return list(product(*per_layer)) if per_layer else [tuple()]


def variant_shared_keys(evidence: SamplingEvidence, variant) -> frozenset[str]:
    keys = set()
    for layer, (shared_index, _private_index) in zip(evidence.layers, variant):
        for index in shared_index:
            keys.add(layer.shared[index].key)
    return frozenset(keys)
