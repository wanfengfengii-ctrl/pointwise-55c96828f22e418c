from dataclasses import dataclass
from enum import StrEnum


class TapeSide(StrEnum):
    A = 'A'
    B = 'B'


@dataclass(frozen=True)
class JobIdentity:
    job_id: str
    volume_tag: str

    def __post_init__(self):
        for name, value in (('job_id', self.job_id), ('volume_tag', self.volume_tag)):
            if not isinstance(value, str) or not value.strip():
                raise ValueError(f'{name} must be nonempty text')
