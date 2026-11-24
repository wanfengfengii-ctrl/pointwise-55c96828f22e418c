from dataclasses import dataclass
from enum import StrEnum
from zlib import crc32

from device.recording import RecordedFragment
from device.slot import Outlet, SlotReceipt
from jobs.specification import JobSpec


class FragmentKind(StrEnum):
    A = 'A'
    B = 'B'
    P = 'P'


class InvalidFragment(ValueError):
    pass


@dataclass(frozen=True)
class ValidatedFragment:
    receipt: SlotReceipt
    volume_tag: str
    kind: FragmentKind
    payload: bytes
    checksum: int


def validate_fragment(job: JobSpec, outlet: Outlet, receipt: SlotReceipt,
                      fragment: RecordedFragment) -> ValidatedFragment:
    if (fragment.receipt != receipt or receipt.outlet != outlet
            or receipt.job_id != job.identity.job_id):
        raise InvalidFragment('fragment does not match the current slot binding')
    if fragment.volume_tag != job.identity.volume_tag:
        raise InvalidFragment('fragment volume tag does not match the job')
    try:
        kind = FragmentKind(fragment.kind)
    except ValueError as error:
        raise InvalidFragment('unsupported fragment kind') from error
    if not isinstance(fragment.payload, bytes) or len(fragment.payload) != job.length:
        raise InvalidFragment('fragment byte length does not match the job')
    if type(fragment.checksum) is not int or crc32(fragment.payload) != fragment.checksum:
        raise InvalidFragment('fragment CRC32 does not match the payload')
    return ValidatedFragment(receipt, fragment.volume_tag, kind, fragment.payload, fragment.checksum)
