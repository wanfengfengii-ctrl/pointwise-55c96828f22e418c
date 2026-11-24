import hashlib
import json
import sqlite3
from dataclasses import dataclass

from capture.custody import CustodyRecord, CustodyStore
from capture.validation import InvalidFragment, validate_payload
from device.position import OutletPosition, finish_outlet
from device.replay_state import ReplayOutlet
from device.slot import Outlet, SlotReceipt
from jobs.registration import get_job


def _unhex(value, field='hex'):
    if not isinstance(value, str):
        raise ValueError(f'shared member {field} must be hex text')
    try:
        return bytes.fromhex(value)
    except ValueError as error:
        raise ValueError(f'shared member {field} is not valid binary data') from error


def _decode_frame(value, default_volume):
    if not isinstance(value, dict):
        raise ValueError('a shared member frame must be an object')
    volume = value.get('volume_tag', default_volume)
    kind, checksum = value.get('kind'), value.get('crc32')
    if not isinstance(volume, str) or not volume.strip():
        raise ValueError('shared member frame volume must be nonempty text')
    if not isinstance(kind, str) or not kind:
        raise ValueError('shared member frame kind must be nonempty text')
    if type(checksum) is not int or not 0 <= checksum < 2 ** 32:
        raise ValueError('shared member frame CRC32 must be an unsigned 32-bit integer')
    payload = _unhex(value.get('hex'))
    return {'volume_tag': volume, 'kind': kind, 'hex': payload.hex(), 'crc32': checksum}


def _write_identity(frame, series):
    canonical = json.dumps([series, frame['volume_tag'], frame['kind'], frame['hex'], frame['crc32']],
                           separators=(',', ':'))
    return 'w-' + hashlib.sha256(canonical.encode('utf-8')).hexdigest()[:32]


@dataclass(frozen=True)
class SharedMember:
    slot_id: str
    job_id: str
    recording_id: str
    volume_tag: str
    slot_index: int
    frame: dict


@dataclass(frozen=True)
class SharedSlotRecord:
    slot_id: str
    members: tuple[SharedMember, ...]
    released: bool

    def member(self, job_id):
        return next((member for member in self.members if member.job_id == job_id), None)


class SharedSlotStore:
    def __init__(self, connection: sqlite3.Connection):
        self.connection = connection

    def initialize(self):
        if self.connection.in_transaction:
            raise RuntimeError('initialize shared slots outside a transaction')
        self.connection.executescript('''
            CREATE TABLE IF NOT EXISTS shared_slots (
                slot_id TEXT PRIMARY KEY,
                released INTEGER NOT NULL DEFAULT 0 CHECK (released IN (0, 1))
            );
            CREATE TABLE IF NOT EXISTS shared_members (
                slot_id TEXT NOT NULL REFERENCES shared_slots (slot_id),
                member_index INTEGER NOT NULL CHECK (member_index IN (0, 1)),
                job_id TEXT NOT NULL UNIQUE REFERENCES job_specs (job_id),
                recording_id TEXT NOT NULL,
                volume_tag TEXT NOT NULL,
                slot_index INTEGER NOT NULL,
                volume_frame TEXT NOT NULL,
                series INTEGER NOT NULL,
                write_id TEXT NOT NULL,
                PRIMARY KEY (slot_id, member_index)
            );
        ''')

    def load(self, slot_id):
        slot = self.connection.execute(
            'SELECT released FROM shared_slots WHERE slot_id = ?', (slot_id,)).fetchone()
        if slot is None:
            return None
        rows = self.connection.execute('''
            SELECT job_id, recording_id, volume_tag, slot_index, volume_frame, write_id
            FROM shared_members WHERE slot_id = ? ORDER BY member_index
        ''', (slot_id,)).fetchall()
        members = tuple(
            SharedMember(slot_id, job_id, recording_id, volume_tag, slot_index,
                         {**json.loads(volume_frame), 'write_id': write_id})
            for job_id, recording_id, volume_tag, slot_index, volume_frame, write_id in rows)
        return SharedSlotRecord(slot_id, members, bool(slot[0]))

    def job_slots(self, job_id):
        row = self.connection.execute(
            'SELECT slot_id FROM shared_members WHERE job_id = ?', (job_id,)).fetchone()
        return None if row is None else row[0]

    def mark_released(self, slot_id):
        self.connection.execute(
            'UPDATE shared_slots SET released = 1 WHERE slot_id = ?', (slot_id,))


