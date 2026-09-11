"""Single-job pipeline runner with per-resource lane parallelism.

One job at a time (single-user local tool), but within a job, tasks run on
3-4 lanes that use different resources and so overlap safely (see _run's
own lane construction for the exact set, which depends on WHISPER_BACKEND):

  net lane — stage 0 (video download): bandwidth-bound, runs ahead of
             everything else. Stage 1 (transcribe) joins this lane
             instead of its usual cpu one when WHISPER_BACKEND is a cloud
             backend (groq/openai) -- it's network-bound then, not
             CPU-bound, and leaving it in the cpu lane would needlessly
             serialize it against real CPU work for other lectures.
  cpu lane — stages 1-5 (transcribe/extract/detect/OCR/segment): these
             saturate cores, so only one runs at a time. Stage 1
             (transcribe) moves out of this lane instead -- to its own
             gpu lane under WHISPER_BACKEND=mlx, or to the net lane under
             a cloud backend -- since it no longer contends with the CPU
             lane's other stages for the same resource either way.
  api lane — stage 6 (note generation) and stage 11 (practice exams, run
             through this scheduler via webui/routes/exams.py building
             its own task list): both Anthropic API calls, network-bound,
             near-zero CPU.
  gpu lane — stage 1 (transcribe) only when WHISPER_BACKEND=mlx.

Within a lecture, stages remain strictly sequential (stage k needs k-1's
artifact). Stage 7 (assembly) runs last, after every lane drains.
"""

import glob
import json
import subprocess
import sys
import threading
import time
import uuid

from . import config, progress

# notely/ (ports, adapters) lives alongside scripts/ and webui/ at the
# project root -- not guaranteed to already be on sys.path depending on how
# the server was launched (uvicorn webui.main:app vs. python -m webui.main
# vs. an IDE run config), so this is asserted explicitly rather than
# assumed. Must happen before the `from notely...` import below.
if str(config.PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(config.PROJECT_ROOT))

from notely.adapters.ffprobe_media_probe import FfprobeMediaProbe  # noqa: E402
from notely.runner import build_tasks  # noqa: E402, F401 (re-exported: webui.jobs.build_tasks is public API)
from notely.stages import MAX_PIPELINE_STAGE  # noqa: E402

MAX_EVENTS_IN_MEMORY = 2000

_MEDIA_PROBE = FfprobeMediaProbe()


class Busy(RuntimeError):
    """Raised by start_job() when a job is already running -- the API layer
    maps this to HTTP 409."""


def stage_script(stage: int) -> str:
    # Glob rather than a fixed filename from the stage registry (see
    # notely.stages) on purpose: tests/test_jobs_scheduler.py points
    # SCRIPTS_DIR at a tmp dir of differently-named stub scripts (still
    # "{stage:02d}_*.py") to drive the real scheduler without the real
    # pipeline -- a fixed-name lookup would break that test seam.
    matches = sorted(glob.glob(str(config.SCRIPTS_DIR / f"{stage:02d}_*.py")))
    if not matches:
        raise FileNotFoundError(f"no script for stage {stage}")
    return matches[0]


def get_video_duration(lecture_id: str):
    return _MEDIA_PROBE.get_duration(config.VIDEOS_DIR / f"{lecture_id}.mp4")


