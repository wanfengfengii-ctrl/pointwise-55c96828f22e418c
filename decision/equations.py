from dataclasses import dataclass

from capture.evidence import CustodySnapshot
from capture.validation import FragmentKind, ValidatedFragment


class ConflictingEvidence(ValueError):
    pass


@dataclass(frozen=True)
class ConstraintSet:
    a: ValidatedFragment | None
    b: ValidatedFragment | None
    parity: ValidatedFragment | None


def xor_bytes(left: bytes, right: bytes) -> bytes:
    return bytes(a ^ b for a, b in zip(left, right, strict=True))


def collect_constraints(snapshot: CustodySnapshot) -> ConstraintSet:
    selected = {}
    for record in sorted(snapshot.records, key=lambda item: item.receipt.sort_key()):
        fragment = record.fragment
        previous = selected.setdefault(fragment.kind, fragment)
        if previous.payload != fragment.payload:
            raise ConflictingEvidence(f'conflicting {fragment.kind} fragments')
    return ConstraintSet(selected.get(FragmentKind.A), selected.get(FragmentKind.B),
                         selected.get(FragmentKind.P))
