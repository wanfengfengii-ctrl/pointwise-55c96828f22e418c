from dataclasses import dataclass

from capture.custody import CustodyState
from capture.evidence import CustodySnapshot, snapshot_custody
from device.recording import RecordingStatus, recording_status
from jobs.lifecycle import JobLifecycle, JobPhase
from jobs.specification import JobSpec


class RecordingUnfinished(ValueError):
    pass


@dataclass(frozen=True)
class SealEvidence:
    job: JobSpec
    recording: RecordingStatus
    custody: CustodySnapshot


def require_settled_evidence(connection, job: JobSpec) -> SealEvidence:
    job_id = job.identity.job_id
    connection.execute('BEGIN')
    with connection:
        recording = recording_status(connection, job_id)
        custody = snapshot_custody(connection, job_id)
        accepted = {record.receipt for record in custody.records}
        checked = all(record.state is CustodyState.VERIFIED
                      and record.fragment.volume_tag == job.identity.volume_tag
                      for record in custody.records)
        settled = recording.ended and checked and accepted == recording.released
        phase = JobLifecycle(job).phase(attached=recording.recording_id is not None, settled=settled)
        if phase is not JobPhase.SETTLED:
            raise RecordingUnfinished('both outlets and original custody must be settled')
    return SealEvidence(job, recording, custody)
