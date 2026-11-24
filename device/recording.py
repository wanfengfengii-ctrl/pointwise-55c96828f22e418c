import binascii
import hashlib
import json
from dataclasses import dataclass
from pathlib import Path

from device.position import OutletPosition
from device.replay_state import ReplayFrame, ReplayOutlet, ReplayState
from device.slot import Outlet, SlotReceipt
from jobs.lifecycle import JobLifecycle
from jobs.registration import get_job


@dataclass(frozen=True)
class RecordedFragment:
    receipt: SlotReceipt
    volume_tag: str
    kind: str
    payload: bytes
    checksum: int


@dataclass(frozen=True)
class RecordingStatus:
    recording_id: str | None
    positions: tuple[OutletPosition, ...]
    released: frozenset[SlotReceipt]

    @property
    def ended(self):
        return len(self.positions) == len(Outlet) and all(position.ended for position in self.positions)

    def position(self, outlet: Outlet):
        return next((position for position in self.positions if position.outlet == outlet), None)


def _decode_frame(value, default_volume):
    if not isinstance(value, dict):
        raise ValueError('a frame must be an object')
    volume = value.get('volume_tag', default_volume)
    kind, hex_data, checksum = value.get('kind'), value.get('hex'), value.get('crc32')
    if not all(isinstance(field, str) for field in (volume, kind, hex_data)):
        raise ValueError('frame volume, kind and hex must be text')
    if type(checksum) is not int or not 0 <= checksum < 2**32:
        raise ValueError('frame CRC32 must be an unsigned 32-bit integer')
    try:
        payload = binascii.unhexlify(hex_data)
    except (ValueError, binascii.Error) as error:
        raise ValueError('frame hex is not valid binary data') from error
    return ReplayFrame(volume, kind, payload, checksum)


def _decode_recording(source):
    if not isinstance(source, dict):
        source = json.loads(Path(source).read_text(encoding='utf-8'))
    if not isinstance(source, dict):
        raise ValueError('recording must be an object')
    recording_id, volume = source.get('recording_id'), source.get('volume_tag')
    if not all(isinstance(value, str) and value.strip() for value in (recording_id, volume)):
        raise ValueError('recording id and volume tag must be nonempty text')
    entries = source.get('outlets')
    if not isinstance(entries, dict) or set(entries) != set(Outlet):
        raise ValueError('recording requires exactly main and backup outlets')
    outlets = {}
    for outlet in Outlet:
        entry = entries[outlet]
        if (not isinstance(entry, dict) or type(entry.get('end')) is not bool
                or not isinstance(entry.get('frames'), list)):
            raise ValueError('an outlet requires frames and an explicit end flag')
        frames = tuple(_decode_frame(frame, volume) for frame in entry['frames'])
        outlets[outlet] = ReplayOutlet(frames, entry['end'])
    return recording_id, volume, outlets


def _fingerprint(recording_id, volume, outlets):
    canonical = [recording_id, volume, [
        [outlet, entry.end_event, [
            [frame.volume_tag, frame.kind, frame.payload.hex(), frame.checksum]
            for frame in entry.frames
        ]] for outlet, entry in outlets.items()
    ]]
    return hashlib.sha256(json.dumps(canonical, separators=(',', ':')).encode('utf-8')).hexdigest()


def attach_recording(connection, job_id, recording):
    lifecycle = JobLifecycle(get_job(connection, job_id))
    recording_id, volume, outlets = _decode_recording(recording)
    fingerprint = _fingerprint(recording_id, volume, outlets)

    def check_attachment(previous, proposed):
        lifecycle.check_attachment(volume, previous, proposed)

    ReplayState(connection).attach(job_id, recording_id, fingerprint, outlets, check_attachment)


def read_current(connection, job_id, outlet) -> RecordedFragment | None:
    current = ReplayState(connection).read_current(job_id, Outlet(outlet))
    if current is None:
        return None
    receipt, frame = current
    return RecordedFragment(receipt, frame.volume_tag, frame.kind, frame.payload, frame.checksum)


def recording_status(connection, job_id) -> RecordingStatus:
    return RecordingStatus(*ReplayState(connection).status(job_id))
