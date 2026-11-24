import json
from dataclasses import dataclass

from archive.receipt import FirstReceipt, restore_first_receipt
from archive.storage import ArchiveStore
from decision.provenance import TapeProvenance


@dataclass(frozen=True)
class ArchivedVolume:
    number: str
    job_id: str
    a: bytes
    b: bytes
    provenance: tuple[TapeProvenance, ...]
    receipt: FirstReceipt


def _restore(row):
    if row is None:
        return None
    receipt = restore_first_receipt(row['receipt_id'], row['number'], row['job_id'])
    provenance = tuple(TapeProvenance.from_data(tape) for tape in json.loads(row['provenance']))
    return ArchivedVolume(row['number'], row['job_id'], row['tape_a'], row['tape_b'], provenance, receipt)


def find_by_job(store: ArchiveStore, job_id: str, connection=None) -> ArchivedVolume | None:
    return _restore(store.fetch_by_job(job_id, connection))


def find_by_number(store: ArchiveStore, number: str) -> ArchivedVolume | None:
    return _restore(store.fetch_by_number(number))
