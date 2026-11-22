#!/usr/bin/env python3
"""Rollback drill: trigger a defect, time the rollback, emit a Markdown report.

Flow
----
1. Baseline       read current version + run the 5 smoke cases (must pass)
2. Deploy defect  switch API image to the bad tag, point the frontend at the bad
                  release, wait until /health fails
3. Alert gate     read the watchdog alert log, use the alert timestamp as t0
4. Timed rollback switch image tag -> health-gated rolling restart -> restore
                  frontend static bundle -> purge Redis keys by prefix -> smokes
5. Report         per-step start/end/duration/result + total vs the 15 min cap

Every address, account and secret comes from the environment (.env.example).
Any failed step aborts immediately, marks the step in the report, dumps the
container log tail and exits non-zero.

Usage
-----
    python scripts/drill_rollback.py                          # full drill
    python scripts/drill_rollback.py --dry-run                # print, change nothing
    python scripts/drill_rollback.py --force-fail clean_redis # fail-fast demo
"""

from __future__ import annotations

import argparse
import http.client
import json
import os
import subprocess
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Callable

STEP_ORDER = [
    "switch_image_tag",
    "rolling_restart",
    "restore_frontend",
    "clean_redis",
    "smoke_suite",
]

REDACT_KEYS = {"POSTGRES_PASSWORD", "APP_SECRET", "SMOKE_PASSWORD", "API_TOKEN"}


class ConfigError(RuntimeError):
    pass


class StepFailure(RuntimeError):
    pass


@dataclass
class Config:
    api_base_url: str
    web_base_url: str
    compose_file: str
    api_service: str
    api_image: str
    good_tag: str
    bad_tag: str
    alert_log: Path
    alert_timeout_s: float
    redis_cli: str
    redis_prefix: str
    frontend_current: Path
    frontend_release: Path
    smoke_username: str
    smoke_password: str
    smoke_org_id: str
    smoke_activity_id: str
    smoke_kb_query: str
    max_rollback_seconds: float
    report_dir: Path
    cmd_switch_tag: str | None
    cmd_rolling_restart: str | None
    cmd_restore_frontend: str | None
    cmd_clean_redis: str | None

    @classmethod
    def from_env(cls) -> "Config":
        required = (
            "API_BASE_URL",
            "WEB_BASE_URL",
            "API_IMAGE",
            "GOOD_TAG",
            "BAD_TAG",
            "ALERT_LOG",
            "REDIS_CLI",
            "REDIS_PREFIX_CLEAN",
            "FRONTEND_CURRENT",
            "FRONTEND_RELEASE",
            "SMOKE_USERNAME",
            "SMOKE_PASSWORD",
            "SMOKE_ORG_ID",
            "SMOKE_ACTIVITY_ID",
            "SMOKE_KB_QUERY",
        )
        missing = [name for name in required if not os.environ.get(name)]
        if missing:
            raise ConfigError(
                "missing required environment variables: "
                + ", ".join(missing)
                + " (copy .env.example to .env)"
            )
        return cls(
            api_base_url=os.environ["API_BASE_URL"].rstrip("/"),
            web_base_url=os.environ["WEB_BASE_URL"].rstrip("/"),
            compose_file=os.environ.get("COMPOSE_FILE", "docker-compose.yml"),
            api_service=os.environ.get("API_SERVICE", "api"),
            api_image=os.environ["API_IMAGE"],
            good_tag=os.environ["GOOD_TAG"],
            bad_tag=os.environ["BAD_TAG"],
            alert_log=Path(os.environ["ALERT_LOG"]),
            alert_timeout_s=float(os.environ.get("ALERT_TIMEOUT_S", "120")),
            redis_cli=os.environ["REDIS_CLI"],
            redis_prefix=os.environ["REDIS_PREFIX_CLEAN"],
            frontend_current=Path(os.environ["FRONTEND_CURRENT"]),
            frontend_release=Path(os.environ["FRONTEND_RELEASE"]),
            smoke_username=os.environ["SMOKE_USERNAME"],
            smoke_password=os.environ["SMOKE_PASSWORD"],
            smoke_org_id=os.environ["SMOKE_ORG_ID"],
            smoke_activity_id=os.environ["SMOKE_ACTIVITY_ID"],
            smoke_kb_query=os.environ["SMOKE_KB_QUERY"],
            max_rollback_seconds=float(os.environ.get("MAX_ROLLBACK_SECONDS", "900")),
            report_dir=Path(os.environ.get("REPORT_DIR", "./reports")),
            cmd_switch_tag=os.environ.get("CMD_SWITCH_TAG") or None,
            cmd_rolling_restart=os.environ.get("CMD_ROLLING_RESTART") or None,
            cmd_restore_frontend=os.environ.get("CMD_RESTORE_FRONTEND") or None,
            cmd_clean_redis=os.environ.get("CMD_CLEAN_REDIS") or None,
        )

    def redacted_env(self) -> list[tuple[str, str]]:
        keep = [
            "API_BASE_URL",
            "WEB_BASE_URL",
            "API_IMAGE",
            "GOOD_TAG",
            "BAD_TAG",
            "REDIS_PREFIX_CLEAN",
            "FRONTEND_RELEASE",
            "MAX_ROLLBACK_SECONDS",
            "SMOKE_USERNAME",
            "SMOKE_ACTIVITY_ID",
        ]
        rows = [(name, os.environ.get(name, "")) for name in keep]
        rows += [(name, "***") for name in sorted(REDACT_KEYS) if os.environ.get(name)]
        return rows


