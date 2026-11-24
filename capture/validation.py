from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
from typing import TYPE_CHECKING
from zlib import crc32

from device.slot import Outlet, SlotReceipt
from jobs.specification import JobSpec

if TYPE_CHECKING:
    from device.recording import RecordedFragment


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


def validate_payload(volume_tag: str, kind: str, payload: bytes, checksum: int,
                     length: int) -> tuple[str, FragmentKind, bytes, int]:
    """Check one fragment's own shape: volume, kind, byte length and CRC32."""
    if not isinstance(volume_tag, str) or not volume_tag:
        raise InvalidFragment('fragment volume tag does not match the job')
    try:
        kind = FragmentKind(kind)
    except ValueError as error:
        raise InvalidFragment('unsupported fragment kind') from error
    if not isinstance(payload, bytes) or len(payload) != length:
        raise InvalidFragment('fragment byte length does not match the job')
    if type(checksum) is not int or crc32(payload) != checksum:
        raise InvalidFragment('fragment CRC32 does not match the payload')
    return volume_tag, kind, payload, checksum


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


