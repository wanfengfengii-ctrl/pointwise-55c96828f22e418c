from dataclasses import dataclass
from enum import StrEnum

from jobs.specification import JobSpec


class JobPhase(StrEnum):
    REGISTERED = 'registered'
    RECORDING = 'recording'
    SETTLED = 'settled'


@dataclass(frozen=True)
class JobLifecycle:
    specification: JobSpec

    def phase(self, *, attached: bool, settled: bool) -> JobPhase:
        if not attached:
            return JobPhase.REGISTERED
        if settled:
            return JobPhase.SETTLED
        return JobPhase.RECORDING

    def check_attachment(self, volume_tag, previous_binding, new_binding):
        if volume_tag != self.specification.identity.volume_tag:
            raise ValueError('recording volume does not match the job')
        phase = self.phase(attached=previous_binding is not None, settled=False)
        if phase is not JobPhase.REGISTERED and previous_binding != new_binding:
            raise ValueError('an attached recording cannot be replaced')