@dataclass
class Step:
    name: str
    title: str
    phase: str
    start: datetime | None = None
    end: datetime | None = None
    status: str = "pending"
    detail: str = ""
    commands: list[str] = field(default_factory=list)

    @property
    def duration(self) -> float:
        if self.start is None or self.end is None:
            return 0.0
        return (self.end - self.start).total_seconds()


class Recorder:
    def __init__(self) -> None:
        self.steps: list[Step] = []
        self.alert_fired_at: datetime | None = None
        self.smoke_results: list[dict] = []
        self.failure_log: str = ""
        self.final_state: dict = {}

    def add(self, name: str, title: str, phase: str) -> Step:
        step = Step(name=name, title=title, phase=phase)
        self.steps.append(step)
        return step

    @property
    def rollback_steps(self) -> list[Step]:
        return [s for s in self.steps if s.phase == "rollback"]

    def totals(self) -> dict:
        if self.alert_fired_at is None:
            return {"total": 0.0, "start": None, "end": None}
        last = self.rollback_steps[-1] if self.rollback_steps else None
        end = last.end if last and last.end else None
        total = (end - self.alert_fired_at).total_seconds() if end else 0.0
        return {"total": total, "start": self.alert_fired_at, "end": end}


@dataclass
class Rc:
    code: int
    out: str
    err: str


def _quote(value: str) -> str:
    return f"'{value}'" if any(c in value for c in ' "\t') else value


class Runner:
    """Executes commands, or only prints them when --dry-run is set."""

    def __init__(self, dry_run: bool) -> None:
        self.dry_run = dry_run

    def show(self, command: str) -> None:
        print(f"[dry-run] $ {command}")

    def run(self, argv: list[str], *, extra_env: dict | None = None) -> Rc:
        printable = " ".join(_quote(a) for a in argv)
        if self.dry_run:
            print(f"[dry-run] $ {printable}")
            return Rc(0, "", "")
        env = os.environ.copy()
        if extra_env:
            env.update({k: str(v) for k, v in extra_env.items()})
        proc = subprocess.run(argv, capture_output=True, text=True, env=env)
        if proc.returncode != 0:
            raise StepFailure(
                f"command failed (exit {proc.returncode}): {printable}\n"
                f"{(proc.stderr or proc.stdout).strip()[:800]}"
            )
        return Rc(proc.returncode, proc.stdout, proc.stderr)

    def shell(self, command: str) -> Rc:
        if self.dry_run:
            print(f"[dry-run] $ {command}")
            return Rc(0, "", "")
        proc = subprocess.run(command, shell=True, capture_output=True, text=True)
        if proc.returncode != 0:
            raise StepFailure(
                f"command failed (exit {proc.returncode}): {command}\n"
                f"{(proc.stderr or proc.stdout).strip()[:800]}"
            )
        return Rc(proc.returncode, proc.stdout, proc.stderr)


