import sqlite3

from archive.history import find_by_job
from archive.publish import PublishFailed
from archive.readback import VolumeNotAvailable, read
from archive.storage import ArchiveStore
from capture.evidence import snapshot_custody
from decision.sealing import seal
from tests import JOB, WorkflowCase, frame


class ArchiveTests(WorkflowCase):
    def test_commit_retry(self):
        self.load([frame('P', '0c'), frame('A', '07')], [frame('P', '0c')])
        self.drain()
        before = snapshot_custody(self.conn, JOB)
        observations = []

        def fail_at_commit_boundary():
            self.assertIsNone(find_by_job(self.archive, JOB))
            self.assert_no_archive()
            observations.append(self.archive_counts())
            raise sqlite3.OperationalError('simulated failure before commit')

        with self.assertRaises(PublishFailed):
            seal(self.conn, self.archive, JOB, before_commit=fail_at_commit_boundary)
        self.assertEqual(observations, [(0, 0)])
        self.assert_no_archive()
        self.reopen()
        self.assertEqual(snapshot_custody(self.conn, JOB), before)
        volume = seal(self.conn, self.archive, JOB)
        self.assertEqual((volume.a, volume.b), (b'\x07', b'\x0b'))
        self.assertEqual(volume.receipt.receipt_id, 'FIRST-VOL-J')
        self.assertEqual(self.archive_counts(), (1, 1))
        self.assertEqual(seal(self.conn, self.archive, JOB, before_commit=fail_at_commit_boundary), volume)
        self.assertEqual(read(self.archive, 'VOL-J'), volume)
        self.assertEqual(self.archive_counts(), (1, 1))

    def test_offline_history(self):
        self.load([frame('A', '07')], [frame('B', '0b')])
        self.drain()
        first = seal(self.conn, self.archive, JOB)
        self.load([frame('B', '19')], [frame('P', '13')], job='later', volume='later-coil')
        self.drain('later')
        second = seal(self.conn, self.archive, 'later')
        self.assertEqual((second.a, second.b), (b'\x0a', b'\x19'))
        self.assertNotEqual(first.provenance, second.provenance)
        self.assertNotEqual(first.receipt, second.receipt)
        self.conn.close()
        self.state_path.unlink()
        offline = ArchiveStore(self.archive_path)
        offline.initialize()
        self.assertEqual(read(offline, 'VOL-J'), first)
        self.assertEqual(read(offline, 'VOL-later'), second)
        self.assertEqual(read(offline, 'VOL-J'), first)
        with self.assertRaises(VolumeNotAvailable):
            read(offline, 'VOL-missing')

    def test_unsigned_job_has_no_public_result(self):
        self.load([frame('A', '07')], [frame('B', '0b')])
        self.drain()
        self.assert_no_archive()
