import sqlite3
import unittest
from contextlib import closing
from pathlib import Path
from tempfile import TemporaryDirectory
from zlib import crc32

from archive.readback import VolumeNotAvailable, read
from archive.storage import ArchiveStore
from capture.custody import CustodyStore
from capture.intake import StepState, step
from device.recording import attach_recording, read_current
from device.replay_state import ReplayState
from device.slot import Outlet
from jobs.registration import initialize_jobs, open as open_job


JOB = 'J'
VOLUME = 'coil'


def frame(kind, payload, *, checksum=None, volume_tag=None):
    if checksum is None:
        checksum = crc32(bytes.fromhex(payload))
    data = {'kind': kind, 'hex': payload, 'crc32': checksum}
    if volume_tag is not None:
        data['volume_tag'] = volume_tag
    return data


def recording(main, backup, *, volume=VOLUME, recording_id='shift', main_end=True, backup_end=True):
    return {
        'recording_id': recording_id,
        'volume_tag': volume,
        'outlets': {
            'main': {'frames': list(main), 'end': main_end},
            'backup': {'frames': list(backup), 'end': backup_end},
        },
    }


class WorkflowCase(unittest.TestCase):
    def setUp(self):
        directory = TemporaryDirectory(prefix='line-scan-test-')
        self.addCleanup(directory.cleanup)
        self.root = Path(directory.name)
        self.state_path = self.root / 'field.sqlite'
        self.archive_path = self.root / 'archive.sqlite'
        self.archive = ArchiveStore(self.archive_path)
        self.conn = None
        self.reopen()
        self.initialize()

    def reopen(self):
        if self.conn is not None:
            self.conn.close()
        self.conn = sqlite3.connect(self.state_path)
        self.addCleanup(self.conn.close)

    def initialize(self):
        initialize_jobs(self.conn)
        ReplayState(self.conn).initialize()
        CustodyStore(self.conn).initialize()
        self.archive.initialize()

    def load(self, main, backup, *, length=1, job=JOB, **kwargs):
        data = recording(main, backup, **kwargs)
        open_job(self.conn, job, data['volume_tag'], length, length)
        attach_recording(self.conn, job, data)
        return data

    def current(self, outlet, job=JOB):
        current = read_current(self.conn, job, outlet)
        self.assertIsNotNone(current)
        return current

    def take(self, outlet, job=JOB, **options):
        receipt = self.current(outlet, job).receipt
        return step(self.conn, job, outlet, receipt, **options)

    def drain(self, job=JOB):
        for outlet in Outlet:
            while read_current(self.conn, job, outlet) is not None:
                self.assertEqual(self.take(outlet, job).state, StepState.RELEASED)

    def assert_no_archive(self, job=JOB):
        with self.assertRaises(VolumeNotAvailable):
            read(self.archive, f'VOL-{job}')
        self.assertEqual(self.archive_counts(), (0, 0))

    def archive_counts(self):
        with closing(sqlite3.connect(self.archive_path)) as connection:
            volumes = connection.execute('SELECT COUNT(*) FROM archive_volumes').fetchone()[0]
            receipts = connection.execute('SELECT COUNT(*) FROM archive_receipts').fetchone()[0]
        return volumes, receipts