def _write_backup(connection, job_id, recording_id, fingerprint, frames, end_event):
    connection.execute('''
        INSERT INTO replay_recordings (job_id, recording_id, fingerprint) VALUES (?, ?, ?)
    ''', (job_id, recording_id, fingerprint))
    for outlet, entry in ((Outlet.MAIN, ReplayOutlet((), False)),
                          (Outlet.BACKUP, ReplayOutlet(frames, end_event))):
        position = finish_outlet(OutletPosition(outlet, 0, len(entry.frames), entry.end_event))
        connection.execute('''
            INSERT INTO replay_outlets
                (job_id, outlet, next_slot, fragment_count, end_event, ended)
            VALUES (?, ?, ?, ?, ?, ?)
        ''', (job_id, outlet, position.next_slot, position.fragment_count,
              position.end_event, position.ended))
        connection.executemany('''
            INSERT INTO replay_frames
                (job_id, outlet, ordinal, volume_tag, kind, payload, checksum)
            VALUES (?, ?, ?, ?, ?, ?, ?)
        ''', ((job_id, outlet, ordinal, frame['volume_tag'], frame['kind'],
               bytes.fromhex(frame['hex']), frame['crc32'])
              for ordinal, frame in enumerate(entry.frames)))


def attach_shared_slot(connection: sqlite3.Connection, slot_id: str, members) -> SharedSlotRecord:
    if not isinstance(slot_id, str) or not slot_id.strip():
        raise ValueError('slot id must be nonempty text')
    if not isinstance(members, (list, tuple)) or len(members) != 2:
        raise ValueError('a shared carrier requires exactly two members')
    store = SharedSlotStore(connection)
    decoded, seen = [], set()
    for entry in members:
        if not isinstance(entry, dict):
            raise ValueError('each shared member must be an object')
        job_id, recording_id = entry.get('job_id'), entry.get('recording_id')
        if not all(isinstance(value, str) and value.strip() for value in (job_id, recording_id)):
            raise ValueError('shared member job and recording ids must be nonempty text')
        if job_id in seen:
            raise ValueError('each job may occupy only one shared member')
        seen.add(job_id)
        try:
            job = get_job(connection, job_id)
        except KeyError as error:
            raise ValueError(f'job {job_id!r} is not registered') from error
        main = _decode_frame(entry.get('main'), job.identity.volume_tag)
        backup = entry.get('backup')
        if (not isinstance(backup, dict) or type(backup.get('end')) is not bool
                or not isinstance(backup.get('frames'), list)):
            raise ValueError('a shared member backup requires frames and an explicit end flag')
        frames = tuple(_decode_frame(frame, job.identity.volume_tag)
                       for frame in backup['frames'])
        decoded.append((job, recording_id, main, frames, backup['end']))

    connection.execute('BEGIN IMMEDIATE')
    with connection:
        if store.load(slot_id) is not None:
            raise ValueError('slot id already carries an attachment')
        for job, _, _, _, _ in decoded:
            if store.job_slots(job.identity.job_id) is not None:
                raise ValueError(f'job {job.identity.job_id!r} already carries a shared recording')
        connection.execute('INSERT INTO shared_slots (slot_id, released) VALUES (?, 0)', (slot_id,))
        for index, (job, recording_id, main, frames, end_event) in enumerate(decoded):
            write_id = _write_identity(main, 0)
            connection.execute('''
                INSERT INTO shared_members
                    (slot_id, member_index, job_id, recording_id, volume_tag, slot_index,
                     volume_frame, series, write_id)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            ''', (slot_id, index, job.identity.job_id, recording_id, job.identity.volume_tag, index,
                  json.dumps(main, separators=(',', ':')), 0, write_id))
            existing = connection.execute(
                'SELECT 1 FROM replay_recordings WHERE job_id = ?',
                (job.identity.job_id,)).fetchone()
            if existing is not None:
                raise ValueError(f'job {job.identity.job_id!r} already has a recording')
            fingerprint = hashlib.sha256(json.dumps(
                [recording_id, job.identity.volume_tag, slot_id, index, main, list(frames), end_event],
                separators=(',', ':')).encode('utf-8')).hexdigest()
            _write_backup(connection, job.identity.job_id, recording_id, fingerprint, frames, end_event)
    return store.load(slot_id)


