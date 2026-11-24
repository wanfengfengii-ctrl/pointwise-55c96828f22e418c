from dataclasses import dataclass

from capture.custody import CustodyRecord
from decision.equations import ConflictingEvidence, ConstraintSet, xor_bytes
from jobs.identity import TapeSide


class MissingEvidence(ValueError):
    pass


@dataclass(frozen=True)
class RecoveredTapes:
    a: bytes
    b: bytes
    a_support: tuple[CustodyRecord, ...]
    b_support: tuple[CustodyRecord, ...]

    def support_for(self, side: TapeSide):
        return self.a_support if side is TapeSide.A else self.b_support


def _payload(record: CustodyRecord) -> bytes:
    return record.fragment.payload


def recover_tapes(constraints: ConstraintSet) -> RecoveredTapes:
    a, b, parity = constraints.a, constraints.b, constraints.parity
    if a is not None and b is not None:
        if parity is not None and xor_bytes(_payload(a), _payload(b)) != _payload(parity):
            raise ConflictingEvidence('parity disagrees with the direct tapes')
        return RecoveredTapes(_payload(a), _payload(b), (a,), (b,))
    if parity is not None and a is not None:
        return RecoveredTapes(_payload(a), xor_bytes(_payload(a), _payload(parity)), (a,), (a, parity))
    if parity is not None and b is not None:
        return RecoveredTapes(xor_bytes(_payload(b), _payload(parity)), _payload(b), (b, parity), (b,))
    raise MissingEvidence('compatible fragments do not uniquely determine A and B')
