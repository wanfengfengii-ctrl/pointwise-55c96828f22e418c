from dataclasses import dataclass, replace

from device.slot import Outlet


@dataclass(frozen=True)
class OutletPosition:
    outlet: Outlet
    next_slot: int
    fragment_count: int
    end_event: bool
    ended: bool = False


def finish_outlet(position: OutletPosition) -> OutletPosition:
    if position.next_slot == position.fragment_count and position.end_event:
        return replace(position, ended=True)
    return position


def advance_outlet(position: OutletPosition, ordinal: int) -> OutletPosition:
    if (position.ended or ordinal != position.next_slot
            or ordinal >= position.fragment_count):
        raise ValueError('release requires the current slot of this outlet')
    return finish_outlet(replace(position, next_slot=ordinal + 1))
