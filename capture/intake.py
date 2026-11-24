from dataclasses import dataclass
from enum import StrEnum

from capture.custody import CustodyRecord, CustodyState, CustodyStore
from capture.reopen import resume_release
from capture.validation import InvalidFragment, validate_fragment
from device.recording import read_current, recording_status
from device.release import ReleaseFault
from device.slot import Outlet, SlotReceipt
from jobs.registration import get_job


class StepState(StrEnum):
    RELEASED = 'released'
    PENDING = 'pending'
    ENDED = 'ended'
    WAITING = 'waiting'


@dataclass(frozen=True)
class StepResult:
    state: StepState
    record: CustodyRecord | None = None


def step(connection, job_id: str, outlet, receipt: SlotReceipt | None = None,
         *, release_fault: ReleaseFault | None = None) -> StepResult:
    job = get_job(connection, job_id)
    try:
        outlet = Outlet(outlet)
    except ValueError as error:
        raise InvalidFragment('unknown outlet') from error
    if release_fault is not None:
        release_fault = ReleaseFault(release_fault)
    if receipt is not None and (not isinstance(receipt, SlotReceipt)
                                or receipt.job_id != job_id or receipt.outlet != outlet):
        raise InvalidFragment('receipt belongs to another job or outlet')
    store = CustodyStore(connection)
    original = None if receipt is None else store.get(receipt)
    if original is None:
        current = read_current(connection, job_id, outlet)
        if current is None and receipt is None:
            position = recording_status(connection, job_id).position(outlet)
            state = StepState.ENDED if position is not None and position.ended else StepState.WAITING
            return StepResult(state)
        if current is None:
            # The caller may have supplied a receipt another worker just accepted and released.
            original = store.get(receipt)
            if original is None:
                raise InvalidFragment('no current slot for the supplied receipt')
        elif receipt is not None and current.receipt != receipt:
            original = store.get(receipt)
            if original is None:
                raise InvalidFragment('receipt does not identify the current slot')
        else:
            fragment = validate_fragment(job, outlet, current.receipt, current)
            original = store.accept(fragment)
    settled = resume_release(connection, store, original, fault=release_fault)
    status = StepState.RELEASED if settled.state is CustodyState.VERIFIED else StepState.PENDING
    return StepResult(status, settled)
