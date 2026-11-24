import argparse
import json
import sqlite3
from contextlib import closing
from pathlib import Path
from tempfile import TemporaryDirectory
from zlib import crc32

from archive.readback import VolumeNotAvailable, read
from archive.storage import ArchiveStore
from capture.custody import CustodyStore
from capture.intake import StepState, step
from capture.validation import InvalidFragment
from decision.completeness import RecordingUnfinished
from decision.sealing import seal
from device.replay_state import ReplayState
from jobs.registration import initialize_jobs, open as open_job

SAMPLE = Path(__file__).resolve().parent / "samples" / "shared_slot.json"


def check_fixture(sample):
    assert [member["job_id"] for member in sample["members"]] == ["X", "Y"]
    for member in sample["members"]:
        assert member["backup"]["end"] is True
        frames = [member["main"], *member["backup"]["frames"], *sample["rewrites"].values()]
        for frame in frames:
            assert crc32(bytes.fromhex(frame["hex"])) == frame["crc32"]
    assert sample["rewrites"]["same"] == sample["members"][1]["main"]
    observations = {}
    for name, entry in (("X", sample["members"][0]["main"]), *sample["rewrites"].items()):
        observations[name] = format(int(entry["hex"], 16) ^ 12, "02x")
    assert observations == {"X": "0b", "changed": "06", "same": "05"}
    return observations


def connect(path):
    conn = sqlite3.connect(path)
    initialize_jobs(conn)
    ReplayState(conn).initialize()
    CustodyStore(conn).initialize()
    return conn


def rejects(error, action, *args, **kwargs):
    try:
        action(*args, **kwargs)
    except error as exc:
        return exc
    raise AssertionError(f"{action.__name__} accepted an operation that should fail")


def commit_failure():
    raise RuntimeError("fixture: refuse member commit")


def exercise(root, sample, variation, expected_b):
    from capture.intake import accept_shared_member
    from device.recording import attach_shared_slot, read_shared_member, rewrite_shared_member
    from device.release import query_shared_release, release_shared_slot

    field = root / "field.sqlite"
    archive = ArchiveStore(root / "archive.sqlite")
    archive.initialize()
    slot = sample["slot_id"]
    with closing(connect(field)) as device, closing(connect(field)) as desk:
        for job in sample["jobs"]:
            open_job(desk, job["job_id"], job["volume_tag"], job["length"], job["length"])
        attach_shared_slot(device, slot, sample["members"])
        for job_id in ("X", "Y"):
            assert step(desk, job_id, "backup").state is StepState.RELEASED
            rejects(RecordingUnfinished, seal, desk, archive, job_id)
        assert query_shared_release(device, slot) == "current"
        assert release_shared_slot(device, slot) == "current"
        x = read_shared_member(device, slot, "X")
        stale_y = read_shared_member(device, slot, "Y")
        assert x["payload"] == b"\x07" and stale_y["payload"] == b"\x09"
        rewrite_shared_member(device, stale_y, sample["rewrites"][variation])
        y = read_shared_member(device, slot, "Y")
        assert y["write_id"] != stale_y["write_id"]
        rejects(InvalidFragment, accept_shared_member, desk, "Y", stale_y)
        rejects(InvalidFragment, accept_shared_member, desk, "Y", x)
        altered = dict(y, payload=b"\x08", checksum=crc32(b"\x08"))
        rejects(InvalidFragment, accept_shared_member, desk, "Y", altered)
        try:
            result = step(desk, "Y", "main")
        except InvalidFragment:
            pass
        else:
            assert result.record is None
        x_receipt = accept_shared_member(desk, "X", x)
        rejects(ValueError, rewrite_shared_member, device, x, sample["members"][0]["main"])
        signed_x = seal(desk, archive, "X")
        assert signed_x.b.hex() == "0b"
        assert release_shared_slot(device, slot) == "current"
        error = rejects(RuntimeError, accept_shared_member, desk, "Y", y, before_commit=commit_failure)
        assert str(error) == "fixture: refuse member commit"
        assert release_shared_slot(device, slot) == "current"
        rejects(RecordingUnfinished, seal, desk, archive, "Y")
        rejects(VolumeNotAvailable, read, archive, "VOL-Y")

    with closing(connect(field)) as device, closing(connect(field)) as desk:
        assert query_shared_release(device, slot) == "current"
        assert accept_shared_member(desk, "X", x) == x_receipt
        y_receipt = accept_shared_member(desk, "Y", y)
        assert release_shared_slot(device, slot) == "released"
        assert release_shared_slot(device, slot) == "released"
        signed_y = seal(desk, archive, "Y")
        assert seal(desk, archive, "X") == signed_x

    with closing(connect(field)) as reopened:
        assert query_shared_release(reopened, slot) == "released"
        assert accept_shared_member(reopened, "Y", y) == y_receipt
        rejects(InvalidFragment, accept_shared_member, reopened, "Y", stale_y)

    field.unlink()
    offline = ArchiveStore(root / "archive.sqlite")
    result = {}
    for packet, signed, wanted in ((x, signed_x, "0b"), (y, signed_y, expected_b)):
        volume = read(offline, signed.number)
        assert volume == signed and volume.a == packet["payload"] and volume.b.hex() == wanted
        assert volume.receipt.receipt_id == f"FIRST-{volume.number}"
        sources = [[source.to_data() for source in tape.sources] for tape in volume.provenance]
        main = [entry for tape in sources for entry in tape if entry["outlet"] == "main"]
        assert len(main) == 2
        for entry in main:
            for key in ("slot_id", "job_id", "recording_id", "write_id"):
                assert entry[key] == packet[key]
        result[volume.job_id] = {"a": volume.a.hex(), "b": volume.b.hex(),
                                 "sources": sources, "first_receipt": volume.receipt.receipt_id}
    return result


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("mode", choices=("fixture", "verify", "demo"))
    mode = parser.parse_args().mode
    sample = json.loads(SAMPLE.read_text(encoding="utf-8"))
    expected = check_fixture(sample)
    if mode == "fixture":
        print(json.dumps({"slot": sample["slot_id"], "expected_b": expected}))
        return
    variants = ("changed", "same") if mode == "verify" else ("changed",)
    for variant in variants:
        with TemporaryDirectory(prefix="shared-slot-") as directory:
            volumes = exercise(Path(directory), sample, variant, expected[variant])
        print("POINTWISE_SCENARIO " + json.dumps({"case_id": f"shared_slot.{variant}",
                                                  "expected_b": {"X": "0b", "Y": expected[variant]},
                                                  "offline": volumes}, sort_keys=True))


if __name__ == "__main__":
    main()
