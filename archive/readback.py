from archive.history import ArchivedVolume, find_by_number
from archive.storage import ArchiveStore


class VolumeNotAvailable(LookupError):
    pass


def read(store: ArchiveStore, volume_number: str) -> ArchivedVolume:
    volume = find_by_number(store, volume_number)
    if volume is None:
        raise VolumeNotAvailable(volume_number)
    return volume