def _packet(member: SharedMember):
    frame = member.frame
    return {
        'slot_id': member.slot_id,
        'job_id': member.job_id,
        'recording_id': member.recording_id,
        'volume_tag': frame['volume_tag'],
        'kind': frame['kind'],
        'payload': bytes.fromhex(frame['hex']),
        'checksum': frame['crc32'],
        'write_id': frame['write_id'],
        'proof': frame['write_id'],
    }


def read_shared_member(connection, slot_id: str, job_id: str):
    record = SharedSlotStore(connection).load(slot_id)
    if record is None or record.released:
        return None
    member = record.member(job_id)
    return None if member is None else _packet(member)


def _check_packet(packet, member: SharedMember):
    if not isinstance(packet, dict):
        raise ValueError('a shared member packet is required')
    if packet.get('slot_id') != member.slot_id:
        raise ValueError('packet belongs to another shared slot')
    if packet.get('job_id') != member.job_id:
        raise ValueError('packet belongs to another job')
    if packet.get('recording_id') != member.recording_id:
        raise ValueError('packet belongs to another recording')


def rewrite_shared_member(connection, packet, frame):
    if not isinstance(packet, dict):
        raise ValueError('a shared member packet is required')
    store = SharedSlotStore(connection)
    record = store.load(packet.get('slot_id'))
    if record is None:
        raise ValueError('unknown shared slot')
    member = record.member(packet.get('job_id'))
    if member is None:
        raise ValueError('packet does not belong to this shared member')
    _check_packet(packet, member)
    if packet.get('write_id') != member.frame['write_id'] or packet.get('proof') != packet.get('write_id'):
        raise ValueError('packet write identity is no longer valid')
    if (packet.get('payload') != bytes.fromhex(member.frame['hex'])
            or packet.get('checksum') != member.frame['crc32']
            or packet.get('kind') != member.frame['kind']):
        raise ValueError('packet bytes do not match the actual member write')
    if record.released:
        raise ValueError('a released shared slot cannot be rewritten')
    if CustodyStore(connection).get(_receipt(member)) is not None:
        raise ValueError('an accepted shared member cannot be rewritten')
    job = get_job(connection, member.job_id)
    new_frame = _decode_frame(frame, job.identity.volume_tag)
    connection.execute('BEGIN IMMEDIATE')
    with connection:
        if CustodyStore(connection).get(_receipt(member)) is not None:
            raise ValueError('an accepted shared member cannot be rewritten')
        series = connection.execute('''
            SELECT series FROM shared_members WHERE slot_id = ? AND job_id = ?
        ''', (member.slot_id, member.job_id)).fetchone()[0] + 1
        write_id = _write_identity(new_frame, series)
        changed = connection.execute('''
            UPDATE shared_members SET volume_frame = ?, series = ?, write_id = ?
            WHERE slot_id = ? AND job_id = ? AND write_id = ?
        ''', (json.dumps(new_frame, separators=(',', ':')), series, write_id,
              member.slot_id, member.job_id, packet['write_id']))
        if changed.rowcount != 1:
            raise ValueError('packet write identity is no longer valid')
    return read_shared_member(connection, member.slot_id, member.job_id)


def _receipt(member: SharedMember):
    return SlotReceipt(member.job_id, member.recording_id, Outlet.MAIN, member.slot_index)


def _result(record: CustodyRecord):
    return {'slot_id': record.slot_id, 'job_id': record.receipt.job_id,
            'recording_id': record.receipt.recording_id, 'write_id': record.write_id}


