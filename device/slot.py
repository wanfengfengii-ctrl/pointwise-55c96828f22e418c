from dataclasses import dataclass
from enum import StrEnum


class Outlet(StrEnum):
    MAIN = 'main'
    BACKUP = 'backup'


class SlotState(StrEnum):
    CURRENT = 'current'
    RELEASED = 'released'
    UNKNOWN = 'unknown'


@dataclass(frozen=True)
class SlotReceipt:
    job_id: str
    recording_id: str
    outlet: Outlet
    ordinal: int

    def __post_init__(self):
        if not isinstance(self.outlet, Outlet):
            raise ValueError('slot receipt requires an outlet')
        if type(self.ordinal) is not int or not 0 <= self.ordinal < 2**63:
            raise ValueError('invalid slot ordinal')

    def sort_key(self):
        return (self.outlet is Outlet.BACKUP, self.ordinal)