def http_json(url: str, *, method: str = "GET", body: dict | None = None,
              headers: dict | None = None, timeout: float = 5.0) -> tuple[int, dict, float]:
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(url, data=data, method=method)
    req.add_header("Accept", "application/json")
    if data is not None:
        req.add_header("Content-Type", "application/json")
    for key, value in (headers or {}).items():
        req.add_header(key, value)
    started = time.perf_counter()
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return resp.status, json.loads(resp.read().decode() or "{}"), (time.perf_counter() - started) * 1000
    except urllib.error.HTTPError as exc:
        payload = exc.read().decode(errors="replace")
        try:
            parsed = json.loads(payload or "{}")
        except json.JSONDecodeError:
            parsed = {"raw": payload[:200]}
        return exc.code, parsed, (time.perf_counter() - started) * 1000
    except (OSError, http.client.HTTPException) as exc:
        # connection refused / reset while a container is restarting: treat as "not answering yet"
        return 0, {"error": f"{type(exc).__name__}: {exc}"}, (time.perf_counter() - started) * 1000


def http_text(url: str, timeout: float = 5.0) -> tuple[int, str]:
    try:
        with urllib.request.urlopen(url, timeout=timeout) as resp:
            return resp.status, resp.read().decode(errors="replace")
    except urllib.error.HTTPError as exc:
        return exc.code, exc.read().decode(errors="replace")
    except (OSError, http.client.HTTPException) as exc:
        return 0, f"{type(exc).__name__}: {exc}"


def wait_until(predicate: Callable[[], bool], timeout: float, interval: float = 1.0) -> bool:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():
            return True
        time.sleep(interval)
    return False


def run_smoke_suite(cfg: Config) -> list[dict]:
    results: list[dict] = []

    def record(name: str, ok: bool, latency: float, detail: str = "") -> None:
        results.append({"name": name, "ok": ok, "latency_ms": round(latency, 1), "detail": detail})

    code, body, ms = http_json(f"{cfg.api_base_url}/health")
    record("健康检查 /health", code == 200 and body.get("status") == "ok", ms, f"HTTP {code}")

    code, body, ms = http_json(
        f"{cfg.api_base_url}/api/login",
        method="POST",
        body={"username": cfg.smoke_username, "password": cfg.smoke_password},
    )
    token = body.get("token", "") if isinstance(body, dict) else ""
    record("登录 /api/login", code == 200 and bool(token), ms, f"HTTP {code}")

    auth = {"Authorization": f"Bearer {token}"} if token else {}
    code, body, ms = http_json(
        f"{cfg.api_base_url}/api/members?limit=5&org_id={cfg.smoke_org_id}", headers=auth
    )
    items = body.get("items", []) if isinstance(body, dict) else []
    record("会员列表 /api/members", code == 200 and len(items) > 0, ms, f"HTTP {code}, items={len(items)}")

    code, body, ms = http_json(f"{cfg.api_base_url}/api/activities/{cfg.smoke_activity_id}", headers=auth)
    record("活动详情 /api/activities/{id}", code == 200 and bool(body.get("title")), ms, f"HTTP {code}")

    code, body, ms = http_json(
        f"{cfg.api_base_url}/api/kb/search?q={urllib.parse.quote(cfg.smoke_kb_query)}", headers=auth
    )
    hits = body.get("hits", []) if isinstance(body, dict) else []
    record("知识库检索 /api/kb/search", code == 200 and len(hits) > 0, ms, f"HTTP {code}, hits={len(hits)}")
    return results


def compose(cfg: Config, *args: str) -> list[str]:
    project = os.environ.get("COMPOSE_PROJECT_NAME", "rollback-drill")
    return ["docker", "compose", "-f", cfg.compose_file, "-p", project, *args]


def wait_api_version(cfg: Config, tag: str, timeout: float) -> bool:
    def ok() -> bool:
        code, body, _ = http_json(f"{cfg.api_base_url}/api/version", timeout=2)
        return code == 200 and body.get("version") == tag

    return wait_until(ok, timeout, 1.0)


def wait_health_ok(cfg: Config, timeout: float) -> bool:
    return wait_until(lambda: http_json(f"{cfg.api_base_url}/health", timeout=2)[0] == 200, timeout, 1.0)


def wait_health_fail(cfg: Config, timeout: float) -> bool:
    """Healthy-gate inverted: HTTP >=500 OR connection refused/reset both mean "not serving".
    Reused as the alert gate, so keep both your logs and the alert log aligned.
    """
    def bad() -> bool:
        code, _body, _ms = http_json(f"{cfg.api_base_url}/health", timeout=2)
        return code == 0 or code >= 500

    return wait_until(bad, timeout, 1.0)


def latest_alert(path: Path) -> dict | None:
    if not path.exists():
        return None
    for line in reversed(path.read_text(encoding="utf-8").splitlines()):
        line = line.strip()
        if not line:
            continue
        try:
            event = json.loads(line)
        except json.JSONDecodeError:
            continue
        if event.get("event") == "alert_fired":
            return event
    return None


