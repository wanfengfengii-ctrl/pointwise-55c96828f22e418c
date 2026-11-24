import sqlite3
from dataclasses import dataclass

from device.position import OutletPosition, advance_outlet, finish_outlet
from device.slot import Outlet, SlotReceipt, SlotState


@dataclass(frozen=True)
class ReplayFrame:
    volume_tag: str
    kind: str
    payload: bytes
    checksum: int


@dataclass(frozen=True)
class ReplayOutlet:
    frames: tuple[ReplayFrame, ...]
    end_event: bool


class ReplayState:
    def __init__(self, connection: sqlite3.Connection):
        self.connection = connection

    def initialize(self):
        if self.connection.in_transaction:
            raise RuntimeError('initialize replay outside a transaction')
        self.connection.executescript('''
            CREATE TABLE IF NOT EXISTS replay_recordings (
                job_id TEXT PRIMARY KEY REFERENCES job_specs (job_id),
                recording_id TEXT NOT NULL,
                fingerprint TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS replay_outlets (
                job_id TEXT NOT NULL REFERENCES replay_recordings (job_id),
                outlet TEXT NOT NULL CHECK (outlet IN ('main', 'backup')),
                next_slot INTEGER NOT NULL,
                fragment_count INTEGER NOT NULL,
                end_event INTEGER NOT NULL,
                ended INTEGER NOT NULL,
                PRIMARY KEY (job_id, outlet)
            );
            CREATE TABLE IF NOT EXISTS replay_frames (
                job_id TEXT NOT NULL,
                outlet TEXT NOT NULL,
                ordinal INTEGER NOT NULL,
                volume_tag TEXT NOT NULL,
                kind TEXT NOT NULL,
                payload BLOB NOT NULL,
                checksum INTEGER NOT NULL,
                PRIMARY KEY (job_id, outlet, ordinal),
                FOREIGN KEY (job_id, outlet) REFERENCES replay_outlets (job_id, outlet)
            );
            CREATE TABLE IF NOT EXISTS replay_releases (
                job_id TEXT NOT NULL,
                outlet TEXT NOT NULL,
                ordinal INTEGER NOT NULL,
                PRIMARY KEY (job_id, outlet, ordinal),
                FOREIGN KEY (job_id, outlet) REFERENCES replay_outlets (job_id, outlet)
            );
        ''')

    def attach(self, job_id, recording_id, fingerprint, outlets, check_attachment):
        self.connection.execute('BEGIN IMMEDIATE')
        with self.connection:
            existing = self.connection.execute('''
                SELECT recording_id, fingerprint FROM replay_recordings WHERE job_id = ?
            ''', (job_id,)).fetchone()
            previous = None if existing is None else tuple(existing)
            check_attachment(previous, (recording_id, fingerprint))
            if previous is not None:
                return
            self.connection.execute('''
                INSERT INTO replay_recordings (job_id, recording_id, fingerprint)
                VALUES (?, ?, ?)
            ''', (job_id, recording_id, fingerprint))
            for outlet, entry in outlets.items():
                position = finish_outlet(OutletPosition(outlet, 0, len(entry.frames), entry.end_event))
                self.connection.execute('''
                    INSERT INTO replay_outlets
                        (job_id, outlet, next_slot, fragment_count, end_event, ended)
                    VALUES (?, ?, ?, ?, ?, ?)
                ''', (job_id, outlet, position.next_slot, position.fragment_count,
                      position.end_event, position.ended))
                self.connection.executemany('''
                    INSERT INTO replay_frames
                        (job_id, outlet, ordinal, volume_tag, kind, payload, checksum)
                    VALUES (?, ?, ?, ?, ?, ?, ?)
                ''', ((job_id, outlet, ordinal, frame.volume_tag, frame.kind,
                       frame.payload, frame.checksum)
                      for ordinal, frame in enumerate(entry.frames)))

    def read_current(self, job_id, outlet):
        row = self.connection.execute('''
            SELECT r.recording_id, f.ordinal, f.volume_tag, f.kind, f.payload, f.checksum
            FROM replay_recordings AS r
            JOIN replay_outlets AS o ON o.job_id = r.job_id
            JOIN replay_frames AS f
              ON f.job_id = o.job_id AND f.outlet = o.outlet AND f.ordinal = o.next_slot
            WHERE r.job_id = ? AND o.outlet = ?
        ''', (job_id, outlet)).fetchone()
        if row is None:
            return None
        return SlotReceipt(job_id, row[0], outlet, row[1]), ReplayFrame(*row[2:])

    def _release_position(self, receipt: SlotReceipt):
        return self.connection.execute('''
            SELECT o.next_slot, o.fragment_count, o.end_event, o.ended,
                   EXISTS (
                       SELECT 1 FROM replay_releases AS h
                       WHERE h.job_id = o.job_id AND h.outlet = o.outlet AND h.ordinal = ?
                   )
            FROM replay_recordings AS r
            JOIN replay_outlets AS o ON o.job_id = r.job_id
            WHERE r.job_id = ? AND r.recording_id = ? AND o.outlet = ?
        ''', (receipt.ordinal, receipt.job_id, receipt.recording_id, receipt.outlet)).fetchone()

    def advance(self, receipt: SlotReceipt) -> bool:
        self.connection.execute('BEGIN IMMEDIATE')
        with self.connection:
            row = self._release_position(receipt)
            if row is None:
                raise ValueError('unknown recording or outlet')
            next_slot, count, end_event, ended, already_released = row
            if already_released:
                return False
            position = OutletPosition(receipt.outlet, next_slot, count, bool(end_event), bool(ended))
            advanced = advance_outlet(position, receipt.ordinal)
            key = (receipt.job_id, receipt.outlet, receipt.ordinal)
            deleted = self.connection.execute('''
                DELETE FROM replay_frames WHERE job_id = ? AND outlet = ? AND ordinal = ?
            ''', key)
            if deleted.rowcount != 1:
                raise ValueError('no payload in the current slot')
            self.connection.execute('''
                INSERT INTO replay_releases (job_id, outlet, ordinal) VALUES (?, ?, ?)
            ''', key)
            self.connection.execute('''
                UPDATE replay_outlets SET next_slot = ?, ended = ?
                WHERE job_id = ? AND outlet = ?
            ''', (advanced.next_slot, advanced.ended, receipt.job_id, receipt.outlet))
        return True

    def lookup_release(self, receipt: SlotReceipt) -> SlotState:
        row = self._release_position(receipt)
        if row is None:
            return SlotState.UNKNOWN
        if row[4]:
            return SlotState.RELEASED
        if not row[3] and receipt.ordinal == row[0] < row[1]:
            return SlotState.CURRENT
        return SlotState.UNKNOWN

    def status(self, job_id):
        rows = self.connection.execute('''
            SELECT r.recording_id, o.outlet, o.next_slot, o.fragment_count,
                   o.end_event, o.ended, h.ordinal
            FROM replay_recordings AS r
            JOIN replay_outlets AS o ON o.job_id = r.job_id
            LEFT JOIN replay_releases AS h ON h.job_id = o.job_id AND h.outlet = o.outlet
            WHERE r.job_id = ?
        ''', (job_id,)).fetchall()
        if not rows:
            return None, (), frozenset()
        positions = {}
        released = set()
        for recording_id, name, next_slot, count, event, ended, ordinal in rows:
            outlet = Outlet(name)
            positions[outlet] = OutletPosition(outlet, next_slot, count, bool(event), bool(ended))
            if ordinal is not None:
                released.add(SlotReceipt(job_id, recording_id, outlet, ordinal))
        return rows[0][0], tuple(positions[outlet] for outlet in Outlet), frozenset(released)
