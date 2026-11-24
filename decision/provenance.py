from dataclasses import dataclass

from capture.validation import FragmentKind
from decision.recovery import RecoveredTapes
from device.slot import Outlet, SlotReceipt
from jobs.identity import TapeSide


@dataclass(frozen=True)
class EvidenceSource:
    receipt: SlotReceipt
    volume_tag: str
    kind: FragmentKind

    def to_data(self):
        return {
            'job_id': self.receipt.job_id,
            'recording_id': self.receipt.recording_id,
            'outlet': self.receipt.outlet.value,
            'ordinal': self.receipt.ordinal,
            'volume_tag': self.volume_tag,
            'kind': self.kind.value,
        }

    @classmethod
    def from_data(cls, data):
        receipt = SlotReceipt(data['job_id'], data['recording_id'], Outlet(data['outlet']), data['ordinal'])
        return cls(receipt, data['volume_tag'], FragmentKind(data['kind']))


@dataclass(frozen=True)
class TapeProvenance:
    side: TapeSide
    mode: str
    sources: tuple[EvidenceSource, ...]

    def to_data(self):
        return {'side': self.side.value, 'mode': self.mode,
                'sources': [source.to_data() for source in self.sources]}

    @classmethod
    def from_data(cls, data):
        return cls(TapeSide(data['side']), data['mode'],
                   tuple(EvidenceSource.from_data(source) for source in data['sources']))


def build_provenance(tapes: RecoveredTapes) -> tuple[TapeProvenance, ...]:
    provenance = []
    for side in TapeSide:
        support = tapes.support_for(side)
        sources = tuple(EvidenceSource(fragment.receipt, fragment.volume_tag, fragment.kind)
                        for fragment in support)
        mode = 'direct' if len(sources) == 1 else 'recovered'
        provenance.append(TapeProvenance(side, mode, sources))
    return tuple(provenance)
