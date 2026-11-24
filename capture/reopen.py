from capture.custody import CustodyRecord, CustodyState, CustodyStore
from device.release import ReleaseOutcome, query_release, release_slot
from device.slot import SlotState


def settle_release_reply(store: CustodyStore, record: CustodyRecord,
                         reply: ReleaseOutcome) -> CustodyRecord:
    state = CustodyState.VERIFIED if reply is ReleaseOutcome.RELEASED else CustodyState.PENDING
    return store.record_release_status(record.receipt, state)


def resume_release(connection, store: CustodyStore, record: CustodyRecord,
                   *, fault=None) -> CustodyRecord:
    if record.state is CustodyState.VERIFIED:
        return record
    observed = query_release(connection, record.receipt)
    if observed is SlotState.RELEASED:
        return settle_release_reply(store, record, ReleaseOutcome.RELEASED)
    if observed is not SlotState.CURRENT:
        return settle_release_reply(store, record, ReleaseOutcome.PENDING)
    current = store.record_release_status(record.receipt, CustodyState.ACCEPTED)
    if current.state is CustodyState.VERIFIED:
        return current
    reply = release_slot(connection, current.receipt, fault=fault)
    return settle_release_reply(store, current, reply)
