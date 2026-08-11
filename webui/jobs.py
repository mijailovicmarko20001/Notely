"""Single-job pipeline runner with two-lane parallelism.

One job at a time (single-user local tool), but within a job, tasks run on
two lanes that use different resources and so overlap safely:

  IO lane  — stage 0 (video download) and stage 6 (note generation via API):
             network-bound, near-zero CPU. Downloads run ahead of the whole
             queue; notes generate as soon as each lecture's stage 5 lands.
  CPU lane — stages 1-5 (transcribe/extract/detect/OCR/segment): these
             saturate cores, so only one runs at a time.

Within a lecture, stages remain strictly sequential (stage k needs k-1's
artifact). Stage 7 (assembly) runs last, after both lanes drain.
"""

import glob
import json
import subprocess
import sys
import threading
import time
import uuid

from . import progress
from .config import LOGS_DIR, PROJECT_ROOT, SCRIPTS_DIR, stage_env

MAX_EVENTS_IN_MEMORY = 2000


def stage_script(stage: int) -> str:
    matches = sorted(glob.glob(str(SCRIPTS_DIR / f"{stage:02d}_*.py")))
    if not matches:
        raise FileNotFoundError(f"no script for stage {stage}")
    return matches[0]


def get_video_duration(lecture_id: str):
    video = PROJECT_ROOT / "input" / "videos" / f"{lecture_id}.mp4"
    if not video.exists():
        return None
    try:
        r = subprocess.run(
            ["ffprobe", "-v", "error", "-show_entries", "format=duration",
             "-of", "default=noprint_wrappers=1:nokey=1", str(video)],
            capture_output=True, text=True, timeout=30,
        )
        return float(r.stdout.strip()) if r.returncode == 0 else None
    except Exception:
        return None


class JobManager:
    def __init__(self):
        self._lock = threading.Lock()
        self._thread = None
        self._procs = {}         # lane name -> running Popen
        self._cancelled = False
        self._cond = threading.Condition()  # scheduler wake-ups
        self.job = None          # snapshot dict
        self.events = []         # [{seq, type, ...}]
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
        return self._thread is not None and self._thread.is_alive()

    def snapshot(self):
        with self._lock:
            return json.loads(json.dumps(self.job)) if self.job else None

    def start_job(self, tasks):
        """tasks: [(lecture_id|None, stage, argv_extra)] — raises if busy."""
        if self.busy:
            raise RuntimeError("a job is already running")
        job_id = uuid.uuid4().hex[:8]
        self._cancelled = False
        self.events = []
        self._seq = 0
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
        self._thread = threading.Thread(target=self._run, args=(tasks,), daemon=True)
        self._thread.start()
        return job_id

    def cancel(self):
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
    IO_STAGES = {0, 6}   # network-bound: download, note generation (API)

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

        with self._lock:
            log.write(f"\n===== task {i} [{lane}]: stage {stage} {lecture_id or ''} =====\n$ {' '.join(argv)}\n")
            log.flush()
        skip_line = ""
        try:
            proc = subprocess.Popen(
                argv, cwd=str(PROJECT_ROOT), env=stage_env(),
                stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True,
            )
            self._procs[lane] = proc
            for line in proc.stdout:
                line = line.rstrip("\n")
                with self._lock:
                    log.write(f"[{lecture_id or 'all'}:{stage}] {line}\n")
                if "[skip]" in line:
                    skip_line = line
                pct = progress.parse_line(stage, line, ctx)
                if pct is not None:
                    # monotonic: concurrent stage-6 slides complete out of order
                    new_pct = round(pct * 100, 1)
                    if task["percent"] is None or new_pct > task["percent"]:
                        task["percent"] = new_pct
                        self._emit("progress", task_index=i, percent=task["percent"])
                else:
                    self._emit("log", task_index=i, line=f"[{lecture_id or 'all'}] {line}")
            returncode = proc.wait()
        except Exception as e:
            returncode = -1
            task["detail"] = str(e)
            self._emit("log", task_index=i, line=f"ERROR: {e}")
        finally:
            self._procs.pop(lane, None)
            with self._lock:
                log.flush()

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
        self._emit("stage_done", task_index=i, status=task["status"], detail=task["detail"])
        return task["status"] == "done"

    def _run(self, tasks):
        LOGS_DIR.mkdir(parents=True, exist_ok=True)
        log_path = LOGS_DIR / f"{self.job['id']}.log"
        failed_lectures = set()  # a failure only skips that lecture's remaining stages

        # stage 7 (assembly, lecture_id=None) runs after both lanes drain
        final_tasks = [(i, t) for i, t in enumerate(tasks) if t[1] == 7]

        # One lane per independent resource, so no stage ever queues behind a
        # stage using different hardware:
        #   net — downloads (stage 0): bandwidth. Runs ahead of everything.
        #   api — note generation (stage 6): Anthropic API. Trails behind.
        #   gpu — transcription (stage 1) when WHISPER_BACKEND=mlx.
        #   cpu — extraction/detection/OCR/segmentation (2-5), plus
        #         transcription on the CPU backend.
        gpu_transcribe = stage_env().get("WHISPER_BACKEND", "").lower() == "mlx"
        lanes = [
            ("net", {0}),
            ("cpu", {2, 3, 4, 5} if gpu_transcribe else {1, 2, 3, 4, 5}),
            ("api", {6}),
        ]
        if gpu_transcribe:
            lanes.append(("gpu", {1}))

        with open(log_path, "a") as log:
            workers = [
                threading.Thread(
                    target=self._worker,
                    args=(name, stages, tasks, log, failed_lectures), daemon=True)
                for name, stages in lanes
            ]
            for w in workers:
                w.start()
            for w in workers:
                w.join()

            for i, (lecture_id, stage, extra) in final_tasks:
                if self._cancelled:
                    self.job["tasks"][i]["status"] = "cancelled"
                    continue
                self._run_task("final", i, lecture_id, stage, extra, log)

        self.job["status"] = (
            "cancelled" if self._cancelled else ("failed" if failed_lectures else "done")
        )
        self.job["finished"] = time.time()
        self._emit("job_done", status=self.job["status"])


MANAGER = JobManager()


def build_tasks(lecture_ids, stages, options, force, has_api_key):
    """Translate a UI job request into per-stage argv lists."""
    opts = options or {}

    def flag(name, key):
        v = opts.get(key)
        return [name, str(v)] if v not in (None, "") else []

    tasks = []
    per_lecture_stages = [s for s in stages if s != 7]
    for lec in lecture_ids:
        for s in per_lecture_stages:
            if s == 6 and not has_api_key:
                continue  # note generation locked without a key
            extra = []
            if s == 3:
                extra += flag("--crop", "crop") + flag("--threshold", "threshold") + flag("--interval", "interval")
            elif s == 4:
                extra += (flag("--ocr-lang", "ocr_lang") + flag("--margin", "margin")
                          + flag("--stay-margin", "stay_margin")
                          + flag("--confidence-threshold", "confidence_threshold")
                          + flag("--min-forward-score", "min_forward_score"))
            elif s == 5:
                extra += flag("--min-dwell", "min_dwell")
            if force:
                extra.append("--force")
            tasks.append((lec, s, extra))
    if 7 in stages:
        tasks.append((None, 7, ["--force"]))  # stage 07 fails without --force when output exists
    return tasks