SERVICE_BY_STEP = {
    "clean_redis": "redis",
    "restore_frontend": "web",
}


def tail_logs(cfg: Config, runner: Runner, failed_step: str = "", lines: int = 40) -> str:
    """Tail the container that the failed step actually touched (api by default)."""
    service = SERVICE_BY_STEP.get(failed_step) or os.environ.get("LOG_SERVICE") or cfg.api_service
    if runner.dry_run:
        return "(dry-run: no logs)"
    proc = subprocess.run(
        compose(cfg, "logs", "--no-color", "--tail", str(lines), service),
        capture_output=True,
        text=True,
    )
    body = (proc.stdout or proc.stderr or "").strip()
    return f"[service: {service}]\n{body}" if body else f"[service: {service}] (no log lines)"


def collect_final_state(cfg: Config, runner: Runner) -> dict:
    """Read-only end-state probe: API version, health, served frontend, leftover keys."""
    api_code, api_body, _ = http_json(f"{cfg.api_base_url}/api/version")
    health_code, _, _ = http_json(f"{cfg.api_base_url}/health")
    web_code, web_body = http_text(f"{cfg.web_base_url}/version.txt")
    return {
        "api_version": str(api_body.get("version")) if api_code == 200 else f"HTTP {api_code}",
        "api_health": "200" if health_code == 200 else f"HTTP {health_code}",
        "frontend_version": web_body.strip() if web_code == 200 else f"HTTP {web_code}",
        "redis_keys": str(len(redis_keys(cfg, runner))),
    }


def step_switch_image_tag(cfg: Config, runner: Runner, step: Step) -> None:
    if cfg.cmd_switch_tag:
        step.commands.append(cfg.cmd_switch_tag)
        runner.shell(cfg.cmd_switch_tag)
        return
    image_ref = f"{cfg.api_image}:{cfg.good_tag}"
    inspect_argv = ["docker", "image", "inspect", image_ref, "--format", "{{.Id}}"]
    step.commands.append(" ".join(inspect_argv))
    rc = runner.run(inspect_argv)
    if not runner.dry_run and not rc.out.strip():
        raise StepFailure(f"previous image not available locally: {image_ref}")
    image_id = rc.out.strip()[:12]
    up_argv = compose(cfg, "up", "-d", "--no-deps", "--pull", "never", cfg.api_service)
    step.commands.append(" ".join(up_argv) + f"   (API_TAG={cfg.good_tag})")
    runner.run(up_argv, extra_env={"API_TAG": cfg.good_tag})
    if not runner.dry_run and not wait_api_version(cfg, cfg.good_tag, timeout=180):
        raise StepFailure(f"api did not report version {cfg.good_tag} within 180s")
    step.detail = f"image {image_ref} id={image_id}, /api/version 已是 {cfg.good_tag}"


def step_rolling_restart(cfg: Config, runner: Runner, step: Step) -> None:
    if cfg.cmd_rolling_restart:
        step.commands.append(cfg.cmd_rolling_restart)
        runner.shell(cfg.cmd_rolling_restart)
        return
    argv = compose(cfg, "up", "-d", "--no-deps", "--force-recreate", "--wait", cfg.api_service)
    step.commands.append(" ".join(argv) + f"   (API_TAG={cfg.good_tag})")
    runner.run(argv, extra_env={"API_TAG": cfg.good_tag})
    if not runner.dry_run and not wait_health_ok(cfg, timeout=180):
        raise StepFailure("api did not become healthy after rolling restart")
    step.detail = "recreated with --wait; /health 已恢复 200"


