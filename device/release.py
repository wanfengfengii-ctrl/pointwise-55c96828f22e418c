from enum import StrEnum

from capture.shared import SharedSlotStore, query_shared_release, release_shared_slot
from device.replay_state import ReplayState
from device.slot import Outlet, SlotReceipt, SlotState


class ReleaseOutcome(StrEnum):
    RELEASED = 'released'
    PENDING = 'pending'


class ReleaseFault(StrEnum):
    BEFORE = 'before'
    AFTER = 'after'


def query_release(connection, receipt: SlotReceipt) -> SlotState:
    return ReplayState(connection).lookup_release(receipt)


def release_slot(connection, receipt: SlotReceipt, *, fault=None) -> ReleaseOutcome:
    if (receipt.outlet is Outlet.MAIN
            and SharedSlotStore(connection).job_slots(receipt.job_id) is not None):
        raise ValueError('a shared carrier slot cannot be released through the normal outlet')
    replay = ReplayState(connection)
    fault = ReleaseFault(fault) if fault is not None else None
    state = replay.lookup_release(receipt)
    if state is SlotState.RELEASED:
        return ReleaseOutcome.RELEASED
    if state is not SlotState.CURRENT:
        raise ValueError('release requires a known current slot')
    if fault is ReleaseFault.BEFORE:
        return ReleaseOutcome.PENDING
    applied = replay.advance(receipt)
    if applied and fault is ReleaseFault.AFTER:
        return ReleaseOutcome.PENDING
    return ReleaseOutcome.RELEASED
