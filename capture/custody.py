import sqlite3
from dataclasses import dataclass
from enum import StrEnum

from capture.validation import FragmentKind, InvalidFragment, ValidatedFragment
from device.slot import Outlet, SlotReceipt


class CustodyState(StrEnum):
    ACCEPTED = 'accepted'
    PENDING = 'pending'
    VERIFIED = 'verified'


@dataclass(frozen=True)
class CustodyRecord:
    fragment: ValidatedFragment
    state: CustodyState

    @property
    def receipt(self):
        return self.fragment.receipt


def _key(receipt):
    return receipt.job_id, receipt.recording_id, receipt.outlet, receipt.ordinal


def _decode(row):
    job, recording, outlet, ordinal, volume, kind, payload, checksum, state = row
    receipt = SlotReceipt(job, recording, Outlet(outlet), ordinal)
    return CustodyRecord(ValidatedFragment(receipt, volume, FragmentKind(kind), payload, checksum),
                         CustodyState(state))


class CustodyStore:
    def __init__(self, connection: sqlite3.Connection):
        self.connection = connection

    def initialize(self):
        if self.connection.in_transaction:
            raise RuntimeError('initialize custody outside a transaction')
        self.connection.execute('''
            CREATE TABLE IF NOT EXISTS custody (
                job_id TEXT NOT NULL REFERENCES job_specs (job_id),
                recording_id TEXT NOT NULL,
                outlet TEXT NOT NULL CHECK (outlet IN ('main', 'backup')),
                ordinal INTEGER NOT NULL,
                volume_tag TEXT NOT NULL,
                kind TEXT NOT NULL CHECK (kind IN ('A', 'B', 'P')),
                payload BLOB NOT NULL CHECK (length(payload) > 0),
                checksum INTEGER NOT NULL,
                state TEXT NOT NULL CHECK (state IN ('accepted', 'pending', 'verified')),
                PRIMARY KEY (job_id, recording_id, outlet, ordinal)
            )
        ''')

    def accept(self, fragment: ValidatedFragment) -> CustodyRecord:
        self.connection.execute('BEGIN IMMEDIATE')
        with self.connection:
            self.connection.execute('''
                INSERT INTO custody
                    (job_id, recording_id, outlet, ordinal, volume_tag, kind, payload, checksum, state)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT (job_id, recording_id, outlet, ordinal) DO NOTHING
            ''', (*_key(fragment.receipt), fragment.volume_tag, fragment.kind,
                  fragment.payload, fragment.checksum, CustodyState.ACCEPTED))
            original = self.get(fragment.receipt)
            if original.fragment != fragment:
                raise InvalidFragment('slot receipt already has different custody facts')
        return original

    def get(self, receipt: SlotReceipt) -> CustodyRecord | None:
        row = self.connection.execute('''
            SELECT job_id, recording_id, outlet, ordinal, volume_tag, kind, payload, checksum, state
            FROM custody WHERE job_id = ? AND recording_id = ? AND outlet = ? AND ordinal = ?
        ''', _key(receipt)).fetchone()
        return None if row is None else _decode(row)

    def record_release_status(self, receipt: SlotReceipt, state: CustodyState) -> CustodyRecord:
        state = CustodyState(state)
        self.connection.execute('BEGIN IMMEDIATE')
        with self.connection:
            original = self.get(receipt)
            if original is None:
                raise InvalidFragment('release status requires original custody')
            if original.state is CustodyState.VERIFIED or original.state is state:
                return original
            self.connection.execute('''
                UPDATE custody SET state = ?
                WHERE job_id = ? AND recording_id = ? AND outlet = ? AND ordinal = ?
            ''', (state, *_key(receipt)))
        return CustodyRecord(original.fragment, state)

    def list_for_job(self, job_id: str) -> tuple[CustodyRecord, ...]:
        rows = self.connection.execute('''
            SELECT job_id, recording_id, outlet, ordinal, volume_tag, kind, payload, checksum, state
            FROM custody WHERE job_id = ?
            ORDER BY CASE outlet WHEN 'main' THEN 0 ELSE 1 END, ordinal, recording_id
        ''', (job_id,))
        return tuple(_decode(row) for row in rows)
