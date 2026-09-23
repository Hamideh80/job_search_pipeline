"""Terminal progress display and structured logging for pipeline runs."""
import logging
from datetime import datetime
from pathlib import Path

from rich.console import Console
from rich.progress import (
    BarColumn,
    Progress,
    SpinnerColumn,
    TaskProgressColumn,
    TextColumn,
    TimeElapsedColumn,
)

_STAGES = [
    ("discover", "DISCOVER    — pulling job postings from companies.yaml + Gmail"),
    ("extract",  "EXTRACT     — structuring job descriptions with Claude"),
    ("score",    "SCORE       — evaluating fit and selecting CV profile"),
    ("tailor",   "TAILOR      — building tailored resumes and drafting answers"),
    ("sync",     "NOTION SYNC — creating dashboard cards"),
    ("apply",    "APPLY       — submitting approved applications"),
]
_STAGE_NAMES = [k for k, _ in _STAGES]
_STAGE_LABELS = dict(_STAGES)


class RunProgress:
    """Context manager for one pipeline run.

    Wraps a rich progress bar (stage N/6), writes a timestamped log file under
    log_dir, and accumulates per-job stage results for the end-of-run summary.
    """

    def __init__(self, log_dir: Path) -> None:
        self.run_id = datetime.now().strftime("%Y%m%d_%H%M%S")
        log_dir.mkdir(parents=True, exist_ok=True)
        self.log_path = log_dir / f"run_{self.run_id}.log"
        self._setup_logger()
        self._job_statuses: dict[int, dict] = {}
        # legacy_windows=False forces VT100/ANSI output, which supports Unicode
        # on Windows 10+ terminals (cp1252 legacy mode cannot encode ✓/✗).
        self._console = Console(legacy_windows=False)
        self._bar = Progress(
            SpinnerColumn(),
            TextColumn("[bold cyan]{task.description}"),
            BarColumn(bar_width=28),
            TaskProgressColumn(),
            TimeElapsedColumn(),
            console=self._console,
        )
        self._task = self._bar.add_task("Initialising…", total=len(_STAGES))

    def _setup_logger(self) -> None:
        self.logger = logging.getLogger(f"pipeline.{self.run_id}")
        self.logger.setLevel(logging.DEBUG)
        fh = logging.FileHandler(self.log_path, encoding="utf-8")
        fh.setFormatter(logging.Formatter("%(asctime)s [%(levelname)s] %(message)s"))
        self.logger.addHandler(fh)
        self.logger.info("Run started: %s", self.run_id)

    # ── stage lifecycle ──────────────────────────────────────────────────────

    def stage_start(self, stage: str) -> None:
        idx = _STAGE_NAMES.index(stage)
        label = _STAGE_LABELS[stage]
        self._bar.update(self._task, description=f"[{idx + 1}/6] {label}")
        self._console.rule(f"[bold][{idx + 1}/6] {label}")
        self.logger.info("Stage start: %s", stage)

    def stage_done(self, stage: str, *, count: int = 0, note: str = "") -> None:
        self._bar.advance(self._task)
        extra = f" ({note})" if note else ""
        self.logger.info("Stage done: %s — %d job(s)%s", stage, count, extra)

    # ── per-job reporting ────────────────────────────────────────────────────

    def job_update(self, job_id: int, title: str, company: str,
                   stage: str, ok: bool, extra: str = "") -> None:
        """Call once per job per stage as it completes (or fails)."""
        mark_plain = "✓" if ok else "✗"
        extra_str = f"  {extra}" if extra else ""
        self.logger.info("Job %d [%s] %s%s  (%s @ %s)",
                         job_id, stage, mark_plain, extra_str, title, company)

        if job_id not in self._job_statuses:
            self._job_statuses[job_id] = {"title": title, "company": company, "stages": {}}
        self._job_statuses[job_id]["stages"][stage] = {"ok": ok, "extra": extra}

        mark_rich = "[green]✓[/green]" if ok else "[red]✗[/red]"
        self._console.print(
            f"  Job [bold]{job_id}[/bold]  {title[:42]:<42}  {stage:<12} {mark_rich}{extra_str}"
        )

    def info(self, msg: str) -> None:
        self.logger.info(msg)
        self._console.print(f"  [dim]{msg}[/dim]")

    def error(self, msg: str) -> None:
        self.logger.error(msg)
        self._console.print(f"  [red]{msg}[/red]")

    # ── end-of-run summary ───────────────────────────────────────────────────

    def print_summary(self) -> None:
        if not self._job_statuses:
            self._console.print("[dim]No jobs processed this run.[/dim]")
            return
        self._console.rule("[bold]Run Summary")
        for job_id, info in sorted(self._job_statuses.items()):
            self._console.print(
                f"\n[bold]Job {job_id}[/bold]  {info['title']} @ {info['company']}"
            )
            for stage, res in info["stages"].items():
                mark = "[green]✓[/green]" if res["ok"] else "[red]✗[/red]"
                extra = f"  {res['extra']}" if res["extra"] else ""
                self._console.print(f"  {stage:<16} {mark}{extra}")
        self.logger.info("Summary: %d job(s) processed", len(self._job_statuses))

    # ── context manager ──────────────────────────────────────────────────────

    def __enter__(self) -> "RunProgress":
        self._bar.start()
        return self

    def __exit__(self, exc_type, *_) -> None:
        self._bar.stop()
        self.print_summary()
        if exc_type:
            self.logger.error("Run aborted with exception")
        else:
            self.logger.info("Run complete")
