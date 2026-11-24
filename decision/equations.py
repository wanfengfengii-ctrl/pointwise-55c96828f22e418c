from dataclasses import dataclass

from capture.custody import CustodyRecord
from capture.evidence import CustodySnapshot
from capture.validation import FragmentKind


class ConflictingEvidence(ValueError):
    pass


@dataclass(frozen=True)
class ConstraintSet:
    a: CustodyRecord | None
    b: CustodyRecord | None
    parity: CustodyRecord | None


def xor_bytes(left: bytes, right: bytes) -> bytes:
    return bytes(a ^ b for a, b in zip(left, right, strict=True))


def collect_constraints(snapshot: CustodySnapshot) -> ConstraintSet:
    selected = {}
    for record in sorted(snapshot.records, key=lambda item: item.receipt.sort_key()):
        kind = record.fragment.kind
        previous = selected.setdefault(kind, record)
        if previous.fragment.payload != record.fragment.payload:
            raise ConflictingEvidence(f'conflicting {kind} fragments')
    return ConstraintSet(selected.get(FragmentKind.A), selected.get(FragmentKind.B),
                         selected.get(FragmentKind.P))
