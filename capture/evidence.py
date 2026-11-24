from dataclasses import dataclass

from capture.custody import CustodyRecord, CustodyStore


@dataclass(frozen=True)
class CustodySnapshot:
    job_id: str
    records: tuple[CustodyRecord, ...]


def snapshot_custody(connection, job_id: str) -> CustodySnapshot:
    return CustodySnapshot(job_id, CustodyStore(connection).list_for_job(job_id))
