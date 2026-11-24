from dataclasses import dataclass

from jobs.identity import JobIdentity


@dataclass(frozen=True)
class JobSpec:
    identity: JobIdentity
    a_length: int
    b_length: int

    def __post_init__(self):
        validate_specification(self)

    @property
    def length(self):
        return self.a_length


def validate_specification(spec: JobSpec):
    if not isinstance(spec.identity, JobIdentity):
        raise ValueError('a job identity is required')
    for length in (spec.a_length, spec.b_length):
        if type(length) is not int or not 0 < length < 2**63:
            raise ValueError('tape length must be a positive SQLite integer')
    if spec.a_length != spec.b_length:
        raise ValueError('A and B must have the same byte length')
