import queue
import threading
import time
from dataclasses import dataclass
from typing import Callable, Optional

from .net import FetchError
from .progress import REFRESH_INTERVAL, Board, Status
from .versions import CURRENT, DOWNLOADED, UPDATED

REQUEST_DELAY = 0.1
RETRIES = 3
RETRY_BACKOFF = 2.0
RATE_LIMIT_STATUSES = (429, 503)
JOIN_TIMEOUT = 10.0


@dataclass(frozen=True)
class Job:
    name: str
    url: str
    action: Callable[[], Optional[str]]
    size: int = 0
    describe: Optional[Callable[[Exception], str]] = None
    payload: object = None


@dataclass(frozen=True)
class Result:
    job: Job
    status: Optional[str]
    error: Optional[str] = None


class Batch:
    def __init__(self, jobs, on_result):
        self.jobs = list(jobs)
        self.pending = queue.Queue()
        for job in self.jobs:
            self.pending.put(job)
        self.on_result = on_result
        self.stream = True
        self.lock = threading.Lock()
        self.results = []
        self.errors = []
        self.counts = {DOWNLOADED: 0, UPDATED: 0, CURRENT: 0}
        self.failed = 0
        self.size_done = 0
        self.size_total = sum(job.size for job in self.jobs)

    def record(self, result):
        with self.lock:
            self.results.append(result)
            self.size_done += result.job.size
            if result.error is not None:
                self.failed += 1
                self.errors.append(result.error)
            elif result.status is not None:
                self.counts[result.status] += 1
            if self.stream and self.on_result is not None:
                self.on_result(result)

    def status(self):
        with self.lock:
            return Status(len(self.results), len(self.jobs), self.size_done, self.size_total)


class Runner:
    def __init__(self, console, fetcher, workers):
        self.console = console
        self.fetcher = fetcher
        self.workers = max(1, workers)
        self.stop = fetcher.cancel
        self.board = Board(console, fetcher.tracker)
        self.lock = threading.Lock()
        self.resume_at = 0.0

    def run(self, title, jobs, quiet=False, on_result=None):
        batch = Batch(jobs, on_result)
        total = len(batch.jobs)
        live = self.console.can_live() and not quiet and total > 0
        batch.stream = not live
        if not live and not quiet:
            self.console.write(title)
        slots = min(self.workers, total)
        threads = [threading.Thread(target=self._work, args=(batch,), daemon=True) for _ in range(slots)]
        started = time.monotonic()
        self.board.reset()
        self.stop.clear()
        try:
            for thread in threads:
                thread.start()
            while total:
                status = batch.status()
                if live:
                    self.console.live(self.board.frame(title, status, started, slots))
                if status.done >= total:
                    break
                time.sleep(REFRESH_INTERVAL)
            for thread in threads:
                thread.join()
        except BaseException:
            self.stop.set()
            for thread in threads:
                thread.join(JOIN_TIMEOUT)
            raise
        finally:
            self.console.live_clear()
            self.stop.clear()
        if live and on_result is not None:
            for result in batch.results:
                on_result(result)
        return batch

    def _work(self, batch):
        tracker = self.fetcher.tracker
        while not self.stop.is_set():
            try:
                job = batch.pending.get_nowait()
            except queue.Empty:
                return
            tracker.enter(job.name, job.size)
            status = error = None
            try:
                status = self._attempt(job.action)
            except Exception as failure:
                error = self._reason(job, failure)
            batch.record(Result(job, status, error))
            tracker.leave()
            if status in (DOWNLOADED, UPDATED):
                self.stop.wait(REQUEST_DELAY)

    @staticmethod
    def _reason(job, failure):
        if job.describe is not None:
            return job.describe(failure)
        return "%s (%s)" % (getattr(failure, "url", None) or job.url, failure)

    def _attempt(self, action):
        attempt = 0
        while True:
            attempt += 1
            self._respect_pause()
            try:
                return action()
            except FetchError as error:
                if not error.retry or attempt >= RETRIES or self.stop.is_set():
                    raise
                self._back_off(error, attempt)

    def _respect_pause(self):
        while not self.stop.is_set():
            wait = self.resume_at - time.monotonic()
            if wait <= 0:
                return
            self.stop.wait(wait)

    def _back_off(self, error, attempt):
        delay = error.delay or RETRY_BACKOFF * attempt
        if error.status in RATE_LIMIT_STATUSES:
            with self.lock:
                self.resume_at = max(self.resume_at, time.monotonic() + delay)
        else:
            self.stop.wait(delay)