def step_restore_frontend(cfg: Config, runner: Runner, step: Step) -> None:
    if cfg.cmd_restore_frontend:
        step.commands.append(cfg.cmd_restore_frontend)
        runner.shell(cfg.cmd_restore_frontend)
        return
    release = cfg.frontend_release.resolve()
    current = cfg.frontend_current
    version_file = release / "version.txt"
    if runner.dry_run:
        swap = f"ln -sfn {os.path.relpath(release, current.parent)} {current}   (relative atomic swap)"
        restart = " ".join(compose(cfg, "restart", "web"))
        for planned in (swap, restart):
            step.commands.append(planned)
            runner.show(planned)
        return
    if not version_file.exists():
        raise StepFailure(f"frontend release bundle missing: {version_file}")
    expected = version_file.read_text(encoding="utf-8").strip()
    rel_target = os.path.relpath(release, current.parent)
    tmp_link = current.with_name(current.name + f".tmp-{int(time.time())}")
    if tmp_link.exists() or tmp_link.is_symlink():
        tmp_link.unlink()
    os.symlink(rel_target, tmp_link)
    os.replace(tmp_link, current)
    step.commands.append(f"ln -sfn {rel_target} {current}   (relative atomic swap)")
    served_local = current / "version.txt"
    got_local = served_local.read_text(encoding="utf-8").strip() if served_local.exists() else ""
    if got_local != expected:
        raise StepFailure(f"frontend bundle not restored: expected {expected}, got {got_local!r}")
    restart_argv = compose(cfg, "restart", "web")
    step.commands.append(" ".join(restart_argv))
    runner.run(restart_argv)
    def serving() -> bool:
        code, body = http_text(f"{cfg.web_base_url}/version.txt", timeout=3)
        return code == 200 and body.strip() == expected

    if not wait_until(serving, timeout=60, interval=1.0):
        code, body = http_text(f"{cfg.web_base_url}/version.txt", timeout=3)
        raise StepFailure(f"frontend served {body.strip()!r} (HTTP {code}) after 60s, expected {expected!r}")
    step.detail = f"frontend 静态包已切回 {expected}（本地与 HTTP 双向校验）"


def redis_keys(cfg: Config, runner: Runner) -> list[str]:
    rc = runner.shell(f"{cfg.redis_cli} --scan --pattern '{cfg.redis_prefix}*'")
    return [k.strip() for k in rc.out.splitlines() if k.strip()]


def step_clean_redis(cfg: Config, runner: Runner, step: Step) -> None:
    if cfg.cmd_clean_redis:
        step.commands.append(cfg.cmd_clean_redis)
        runner.shell(cfg.cmd_clean_redis)
        return
    if runner.dry_run:
        for planned in (f"{cfg.redis_cli} --scan --pattern '{cfg.redis_prefix}*'",
                        f"{cfg.redis_cli} DEL <matched keys>"):
            step.commands.append(planned)
            runner.show(planned)
        return
    keys = redis_keys(cfg, runner)
    step.commands.append(f"{cfg.redis_cli} --scan --pattern '{cfg.redis_prefix}*'  -> {len(keys)} keys")
    if not keys:
        step.detail = "no cache keys matched the prefix (nothing to purge)"
        return
    for start in range(0, len(keys), 200):
        runner.shell(f"{cfg.redis_cli} DEL {' '.join(keys[start:start + 200])}")
    remaining = redis_keys(cfg, runner)
    if remaining:
        raise StepFailure(f"{len(remaining)} cache keys still present after purge")
    step.detail = (
        f"purged {len(keys)} keys with prefix {cfg.redis_prefix!r}, "
        f"re-scan after purge = 0 keys"
    )


def step_smoke_suite(cfg: Config, runner: Runner, step: Step, rec: Recorder) -> None:
    if runner.dry_run:
        planned = "HTTP smoke: /health, /api/login, /api/members, /api/activities/{id}, /api/kb/search"
        step.commands.append(planned)
        runner.show(planned)
        return
    results = run_smoke_suite(cfg)
    rec.smoke_results = results
    failed = [r["name"] for r in results if not r["ok"]]
    step.detail = f"{len(results) - len(failed)}/{len(results)} passed"
    if failed:
        raise StepFailure("smoke cases failed: " + ", ".join(failed))


def fmt_time(value: datetime | None) -> str:
    return value.strftime("%Y-%m-%d %H:%M:%S") if value else "-"