def _validate_member_packet(connection, packet, member: SharedMember, job) -> bytes:
    if not isinstance(packet, dict):
        raise InvalidFragment('a shared member packet is required')
    try:
        _check_packet(packet, member)
    except ValueError as error:
        raise InvalidFragment(str(error)) from error
    payload = packet.get('payload')
    if payload != bytes.fromhex(member.frame['hex']):
        raise InvalidFragment('packet bytes do not match the actual member write')
    if packet.get('checksum') != member.frame['crc32']:
        raise InvalidFragment('packet CRC32 does not match the actual member write')
    if packet.get('kind') != member.frame['kind']:
        raise InvalidFragment('packet kind does not match the actual member write')
    if packet.get('volume_tag') != job.identity.volume_tag:
        raise InvalidFragment('fragment volume tag does not match the job')
    if packet.get('write_id') != member.frame['write_id']:
        raise InvalidFragment('packet write identity is not the current actual write')
    if not isinstance(packet.get('proof'), str) or packet['proof'] != member.frame['write_id']:
        raise InvalidFragment('packet proof does not verify the current actual write')
    validate_payload(packet.get('volume_tag'), packet.get('kind'), payload,
                     packet.get('checksum'), job.length)
    return payload


def accept_shared_member(connection, job_id: str, packet, *, before_commit=None):
    job = get_job(connection, job_id)
    store = SharedSlotStore(connection)
    slot_id = store.job_slots(job_id)
    if slot_id is None:
        raise InvalidFragment(f'job {job_id!r} has no shared member')
    connection.execute('BEGIN IMMEDIATE')
    try:
        record = store.load(slot_id)
        member = record.member(job_id)
        receipt = _receipt(member)
        custody = CustodyStore(connection)
        original = custody.get(receipt)
        if original is not None:
            _validate_member_packet(connection, packet, member, job)
            if original.write_id != member.frame['write_id']:
                raise InvalidFragment('the accepted shared member has a newer actual write')
            connection.commit()
            return _result(original)
        if record.released:
            raise InvalidFragment('the shared slot has already been released')
        _validate_member_packet(connection, packet, member, job)
        connection.execute('''
            INSERT INTO custody
                (job_id, recording_id, outlet, ordinal, volume_tag, kind, payload, checksum,
                 state, slot_id, write_id)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, 'accepted', ?, ?)
            ON CONFLICT (job_id, recording_id, outlet, ordinal) DO NOTHING
        ''', (receipt.job_id, receipt.recording_id, Outlet.MAIN.value, receipt.ordinal,
              member.frame['volume_tag'], member.frame['kind'], bytes.fromhex(member.frame['hex']),
              member.frame['crc32'], slot_id, member.frame['write_id']))
        accepted = custody.get(receipt)
        if accepted is None or accepted.slot_id != slot_id or accepted.write_id != member.frame['write_id']:
            raise InvalidFragment('the shared member was not recorded as accepted')
        if before_commit is not None:
            before_commit()
        connection.commit()
    except BaseException:
        connection.rollback()
        raise
    return _result(accepted)


def shared_member_evidence(connection, job_id: str):
    store = SharedSlotStore(connection)
    slot_id = store.job_slots(job_id)
    if slot_id is None:
        return None
    record = store.load(slot_id)
    member = record.member(job_id)
    accepted = CustodyStore(connection).get(_receipt(member))
    return record, member, accepted


def query_shared_release(connection, slot_id: str) -> str:
    record = SharedSlotStore(connection).load(slot_id)
    if record is None:
        return 'unknown'
    return 'released' if record.released else 'current'


def release_shared_slot(connection, slot_id: str) -> str:
    store = SharedSlotStore(connection)
    record = store.load(slot_id)
    if record is None:
        raise ValueError('release requires a known shared slot')
    if record.released:
        return 'released'
    connection.execute('BEGIN IMMEDIATE')
    with connection:
        record = store.load(slot_id)
        custody = CustodyStore(connection)
        for member in record.members:
            accepted = custody.get(_receipt(member))
            if accepted is None or accepted.write_id != member.frame['write_id']:
                return 'current'
        for member in record.members:
            connection.execute('''
                UPDATE custody SET state = 'verified'
                WHERE job_id = ? AND recording_id = ? AND outlet = 'main' AND ordinal = ?
            ''', (member.job_id, member.recording_id, member.slot_index))
        store.mark_released(slot_id)
    return 'released'