class JobManager:
    def __init__(self):
        # Single mutex for every read/write of self.job, self.events,
        # self._seq, self._busy (C1). self._cond is the *same* lock wrapped
        # in a Condition (not a second lock) so scheduler wait/notify and
        # plain mutation share one consistent view of state -- no more
        # "mutated under _cond, serialized under _lock" torn reads.
        self._lock = threading.Lock()
        self._cond = threading.Condition(self._lock)  # scheduler wake-ups
        # A separate mutex just for the job's log file: log.write()/flush()
        # (once per subprocess stdout line, potentially thousands of times
        # per job) has nothing to do with job-state consistency, and used
        # to borrow self._lock -- serializing disk I/O against every SSE
        # poll/snapshot/busy check reading job state on the same mutex.
        self._log_lock = threading.Lock()
        self._thread = None
        self._procs = {}  # lane name -> running Popen
        self._cancelled = False
        self._busy = False  # guarded by _lock; claim-and-start atomicity (C2)
        self.job = None  # snapshot dict
        self.events = []  # [{seq, type, ...}]
        self._seq = 0

    # -- events -----------------------------------------------------------
    def _emit(self, type_: str, **data):
        with self._lock:
            self._seq += 1
            evt = {"seq": self._seq, "type": type_, "ts": time.time(), **data}
            self.events.append(evt)
            if len(self.events) > MAX_EVENTS_IN_MEMORY:
                del self.events[: len(self.events) - MAX_EVENTS_IN_MEMORY]
        return evt

    def events_since(self, seq: int):
        with self._lock:
            return [e for e in self.events if e["seq"] > seq]

    # -- lifecycle --------------------------------------------------------
    @property
    def busy(self) -> bool:
        with self._lock:
            return self._busy

    def snapshot(self):
        with self._lock:
            return json.loads(json.dumps(self.job)) if self.job else None

    def start_job(self, tasks):
        """tasks: [(lecture_id|None, stage, argv_extra)] — atomically claims
        the "one job at a time" slot and raises Busy if one is already
        running (C2). The check-and-claim happens under the same lock other
        threads use to read self._busy/self.job, so two concurrent callers
        can't both win."""
        with self._lock:
            if self._busy:
                raise Busy("a job is already running")
            self._busy = True
            job_id = uuid.uuid4().hex[:8]
            self._cancelled = False
            # self.events/_seq are deliberately NOT reset here (C3): an SSE
            # client reconnecting with Last-Event-ID from the previous job
            # must see a monotonically increasing sequence, not a lower one
            # that looks like replay/skip.
            self.job = {
                "id": job_id,
                "status": "running",
                "started": time.time(),
                "tasks": [
                    {
                        "lecture_id": lec,
                        "stage": stage,
                        "stage_name": progress.STAGE_NAMES[stage],
                        "status": "pending",
                        "percent": None,
                        "detail": "",
                    }
                    for lec, stage, _ in tasks
                ],
            }
        # Thread creation/start happens outside the lock (no need to hold it
        # for that), but self._busy is already True so no other start_job()
        # call can slip in and reset self.job/events underneath this job.
        self._thread = threading.Thread(target=self._run, args=(tasks,), daemon=True)
        self._thread.start()
        return job_id

    def cancel(self):
        with self._lock:
            self._cancelled = True
        for proc in list(self._procs.values()):
            if proc and proc.poll() is None:
                proc.terminate()
                try:
                    proc.wait(timeout=5)
                except subprocess.TimeoutExpired:
                    proc.kill()
        with self._cond:
            self._cond.notify_all()

    # -- scheduler ---------------------------------------------------------
    IO_STAGES = {0, 6}  # network-bound: download, note generation (API)

    def _deps_done(self, tasks, idx):
        """A task is ready when every earlier task of the same lecture is done."""
        lec = tasks[idx][0]
        for j in range(idx):
            if tasks[j][0] == lec and self.job["tasks"][j]["status"] != "done":
                return False
        return True

    def _claim_next(self, tasks, lane_stages, failed_lectures):
        """Pick the first pending, ready task for this lane (holding _cond)."""
        for i, (lec, stage, _extra) in enumerate(tasks):
            t = self.job["tasks"][i]
            if t["status"] != "pending" or stage not in lane_stages:
                continue
            if lec in failed_lectures:
                t["status"] = "skipped"
                t["detail"] = "an earlier stage failed for this lecture"
                self._cond.notify_all()
                continue
            if self._deps_done(tasks, i):
                t["status"] = "running"
                return i
        return None

    def _lane_settled(self, tasks, lane_stages):
        return all(
            self.job["tasks"][i]["status"] != "pending"
            for i, (_lec, stage, _e) in enumerate(tasks)
            if stage in lane_stages
        )

    def _worker(self, lane, lane_stages, tasks, log, failed_lectures):
        while not self._cancelled:
            with self._cond:
                idx = self._claim_next(tasks, lane_stages, failed_lectures)
                if idx is None:
                    if self._lane_settled(tasks, lane_stages):
                        return
                    self._cond.wait(timeout=2.0)  # deps may complete on the other lane
                    continue
            lecture_id, stage, extra = tasks[idx]
            ok = self._run_task(lane, idx, lecture_id, stage, extra, log)
            with self._cond:
                if not ok and lecture_id is not None:
                    failed_lectures.add(lecture_id)
                self._cond.notify_all()
        # mark anything still pending in this lane as cancelled
        with self._cond:
            for i, (_lec, stage, _e) in enumerate(tasks):
                if stage in lane_stages and self.job["tasks"][i]["status"] == "pending":
                    self.job["tasks"][i]["status"] = "cancelled"

    def _run_task(self, lane, i, lecture_id, stage, extra, log) -> bool:
        task = self.job["tasks"][i]
        self._emit("stage_start", task_index=i, lecture_id=lecture_id, stage=stage)

        ctx = {"video_duration": get_video_duration(lecture_id) if lecture_id else None}
        argv = [sys.executable, "-u", stage_script(stage)]
        if lecture_id:
            argv.append(lecture_id)
        argv += extra

        with self._log_lock:
            log.write(
                f"\n===== task {i} [{lane}]: stage {stage} {lecture_id or ''} =====\n$ {' '.join(argv)}\n"
            )
            log.flush()
        skip_line = ""
        try:
            proc = subprocess.Popen(
                argv,
                cwd=str(config.PROJECT_ROOT),
                env=config.stage_env(),
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                text=True,
            )
            # Register in _procs (keyed by lane, including "final" for stage
            # 7 -- see _run()) before doing anything else, and re-check
            # _cancelled under the same lock right after registering: closes
            # the race where cancel() runs between Popen() and this
            # assignment and would otherwise miss killing this proc (C5).
            with self._lock:
                self._procs[lane] = proc
                cancelled_now = self._cancelled
            if cancelled_now and proc.poll() is None:
                proc.terminate()
            for line in proc.stdout:
                line = line.rstrip("\n")
                with self._log_lock:
                    log.write(f"[{lecture_id or 'all'}:{stage}] {line}\n")
                if "[skip]" in line:
                    skip_line = line
                pct = progress.parse_line(stage, line, ctx)
                if pct is not None:
                    # monotonic: concurrent stage-6 slides complete out of order
                    new_pct = round(pct * 100, 1)
                    emit_pct = None
                    with self._lock:
                        if task["percent"] is None or new_pct > task["percent"]:
                            task["percent"] = new_pct
                            emit_pct = new_pct
                    if emit_pct is not None:
                        self._emit("progress", task_index=i, percent=emit_pct)
                else:
                    self._emit("log", task_index=i, line=f"[{lecture_id or 'all'}] {line}")
            returncode = proc.wait()
        except Exception as e:
            returncode = -1
            with self._lock:
                task["detail"] = str(e)
            self._emit("log", task_index=i, line=f"ERROR: {e}")
        finally:
            with self._lock:
                self._procs.pop(lane, None)
            with self._log_lock:
                log.flush()

        with self._lock:
            if self._cancelled:
                task["status"] = "cancelled"
            elif returncode != 0:
                task["status"] = "failed"
                task["detail"] = task["detail"] or f"exit code {returncode}"
            elif not progress.artifact_ok(stage, lecture_id):
                task["status"] = "blocked"
                task["detail"] = skip_line or "stage produced no output (missing input?)"
            else:
                task["status"] = "done"
                task["percent"] = 100.0
            status, detail = task["status"], task["detail"]
        self._emit("stage_done", task_index=i, status=status, detail=detail)
        return status == "done"

    def _run(self, tasks):
        config.LOGS_DIR.mkdir(parents=True, exist_ok=True)
        log_path = config.LOGS_DIR / f"{self.job['id']}.log"
        failed_lectures = set()  # a failure only skips that lecture's remaining stages

        # stage 7 (assembly, lecture_id=None) runs after both lanes drain
        final_tasks = [(i, t) for i, t in enumerate(tasks) if t[1] == MAX_PIPELINE_STAGE]

        # One lane per independent resource, so no stage ever queues behind a
        # stage using different hardware:
        #   net — downloads (stage 0): bandwidth. Runs ahead of everything.
        #   api — note generation (stage 6): Anthropic API. Trails behind.
        #   gpu — transcription (stage 1) when WHISPER_BACKEND=mlx.
        #   cpu — extraction/detection/OCR/segmentation (2-5), plus
        #         transcription on the CPU backend.
        whisper_backend = config.stage_env().get("WHISPER_BACKEND", "").lower()
        gpu_transcribe = whisper_backend == "mlx"
        # Cloud transcription (groq/openai) is network-bound like stage 0's
        # downloads, not CPU-bound like the rest of stages 1-5 -- leaving
        # it in the cpu lane would needlessly serialize a waiting-on-the-
        # network stage 1 against real CPU work (stage 2-5 for other
        # lectures) that could otherwise run alongside it.
        cloud_transcribe = whisper_backend in ("groq", "openai")
        lanes = [
            ("net", {0, 1} if cloud_transcribe else {0}),
            ("cpu", {2, 3, 4, 5} if (gpu_transcribe or cloud_transcribe) else {1, 2, 3, 4, 5}),
            # Stage 11 (practice exams) is network-bound like stage 6, and
            # -- unlike stages 9/10 -- IS started through this scheduler
            # (webui/routes/exams.py builds its own [(None, 11, argv)]
            # task list directly, bypassing notely.runner.build_tasks,
            # which doesn't know about stages past MAX_PIPELINE_STAGE).
            # Without this, a stage-11 task would match no lane's
            # `stage in lane_stages` check and sit "pending" forever.
            ("api", {6, 11}),
        ]
        if gpu_transcribe:
            lanes.append(("gpu", {1}))

        with open(log_path, "a") as log:
            workers = [
                threading.Thread(
                    target=self._worker, args=(name, stages, tasks, log, failed_lectures), daemon=True
                )
                for name, stages in lanes
            ]
            for w in workers:
                w.start()
            for w in workers:
                w.join()

            for i, (lecture_id, stage, extra) in final_tasks:
                with self._lock:
                    cancelled_now = self._cancelled
                    if cancelled_now:
                        self.job["tasks"][i]["status"] = "cancelled"
                    else:
                        self.job["tasks"][i]["status"] = "running"
                if cancelled_now:
                    continue
                # lane="final" so this proc lands in self._procs["final"] --
                # cancel() iterates every proc in self._procs regardless of
                # lane name, so a cancel arriving mid-assembly still finds
                # and terminates it (C5).
                self._run_task("final", i, lecture_id, stage, extra, log)

        with self._lock:
            self.job["status"] = "cancelled" if self._cancelled else ("failed" if failed_lectures else "done")
            self.job["finished"] = time.time()
            status = self.job["status"]
            self._busy = False
        self._emit("job_done", status=status)


MANAGER = JobManager()