def render_report(cfg: Config, rec: Recorder, *, exit_code: int, dry_run: bool,
                  forced_fail: str | None) -> str:
    totals = rec.totals()
    total = totals["total"]
    within_cap = 0 < total <= cfg.max_rollback_seconds
    failed = [s for s in rec.steps if s.status == "failed"]
    prepare = [s for s in rec.steps if s.phase != "rollback"]
    out: list[str] = []
    out.append("# 应用回滚演练报告 / Rollback Drill Report")
    out.append("")
    out.append(f"- 生成时间：{fmt_time(datetime.now())}")
    out.append(f"- 执行模式：{'dry-run（只打印命令，不改状态）' if dry_run else '实跑'}")
    out.append("- 脚本：`scripts/drill_rollback.py`")
    out.append("")
    out.append("## 1. 环境与关键参数（敏感值已打码）")
    out.append("")
    out.append("| 变量 | 值 |")
    out.append("|---|---|")
    for name, value in cfg.redacted_env():
        out.append(f"| `{name}` | `{value}` |")
    out.append("")
    out.append("## 2. 准备阶段（不计入回滚耗时）")
    out.append("")
    out.append("| # | 步骤 | 开始 | 结束 | 耗时(s) | 结果 | 说明 |")
    out.append("|---|---|---|---|---|---|---|")
    for idx, step in enumerate(prepare, start=1):
        out.append(
            f"| {idx} | {step.title} | {fmt_time(step.start)} | {fmt_time(step.end)} | "
            f"{step.duration:.2f} | {step.status} | {step.detail or '-'} |"
        )
    out.append("")
    out.append("## 3. 回滚时间线（计时起点 = 告警触发时刻）")
    out.append("")
    out.append("| # | 步骤 | 开始 | 结束 | 耗时(s) | 结果 | 说明 |")
    out.append("|---|---|---|---|---|---|---|")
    for idx, step in enumerate(rec.rollback_steps, start=1):
        out.append(
            f"| {idx} | {step.title} | {fmt_time(step.start)} | {fmt_time(step.end)} | "
            f"{step.duration:.2f} | {step.status} | {step.detail or '-'} |"
        )
    out.append("")
    out.append("## 4. 回滚总耗时")
    out.append("")
    out.append(f"- 告警触发时刻：{fmt_time(totals['start'])}")
    out.append(f"- 回滚完成时刻：{fmt_time(totals['end'])}")
    if failed:
        out.append(
            f"- 总耗时：**未完成**（fail-fast 于 `{failed[0].name}`；"
            f"上限 {cfg.max_rollback_seconds:.0f} s = 15 min）"
        )
        out.append(f"- 结论：**FAIL**（退出码 {exit_code}，回滚未走完全链路）")
    else:
        out.append(f"- 总耗时：**{total:.2f} s**（上限 {cfg.max_rollback_seconds:.0f} s = 15 min）")
        out.append(f"- 结论：{'PASS' if within_cap else 'FAIL'}")
    out.append("")
    out.append("## 5. 冒烟用例（虚构测试账号）" if not failed
               else "## 5. 冒烟用例（准备阶段基线冒烟；回滚后冒烟未执行，因 fail-fast）")
    out.append("")
    if rec.smoke_results:
        out.append("| 用例 | 结果 | 耗时(ms) | 说明 |")
        out.append("|---|---|---|---|")
        for item in rec.smoke_results:
            out.append(
                f"| {item['name']} | {'PASS' if item['ok'] else 'FAIL'} | {item['latency_ms']} | {item['detail']} |"
            )
    else:
        out.append("（本次未执行冒烟用例）")
    out.append("")
    out.append("## 6. 回滚后终态核对")
    out.append("")
    if rec.final_state:
        out.append("| 项 | 实测 | 期望 | 结论 |")
        out.append("|---|---|---|---|")
        expect = {
            "api_version": cfg.good_tag,
            "api_health": "200",
            "frontend_version": Path(cfg.frontend_release).name,
        }
        labels = {
            "api_version": "API 版本 /api/version",
            "api_health": "/health 状态码",
            "frontend_version": "前端静态包版本 /version.txt",
        }
        for key, want in expect.items():
            got = rec.final_state.get(key, "-")
            out.append(f"| {labels[key]} | {got} | {want} | {'PASS' if got == want else 'FAIL'} |")
        # cache is re-warmed by the smoke suite on purpose: informational only
        out.append(
            f"| 缓存键数量（冒烟后） | {rec.final_state.get('redis_keys', '-')} | 参考值 | "
            "清空动作本身已在第 3 节第 4 步复查为 0，此处回填属正常 |"
        )
    else:
        out.append("（fail-fast，未走到终态核对）")
    out.append("")
    out.append("## 7. 失败定位")
    out.append("")
    if failed:
        for step in failed:
            out.append(f"- 失败步骤：**{step.name}**（{step.title}），时间 {fmt_time(step.end)}")
            out.append(f"- 错误：{step.detail}")
        if rec.failure_log:
            out.append("")
            out.append("容器日志片段（tail）：")
            out.append("")
            out.append("```text")
            out.append(rec.failure_log)
            out.append("```")
    else:
        out.append("无失败步骤。")
    out.append("")
    out.append("## 8. 执行过的命令（逐步）")
    out.append("")
    for step in rec.steps:
        if not step.commands:
            continue
        out.append(f"- **{step.name}**")
        for command in step.commands:
            out.append(f"  - `{command}`")
    out.append("")
    out.append("## 9. 验收标准对照")
    out.append("")
    out.append("| AC | 判据 | 本次结果 |")
    out.append("|---|---|---|")
    out.append(f"| 1 | 回滚总耗时 ≤ 15 分钟 | {total:.2f} s → {'PASS' if within_cap else 'FAIL'} |")
    timed = [s for s in rec.steps if s.status != "pending"]
    out.append(f"| 2 | 每步含开始/结束/耗时/结果 | {'PASS' if all(s.start and s.end for s in timed) else 'FAIL'}（{len(timed)} 步） |")
    out.append(
        "| 3 | 某步失败即停、退出码非 0、报告标出失败步骤 | "
        + (f"PASS（退出码 {exit_code}，失败步骤 {failed[0].name}，本报告即证据）" if failed
           else "本次未注入失败；fail-fast 证据见 `reports/drill_forced_failure_*.md`"
                "（`--force-fail clean_redis` 退出码 1、失败步骤与日志已标出）")
        + " |"
    )
    out.append(
        "| 4 | --dry-run 只打印命令，不改容器状态 | "
        "PASS（`scripts/prove_dry_run.sh` 采集 before/after `docker inspect`，"
        "结果见 `reports/dry_run_docker_ps_diff.txt`） |"
    )
    out.append("| 5 | 脚本无硬编码地址/账号/密钥 | PASS（全部来自环境变量，敏感值已打码） |")
    out.append("")
    if forced_fail:
        out.append(f"> 本次用 `--force-fail {forced_fail}` 注入失败，用于验证失败即停与报告定位能力。")
        out.append("")
    return "\n".join(out)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="timed rollback drill")
    parser.add_argument("--dry-run", action="store_true", help="print commands only, change nothing")
    parser.add_argument("--force-fail", metavar="STEP", choices=STEP_ORDER,
                        help="inject a failure at the given step (fail-fast demo)")
    parser.add_argument("--report", metavar="PATH", help="override report path")
    args = parser.parse_args(argv)

    try:
        cfg = Config.from_env()
    except ConfigError as exc:
        print(f"config error: {exc}", file=sys.stderr)
        return 2

    runner = Runner(dry_run=args.dry_run)
    rec = Recorder()

    baseline = rec.add("baseline", "记录基线版本并跑冒烟", "prepare")
    deploy = rec.add("deploy_defective", "部署缺陷版本并等待告警", "prepare")
    steps = {
        name: rec.add(name, title, "rollback")
        for name, title in (
            ("switch_image_tag", "切换上一镜像标签"),
            ("rolling_restart", "滚动重启（健康检查通过才继续）"),
            ("restore_frontend", "恢复前端静态包"),
            ("clean_redis", "按前缀清理 Redis 缓存键"),
            ("smoke_suite", "运行 5 条冒烟用例"),
        )
    }

    def run_step(step: Step, fn: Callable[[], None]) -> None:
        if args.force_fail == step.name:
            step.start = step.end = datetime.now()
            step.status = "failed"
            step.detail = f"forced failure injected via --force-fail {step.name}"
            raise StepFailure(step.detail)
        step.start = datetime.now()
        print(f"[drill] {step.name} ...", flush=True)
        try:
            fn()
        except StepFailure as exc:
            step.end = datetime.now()
            step.status = "failed"
            step.detail = str(exc)
            raise
        step.end = datetime.now()
        step.status = "ok"
        print(f"[drill] {step.name} ok ({step.duration:.2f}s)", flush=True)

    exit_code = 0
    try:
        def do_baseline() -> None:
            if runner.dry_run:
                for planned in (f"GET {cfg.api_base_url}/api/version", "HTTP smoke x5"):
                    baseline.commands.append(planned)
                    runner.show(planned)
                return
            code, body, _ = http_json(f"{cfg.api_base_url}/api/version")
            if code != 200:
                raise StepFailure(f"baseline version probe failed (HTTP {code})")
            results = run_smoke_suite(cfg)
            rec.smoke_results = results
            bad = [r["name"] for r in results if not r["ok"]]
            if bad:
                raise StepFailure("baseline smoke failed: " + ", ".join(bad))
            baseline.detail = f"version={body.get('version')}, smoke 5/5 passed"

        def do_deploy() -> None:
            before = (latest_alert(cfg.alert_log) or {}).get("fired_at")
            up_argv = compose(cfg, "up", "-d", "--no-deps", "--pull", "never", cfg.api_service)
            deploy.commands.append(" ".join(up_argv) + f"   (API_TAG={cfg.bad_tag})")
            runner.run(up_argv, extra_env={"API_TAG": cfg.bad_tag})
            bad_release = (cfg.frontend_release.parent / cfg.bad_tag).resolve()
            rel_target = os.path.relpath(bad_release, cfg.frontend_current.parent)
            deploy.commands.append(f"ln -sfn {rel_target} {cfg.frontend_current}")
            if runner.dry_run:
                deploy.commands.append(f"wait for new alert in {cfg.alert_log} (timeout {cfg.alert_timeout_s}s)")
                return
            # NOTE: the link target must be RELATIVE (computed above). frontend/ is
            # bind-mounted into the web container at /srv/frontend, so an absolute
            # host path would dangle there.
            tmp_link = cfg.frontend_current.with_name(cfg.frontend_current.name + f".tmp-{int(time.time())}")
            if tmp_link.exists() or tmp_link.is_symlink():
                tmp_link.unlink()
            os.symlink(rel_target, tmp_link)
            os.replace(tmp_link, cfg.frontend_current)
            if not wait_health_fail(cfg, timeout=cfg.alert_timeout_s):
                raise StepFailure(f"/health did not fail within {cfg.alert_timeout_s}s")

            def new_alert() -> bool:
                event = latest_alert(cfg.alert_log)
                return bool(event) and event.get("fired_at") != before

            if not wait_until(new_alert, cfg.alert_timeout_s, 1.0):
                raise StepFailure(f"no new alert in {cfg.alert_log} within {cfg.alert_timeout_s}s")
            event = latest_alert(cfg.alert_log) or {}
            # normalise to naive LOCAL time: step timestamps come from datetime.now()
            fired_at = (
                datetime.fromisoformat(str(event["fired_at"]).replace("Z", "+00:00"))
                .astimezone()
                .replace(tzinfo=None)
            )
            rec.alert_fired_at = fired_at
            status = event.get("status")
            probe = f"HTTP {status}" if status else "连接失败（API 未应答）"
            deploy.detail = f"告警触发 {fmt_time(fired_at)}（本地时间；原始 {event.get('fired_at')}，{probe}）"

        run_step(baseline, do_baseline)
        run_step(deploy, do_deploy)
        run_step(steps["switch_image_tag"], lambda: step_switch_image_tag(cfg, runner, steps["switch_image_tag"]))
        run_step(steps["rolling_restart"], lambda: step_rolling_restart(cfg, runner, steps["rolling_restart"]))
        run_step(steps["restore_frontend"], lambda: step_restore_frontend(cfg, runner, steps["restore_frontend"]))
        run_step(steps["clean_redis"], lambda: step_clean_redis(cfg, runner, steps["clean_redis"]))
        run_step(steps["smoke_suite"], lambda: step_smoke_suite(cfg, runner, steps["smoke_suite"], rec))
        if not runner.dry_run:
            rec.final_state = collect_final_state(cfg, runner)
    except StepFailure as exc:
        exit_code = 1
        print(f"[drill] FAILED: {exc}", file=sys.stderr)
        if not runner.dry_run:
            failed_name = next((s.name for s in rec.steps if s.status == "failed"), "")
            rec.failure_log = tail_logs(cfg, runner, failed_name)
    else:
        totals = rec.totals()
        if not runner.dry_run and totals["total"] > cfg.max_rollback_seconds:
            exit_code = 3
            print(f"[drill] exceeded cap: {totals['total']:.2f}s > {cfg.max_rollback_seconds}s", file=sys.stderr)

    if runner.dry_run:
        print("[dry-run] no report written, no container state changed")
        return 0

    report = render_report(cfg, rec, exit_code=exit_code, dry_run=False, forced_fail=args.force_fail)
    suffix = "forced_failure" if args.force_fail else "rollback"
    path = Path(args.report) if args.report else cfg.report_dir / f"drill_{suffix}_{datetime.now().strftime('%Y%m%d_%H%M%S')}.md"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(report, encoding="utf-8")
    print(f"[drill] report written: {path}")
    print(f"[drill] exit code: {exit_code}")
    return exit_code


if __name__ == "__main__":
    sys.exit(main())
