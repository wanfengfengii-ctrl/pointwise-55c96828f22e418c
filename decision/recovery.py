from dataclasses import dataclass

from capture.validation import ValidatedFragment
from decision.equations import ConflictingEvidence, ConstraintSet, xor_bytes
from jobs.identity import TapeSide


class MissingEvidence(ValueError):
    pass


@dataclass(frozen=True)
class RecoveredTapes:
    a: bytes
    b: bytes
    a_support: tuple[ValidatedFragment, ...]
    b_support: tuple[ValidatedFragment, ...]

    def support_for(self, side: TapeSide):
        return self.a_support if side is TapeSide.A else self.b_support


def recover_tapes(constraints: ConstraintSet) -> RecoveredTapes:
    a, b, parity = constraints.a, constraints.b, constraints.parity
    if a is not None and b is not None:
        if parity is not None and xor_bytes(a.payload, b.payload) != parity.payload:
            raise ConflictingEvidence('parity disagrees with the direct tapes')
        return RecoveredTapes(a.payload, b.payload, (a,), (b,))
    if parity is not None and a is not None:
        return RecoveredTapes(a.payload, xor_bytes(a.payload, parity.payload), (a,), (a, parity))
    if parity is not None and b is not None:
        return RecoveredTapes(xor_bytes(b.payload, parity.payload), b.payload, (b, parity), (b,))
    raise MissingEvidence('compatible fragments do not uniquely determine A and B')
