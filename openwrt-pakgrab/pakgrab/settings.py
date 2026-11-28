from dataclasses import dataclass

DEFAULT_JOBS = 4
MAX_JOBS = 16


class SettingsError(ValueError):
    pass


def parse_jobs(text):
    try:
        value = int(str(text).strip())
    except ValueError:
        raise SettingsError("jobs must be a whole number")
    if not 1 <= value <= MAX_JOBS:
        raise SettingsError("jobs must be between 1 and %d" % MAX_JOBS)
    return value


def parse_switch(text):
    value = str(text).strip().lower()
    if value in ("on", "yes", "true", "1"):
        return True
    if value in ("off", "no", "false", "0"):
        return False
    raise SettingsError("expected on or off")


@dataclass
class Settings:
    jobs: int = DEFAULT_JOBS
    rate: int = 0
    resume: bool = True
