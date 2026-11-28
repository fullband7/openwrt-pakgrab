import argparse
from dataclasses import dataclass

from . import listfile
from .index import NAME_RE
from .progress import format_rate
from .selection import Selection, SelectionError, split_tokens
from .settings import SettingsError, parse_jobs, parse_switch
from .throttle import parse_kib_rate, parse_rate


class UsageError(Exception):
    pass


class HelpRequested(UsageError):
    pass


class ShellParser(argparse.ArgumentParser):
    def __init__(self, prog):
        super().__init__(prog=prog, add_help=False, allow_abbrev=False)
        self.add_argument("-h", "--help", action="store_true")

    def error(self, message):
        raise UsageError(message)

    def exit(self, status=0, message=None):
        raise UsageError(message or "")

    def parse(self, args):
        namespace = self.parse_args(args)
        if namespace.help:
            raise HelpRequested()
        return namespace


@dataclass(frozen=True)
class MirrorOptions:
    selection: Selection
    assume_yes: bool
    dry_run: bool


def _names(values, directories):
    names = []
    for token in split_tokens(values):
        if listfile.is_list(token):
            try:
                entries = listfile.read(listfile.locate(token, directories))
            except listfile.ListError as error:
                raise UsageError(str(error))
            for entry in entries:
                if not NAME_RE.match(entry):
                    raise UsageError("invalid package name '%s'" % entry[:64])
            names.extend(entries)
        elif NAME_RE.match(token):
            names.append(token)
        else:
            raise UsageError("invalid package name '%s'" % token[:64])
    return tuple(dict.fromkeys(names))


def parse_mirror(args, directories):
    parser = ShellParser("mirror")
    parser.add_argument("scope", nargs="?", choices=("all",))
    parser.add_argument("-f", "--feeds", action="append", default=[])
    parser.add_argument("-a", "--arch", action="append", default=[])
    parser.add_argument("-p", "--packages", action="append", default=[])
    parser.add_argument("-n", "--no-deps", action="store_true")
    parser.add_argument("-y", "--yes", action="store_true")
    parser.add_argument("--dry-run", action="store_true")
    namespace = parser.parse(args)
    try:
        selection = Selection(
            split_tokens(namespace.feeds),
            split_tokens(namespace.arch),
            _names(namespace.packages, directories),
            not namespace.no_deps,
        )
    except SelectionError as error:
        raise UsageError(str(error))
    if namespace.scope is None and not selection.active:
        raise UsageError("nothing to mirror, use 'all' or a filter")
    return MirrorOptions(selection, namespace.yes, namespace.dry_run)


def rate_text(rate):
    return format_rate(rate) if rate else "off"


def describe_settings(settings):
    return (
        "jobs         : %d" % settings.jobs,
        "limit-rate   : %s" % rate_text(settings.rate),
        "resume       : %s" % ("on" if settings.resume else "off"),
    )


def apply_setting(settings, name, value):
    try:
        if name == "jobs":
            settings.jobs = parse_jobs(value)
        elif name == "limit-rate":
            settings.rate = parse_rate(value)
        elif name == "max-bandwidth":
            settings.rate = parse_kib_rate(value)
        elif name == "resume":
            settings.resume = parse_switch(value)
        else:
            raise UsageError("unknown setting '%s'" % name[:32])
    except SettingsError as error:
        raise UsageError(str(error))
