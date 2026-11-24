from dataclasses import dataclass

from capture.custody import CustodyState
from capture.evidence import CustodySnapshot, snapshot_custody
from capture.shared import SharedSlotStore, shared_member_evidence
from capture.validation import FragmentKind
from device.recording import RecordingStatus, recording_status
from device.slot import Outlet
from jobs.lifecycle import JobLifecycle, JobPhase
from jobs.specification import JobSpec


class RecordingUnfinished(ValueError):
    pass


@dataclass(frozen=True)
class SealEvidence:
    job: JobSpec
    recording: RecordingStatus
    custody: CustodySnapshot


def _checks(records):
    return all(record.state is CustodyState.VERIFIED for record in records)


def _shared_settled(connection, job) -> bool:
    slot_id = SharedSlotStore(connection).job_slots(job.identity.job_id)
    custody = snapshot_custody(connection, job.identity.job_id)
    record, member, accepted = shared_member_evidence(connection, job.identity.job_id)
    if record is None or member is None or accepted is None:
        return False
    if accepted.slot_id != slot_id or accepted.write_id != member.frame['write_id']:
        return False
    if accepted.state not in (CustodyState.ACCEPTED, CustodyState.VERIFIED):
        return False
    if accepted.fragment.kind not in (FragmentKind.A, FragmentKind.B, FragmentKind.P):
        return False
    backing = [item for item in custody.records if item.receipt.outlet is Outlet.BACKUP]
    mains = [item for item in custody.records if item.receipt.outlet is Outlet.MAIN]
    if len(mains) != 1 or mains[0] != accepted:
        return False
    released = {item.receipt for item in custody.records
                if item.receipt.outlet is Outlet.BACKUP}
    status = recording_status(connection, job.identity.job_id)
    released_slots = {receipt for receipt in status.released if receipt.outlet is Outlet.BACKUP}
    backup = status.position(Outlet.BACKUP)
    if backup is None or not backup.ended:
        return False
    return _checks(backing) and released == released_slots


def require_settled_evidence(connection, job: JobSpec) -> SealEvidence:
    job_id = job.identity.job_id
    connection.execute('BEGIN')
    with connection:
        recording = recording_status(connection, job_id)
        custody = snapshot_custody(connection, job_id)
        shared = SharedSlotStore(connection).job_slots(job_id) is not None
        if shared:
            settled = _shared_settled(connection, job)
        else:
            accepted = {record.receipt for record in custody.records}
            checked = all(record.state is CustodyState.VERIFIED
                          and record.fragment.volume_tag == job.identity.volume_tag
                          for record in custody.records)
            settled = recording.ended and checked and accepted == recording.released
        phase = JobLifecycle(job).phase(attached=recording.recording_id is not None, settled=settled)
        if phase is not JobPhase.SETTLED:
            raise RecordingUnfinished('both outlets and original custody must be settled')
    return SealEvidence(job, recording, custody)
