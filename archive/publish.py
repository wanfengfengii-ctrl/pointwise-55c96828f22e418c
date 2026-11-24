import sqlite3

from archive.history import ArchivedVolume, find_by_job
from archive.receipt import issue_first_receipt
from archive.storage import ArchiveStore


class PublishFailed(RuntimeError):
    pass


def publish_volume(store: ArchiveStore, job_id: str, tapes, provenance,
                   *, before_commit=None) -> ArchivedVolume:
    try:
        with store.transaction() as connection:
            original = find_by_job(store, job_id, connection)
            if original is not None:
                return original
            number = f'VOL-{job_id}'
            receipt = issue_first_receipt(job_id, number)
            sources = [entry.to_data() for entry in provenance]
            store.insert_volume(connection, number, job_id, tapes.a, tapes.b, sources)
            store.insert_receipt(connection, receipt.receipt_id, number)
            result = find_by_job(store, job_id, connection)
            if before_commit is not None:
                before_commit()
    except sqlite3.Error as error:
        raise PublishFailed(f'archive transaction failed for job {job_id!r}') from error
    return result
