"""Joint scheduling for two frozen shifts.

The old review conditioned on the two shifts' selections being physically
disjoint.  Conditioning skews each shift's marginal away from its original
layered draw.  This module instead builds a schedule that keeps every complete
sample of each shift equiprobable while every ticket pairs two disjoint
selections.

Let ``L`` and ``R`` be the numbers of complete samples of the two shifts and let
``N`` be a common multiple of both.  A ticket is one left sample and one right
sample.  Choosing each left sample ``N/L`` times and each right sample ``N/R``
times is a transportation problem: integer flows on the bipartite graph of
disjoint sample pairs.  By network-flow integrality a feasible flow yields the
required multiset of ticket pairs directly.
"""
from dataclasses import dataclass
from math import gcd

from exchange.evidence import SamplingEvidence
from exchange.samples import enumerate_variants, variant_shared_keys


@dataclass(frozen=True)
class Schedule:
    ticket_domain: int
    left_variants: tuple
    right_variants: tuple
    pairs: tuple[tuple[int, int], ...]


class _Arc:
    __slots__ = ('to', 'capacity', 'back')

    def __init__(self, to: int, capacity: int, back: int):
        self.to = to
        self.capacity = capacity
        self.back = back


def _add_arc(graph: list[list[_Arc]], source: int, target: int, capacity: int) -> int:
    forward = _Arc(target, capacity, len(graph[target]))
    backward = _Arc(source, 0, len(graph[source]))
    graph[source].append(forward)
    graph[target].append(backward)
    return len(graph[source]) - 1


def _max_flow(graph: list[list[_Arc]], source: int, sink: int) -> int:
    total = 0
    while True:
        level = [-1] * len(graph)
        level[source] = 0
        queue = [source]
        for node in queue:
            for arc in graph[node]:
                if arc.capacity > 0 and level[arc.to] < 0:
                    level[arc.to] = level[node] + 1
                    queue.append(arc.to)
        if level[sink] < 0:
            return total
        cursor = [0] * len(graph)

        def push(node: int, limit: int) -> int:
            if node == sink:
                return limit
            while cursor[node] < len(graph[node]):
                arc = graph[node][cursor[node]]
                if arc.capacity > 0 and level[arc.to] == level[node] + 1:
                    sent = push(arc.to, min(limit, arc.capacity))
                    if sent:
                        arc.capacity -= sent
                        graph[arc.to][arc.back].capacity += sent
                        return sent
                cursor[node] += 1
            return 0

        while True:
            sent = push(source, 1 << 60)
            if not sent:
                break
            total += sent


def _least_common_multiple(a: int, b: int) -> int:
    return a // gcd(a, b) * b


def build_schedule(left: SamplingEvidence, right: SamplingEvidence) -> Schedule | None:
    left_variants = tuple(enumerate_variants(
        tuple((len(layer.shared), layer.private_normal, layer.private_abnormal, layer.quota)
              for layer in left.layers)
    ))
    right_variants = tuple(enumerate_variants(
        tuple((len(layer.shared), layer.private_normal, layer.private_abnormal, layer.quota)
              for layer in right.layers)
    ))
    left_keys = [variant_shared_keys(left, variant) for variant in left_variants]
    right_keys = [variant_shared_keys(right, variant) for variant in right_variants]
    domain = _least_common_multiple(len(left_variants), len(right_variants))
    left_supply = domain // len(left_variants)
    right_demand = domain // len(right_variants)

    left_count = len(left_variants)
    right_count = len(right_variants)
    source = 0
    sink = left_count + right_count + 1
    graph: list[list[_Arc]] = [[] for _ in range(sink + 1)]
    for index in range(left_count):
        _add_arc(graph, source, 1 + index, left_supply)
    for index in range(right_count):
        _add_arc(graph, 1 + left_count + index, sink, right_demand)
    pair_arcs = {}
    for left_index in range(left_count):
        for right_index in range(right_count):
            if left_keys[left_index] & right_keys[right_index]:
                continue
            pair_arcs[(left_index, right_index)] = _add_arc(
                graph, 1 + left_index, 1 + left_count + right_index, domain
            )

    if _max_flow(graph, source, sink) != domain:
        return None

    pairs = []
    for (left_index, right_index), arc_index in sorted(pair_arcs.items()):
        arc = graph[1 + left_index][arc_index]
        used = domain - arc.capacity
        pairs.extend([(left_index, right_index)] * used)
    if len(pairs) != domain:
        return None
    return Schedule(domain, left_variants, right_variants, tuple(pairs))
