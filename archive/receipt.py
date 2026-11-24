from dataclasses import dataclass


@dataclass(frozen=True)
class FirstReceipt:
    receipt_id: str
    volume_number: str
    job_id: str


def issue_first_receipt(job_id: str, volume_number: str) -> FirstReceipt:
    return FirstReceipt(f'FIRST-{volume_number}', volume_number, job_id)


def restore_first_receipt(receipt_id: str, volume_number: str, job_id: str) -> FirstReceipt:
    return FirstReceipt(receipt_id, volume_number, job_id)
