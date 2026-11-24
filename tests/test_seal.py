from archive.readback import read
from capture.custody import CustodyState
from capture.evidence import snapshot_custody
from capture.intake import StepState, step
from capture.validation import InvalidFragment
from decision.completeness import RecordingUnfinished
from decision.equations import ConflictingEvidence
from decision.recovery import MissingEvidence
from decision.sealing import seal
from device.recording import recording_status
from device.release import ReleaseFault
from device.slot import Outlet
from tests import JOB, VOLUME, WorkflowCase, frame


class SealTests(WorkflowCase):
    def test_direct(self):
        self.load([frame('A', '07')], [frame('B', '0b')])
        a = self.take(Outlet.MAIN).record.receipt
        b = self.take(Outlet.BACKUP).record.receipt
        volume = seal(self.conn, self.archive, JOB)
        self.assertEqual((volume.a, volume.b), (b'\x07', b'\x0b'))
        self.assertEqual([(item.side, item.mode) for item in volume.provenance],
                         [('A', 'direct'), ('B', 'direct')])
        self.assertEqual([item.sources[0].receipt for item in volume.provenance], [a, b])
        self.assertEqual(read(self.archive, volume.number), volume)

    def test_rescue(self):
        self.load([frame('P', '0c'), frame('A', '07')], [frame('P', '0c')])
        p = self.take(Outlet.MAIN).record.receipt
        self.take(Outlet.BACKUP)
        a = self.take(Outlet.MAIN).record.receipt
        volume = seal(self.conn, self.archive, JOB)
        self.assertEqual((volume.a, volume.b), (b'\x07', b'\x0b'))
        self.assertEqual([item.mode for item in volume.provenance], ['direct', 'recovered'])
        self.assertEqual(tuple(source.receipt for source in volume.provenance[0].sources), (a,))
        self.assertEqual(tuple(source.receipt for source in volume.provenance[1].sources), (a, p))

    def test_joined_replay_and_exact_sources(self):
        self.load([frame('P', '0c'), frame('A', '07')], [frame('P', '0c')])
        pending = self.take(Outlet.MAIN, release_fault=ReleaseFault.AFTER)
        self.assertEqual(pending.state, StepState.PENDING)
        backup = self.take(Outlet.BACKUP)
        self.assertNotEqual(pending.record.receipt, backup.record.receipt)
        with self.assertRaises(RecordingUnfinished):
            seal(self.conn, self.archive, JOB)
        self.assert_no_archive()
        self.reopen()
        a = self.take(Outlet.MAIN).record.receipt
        self.assertTrue(recording_status(self.conn, JOB).ended)
        with self.assertRaises(RecordingUnfinished):
            seal(self.conn, self.archive, JOB)
        self.assert_no_archive()
        recovered_slot = step(self.conn, JOB, Outlet.MAIN, pending.record.receipt)
        self.assertEqual(recovered_slot.state, StepState.RELEASED)
        status = recording_status(self.conn, JOB)
        self.assertEqual(status.released, {a, pending.record.receipt, backup.record.receipt})
        volume = seal(self.conn, self.archive, JOB)
        self.assertEqual((volume.a, volume.b), (bytes([7]), bytes([11])))
        actual = [(source.kind, source.volume_tag, source.receipt.job_id,
                   source.receipt.recording_id, source.receipt.outlet, source.receipt.ordinal)
                  for source in volume.provenance[1].sources]
        self.assertEqual(actual, [('A', VOLUME, JOB, 'shift', Outlet.MAIN, 1),
                                  ('P', VOLUME, JOB, 'shift', Outlet.MAIN, 0)])
        self.assertEqual(read(self.archive, 'VOL-J'), volume)

    def test_byte_exact(self):
        self.load([frame('P', '0cff7f'), frame('A', '0700ff')], [], length=3)
        self.drain()
        volume = seal(self.conn, self.archive, JOB)
        self.assertEqual(volume.a, bytes([7, 0, 255]))
        self.assertEqual(volume.b, bytes([11, 255, 128]))

    def test_reverse_pair(self):
        self.load([frame('P', '0cff7f')], [frame('B', '0bff80')], length=3)
        p = self.take(Outlet.MAIN).record.receipt
        b = self.take(Outlet.BACKUP).record.receipt
        volume = seal(self.conn, self.archive, JOB)
        self.assertEqual((volume.a, volume.b), (bytes([7, 0, 255]), bytes([11, 255, 128])))
        self.assertEqual([item.mode for item in volume.provenance], ['recovered', 'direct'])
        self.assertEqual(tuple(source.receipt for source in volume.provenance[0].sources), (b, p))
        self.assertEqual(tuple(source.receipt for source in volume.provenance[1].sources), (b,))

    def test_duplicate_parity(self):
        self.load([frame('P', '0c')], [frame('P', '0c')])
        self.drain()
        records = snapshot_custody(self.conn, JOB).records
        self.assertEqual(len(records), 2)
        self.assertEqual([(record.receipt.outlet, record.receipt.ordinal) for record in records],
                         [(Outlet.MAIN, 0), (Outlet.BACKUP, 0)])
        self.assertEqual([record.fragment.payload for record in records], [b'\x0c', b'\x0c'])
        self.assertTrue(all(record.state is CustodyState.VERIFIED for record in records))
        self.assertTrue(recording_status(self.conn, JOB).ended)
        with self.assertRaises(MissingEvidence):
            seal(self.conn, self.archive, JOB)
        self.assert_no_archive()

    def test_conflicting_parity(self):
        self.load([frame('A', '07'), frame('B', '0b')], [frame('P', '0d')])
        self.drain()
        with self.assertRaises(ConflictingEvidence):
            seal(self.conn, self.archive, JOB)
        self.assert_no_archive()

    def test_same_kind_conflict(self):
        self.load([frame('P', '0c')], [frame('P', '0d')])
        self.drain()
        with self.assertRaises(ConflictingEvidence):
            seal(self.conn, self.archive, JOB)
        self.assert_no_archive()

    def test_unfinished_outlet(self):
        self.load([frame('A', '07'), frame('B', '0b')], [frame('P', '0c')])
        self.take(Outlet.MAIN)
        self.take(Outlet.MAIN)
        with self.assertRaises(RecordingUnfinished):
            seal(self.conn, self.archive, JOB)
        self.assert_no_archive()
        self.take(Outlet.BACKUP)
        volume = seal(self.conn, self.archive, JOB)
        self.assertEqual((volume.a, volume.b), (b'\x07', b'\x0b'))
        self.assertEqual([item.mode for item in volume.provenance], ['direct', 'direct'])

    def test_bad_checksum_tail(self):
        self.load([frame('A', '07'), frame('B', '0b')], [frame('P', '0c', checksum=0)])
        self.take(Outlet.MAIN)
        self.take(Outlet.MAIN)
        tail = self.current(Outlet.BACKUP)
        with self.assertRaises(RecordingUnfinished):
            seal(self.conn, self.archive, JOB)
        with self.assertRaises(InvalidFragment):
            self.take(Outlet.BACKUP)
        self.assertEqual(self.current(Outlet.BACKUP), tail)
        self.assertEqual(len(snapshot_custody(self.conn, JOB).records), 2)
        with self.assertRaises(RecordingUnfinished):
            seal(self.conn, self.archive, JOB)
        self.assert_no_archive()

    def test_sufficient_tapes_without_explicit_end(self):
        self.load([frame('A', '07')], [frame('B', '0b')], backup_end=False)
        self.drain()
        with self.assertRaises(RecordingUnfinished):
            seal(self.conn, self.archive, JOB)
        self.assert_no_archive()
