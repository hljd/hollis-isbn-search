"""Per-run estimate using actual query durations and explicit API waits."""
import time


class SearchTimeEstimator:
    def __init__(self, clock=time.perf_counter):
        self.clock = clock
        self.samples = []
        self.pending = 0
        self.query_started = None
        self.query_wait = 0.0
        self.wait_started = None
        self.wait_remaining = 0.0
        self.recovery = False
        self.finished = False
        self.stopped = False

    def on_progress(self, event):
        kind = event["event"]
        if kind == "start":
            self.pending = event["total"]
        elif kind in ("pass_start", "recovery"):
            self.pending = event["pending"]
            self.recovery = event.get("recovery", kind == "recovery")
        elif kind == "query_start":
            self.query_started = self.clock()
            self.query_wait = 0.0
        elif kind == "completed":
            if self.query_started is not None:
                # Samples include request spacing, pagination and retry waits.
                self.samples.append(max(0.0, self.clock() - self.query_started))
            self.query_started = None
            self.query_wait = 0.0
            self.pending = event["pending"]
        elif kind == "finished":
            self.finished = True
            self.stopped = event["stopped"]
            self.pending = 0

    def on_wait(self, remaining, is_delay):
        # Ordinary two-second request spacing already belongs in query time.
        if not is_delay:
            return
        if remaining > 0 and self.wait_started is None:
            self.wait_started = self.clock()
        if remaining <= 0 and self.wait_started is not None:
            if self.query_started is not None:
                self.query_wait += self.clock() - self.wait_started
            self.wait_started = None
        self.wait_remaining = max(0.0, remaining)

    def remaining_seconds(self):
        if self.finished:
            return 0.0
        if len(self.samples) < 5 or self.pending <= 0:
            return None
        average = sum(self.samples) / len(self.samples)
        active = 0.0
        if self.query_started is not None:
            waiting = self.query_wait
            if self.wait_started is not None:
                waiting += self.clock() - self.wait_started
            active = max(0.0, self.clock() - self.query_started - waiting)
        # Known remaining waits are added explicitly, so their countdown
        # reduces the ETA instead of permanently suppressing it. Historical
        # retry costs remain in the average as an allowance for future delays.
        return self.wait_remaining + max(1.0, average * self.pending - active)
