#!/usr/bin/env python3
"""回滚演练脚本：在 docker compose 演练环境里自动把 api 回滚到上一版并计时出报告。

步骤（顺序固定）：
  1. preflight       预检：读环境变量、校验镜像标签存在、校验 compose 可解析
  2. snapshot_before 记录回滚前的 docker ps / compose ps / /version
  3. switch_version  切换目标镜像标签并校验渲染出的镜像引用
  4. compose_up      应用变更（docker compose up -d）
  5. health_check    轮询 /health，超时即失败
  6. verify_version  校验 /version 已是回滚目标版本
  7. snapshot_after  记录回滚后的 docker ps / compose ps / /version
  8. report          生成 JSON + Markdown 双份报告

用法：
  python3 scripts/rollback_drill.py                # 真实演练
  python3 scripts/rollback_drill.py --dry-run      # 只打印将要执行的命令
  DRILL_FAIL_STEP=health_check python3 scripts/rollback_drill.py   # 失败注入

退出码：0 成功；2 某步骤失败（演练判定失败）；3 预检/配置错误；4 脚本内部错误。
"""

from __future__ import annotations

import argparse
import json
import os
import platform
import re
import shlex
import subprocess
import sys
import time
import urllib.error
import urllib.request
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

SCRIPT_DIR = Path(__file__).resolve().parent
REPO_ROOT = SCRIPT_DIR.parent

EXIT_OK = 0
EXIT_STEP_FAILED = 2
EXIT_PREFLIGHT = 3
EXIT_INTERNAL = 4

# 步骤定义：key -> 中文标题
STEPS: list[tuple[str, str]] = [
    ("preflight", "预检（环境变量 / 镜像标签 / compose 解析）"),
    ("snapshot_before", "记录回滚前状态（docker ps / compose ps / version）"),
    ("switch_version", "切镜像版本（渲染并校验目标镜像引用）"),
    ("compose_up", "应用变更（docker compose up -d）"),
    ("health_check", "健康检查轮询（/health）"),
    ("verify_version", "校验版本（/version 应为回滚目标）"),
    ("snapshot_after", "记录回滚后状态（docker ps / compose ps / version）"),
    ("report", "生成报告（JSON + Markdown）"),
]
STEP_KEYS = [k for k, _ in STEPS]
STEP_TITLES = dict(STEPS)

REQUIRED_ENV = [
    "APP_IMAGE_REPO",
    "APP_CURRENT_TAG",
    "APP_ROLLBACK_TAG",
    "DRILL_API_BASE_URL",
    "NGINX_HTTP_PORT",
    "POSTGRES_USER",
    "POSTGRES_PASSWORD",
    "POSTGRES_DB",
    "COMPOSE_FILE",
]


# --------------------------------------------------------------------------- #
# 基础设施
# --------------------------------------------------------------------------- #
def now_iso() -> str:
    return datetime.now(timezone.utc).astimezone().isoformat(timespec="milliseconds")


def log(msg: str) -> None:
    print(f"[{datetime.now().strftime('%H:%M:%S')}] {msg}", flush=True)


def redact(text: str) -> str:
    """抹掉 URL 里的 userinfo 与本机绝对路径中的用户名，报告里不留凭据。"""
    text = re.sub(r"(?<=://)[^/@\s]+:[^/@\s]+@", "***@", text)
    return text


def load_env_file(path: Path) -> list[str]:
    """把 KEY=VALUE 文件读进 os.environ（已存在的环境变量优先，不被覆盖）。"""
    loaded: list[str] = []
    if not path.is_file():
        return loaded
    for raw in path.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        if line.startswith("export "):
            line = line[len("export ") :]
        if "=" not in line:
            continue
        key, value = line.split("=", 1)
        key = key.strip()
        value = value.strip().strip('"').strip("'")
        if key and key not in os.environ:
            os.environ[key] = value
            loaded.append(key)
    return loaded


class StepFailure(Exception):
    """某步骤判定失败（演练失败，退出码 2）。"""


class PreflightError(Exception):
    """预检不通过（配置问题，退出码 3）。"""


@dataclass
class CommandResult:
    command: str
    returncode: int
    stdout: str = ""
    stderr: str = ""
    duration_seconds: float = 0.0
    dry_run: bool = False


class Runner:
    """命令执行器。dry_run 时只打印、不执行。"""

    def __init__(self, dry_run: bool) -> None:
        self.dry_run = dry_run
        self.history: list[dict[str, Any]] = []

    def run(
        self,
        argv: list[str],
        *,
        env_extra: dict[str, str] | None = None,
        timeout: float | None = None,
        check: bool = False,
        quiet: bool = False,
    ) -> CommandResult:
        printable = " ".join(shlex.quote(a) for a in argv)
        if env_extra:
            prefix = " ".join(f"{k}={shlex.quote(v)}" for k, v in env_extra.items())
            printable = f"{prefix} {printable}"

        if self.dry_run:
            print(f"[dry-run] 将要执行: {printable}", flush=True)
            self.history.append(
                {"command": redact(printable), "returncode": None, "duration_seconds": 0.0}
            )
            return CommandResult(printable, 0, "", "", 0.0, dry_run=True)

        env = dict(os.environ)
        if env_extra:
            env.update(env_extra)

        if not quiet:
            print(f"$ {printable}", flush=True)
        started = time.perf_counter()
        try:
            proc = subprocess.run(
                argv,
                capture_output=True,
                text=True,
                timeout=timeout,
                env=env,
                check=False,
            )
        except FileNotFoundError as exc:
            raise PreflightError(f"命令不存在: {argv[0]}") from exc
        except subprocess.TimeoutExpired as exc:
            raise StepFailure(f"命令超时（>{timeout}s）: {printable}") from exc
        elapsed = time.perf_counter() - started

        if check and proc.returncode != 0:
            tail = (proc.stderr or proc.stdout or "").strip().splitlines()[-5:]
            raise StepFailure(
                f"命令失败（退出码 {proc.returncode}）: {printable}\n" + "\n".join(tail)
            )

        self.history.append(
            {
                "command": redact(printable),
                "returncode": proc.returncode,
                "duration_seconds": round(elapsed, 3),
            }
        )
        if not quiet and proc.stdout.strip():
            for line in proc.stdout.strip().splitlines():
                print(f"  {line}", flush=True)
        return CommandResult(printable, proc.returncode, proc.stdout, proc.stderr, elapsed)


@dataclass
class StepRecord:
    key: str
    title: str
    started_at: str = ""
    ended_at: str = ""
    duration_seconds: float = 0.0
    status: str = "pending"  # ok / failed / skipped / planned
    detail: str = ""
    error: str = ""
    commands: list[str] = field(default_factory=list)

    def as_dict(self) -> dict[str, Any]:
        return {
            "step": self.key,
            "title": self.title,
            "started_at": self.started_at,
            "ended_at": self.ended_at,
            "duration_seconds": round(self.duration_seconds, 3),
            "status": self.status,
            "detail": self.detail,
            "error": self.error,
            "commands": self.commands,
        }


# --------------------------------------------------------------------------- #
# 演练主体
# --------------------------------------------------------------------------- #
class RollbackDrill:
    def __init__(self, args: argparse.Namespace) -> None:
        self.args = args
        self.dry_run: bool = args.dry_run
        self.runner = Runner(dry_run=args.dry_run)
        self.report_dir = Path(args.report_dir).resolve()
        self.records: dict[str, StepRecord] = {
            key: StepRecord(key=key, title=title) for key, title in STEPS
        }
        self.failed_step: str | None = None
        self.failure_reason = ""

        repo = os.environ.get("APP_IMAGE_REPO", "")
        self.image_repo = repo
        self.current_tag = os.environ.get("APP_CURRENT_TAG", "")
        self.rollback_tag = os.environ.get("APP_ROLLBACK_TAG", "")
        self.base_url = os.environ.get("DRILL_API_BASE_URL", "").rstrip("/")
        self.compose_file = os.environ.get("COMPOSE_FILE", "drill/docker-compose.yml")
        self.compose_project = os.environ.get("COMPOSE_PROJECT_NAME", "member-space-drill")
        self.env_file = os.environ.get("DRILL_ENV_FILE", "")
        self.health_path = os.environ.get("HEALTH_CHECK_PATH", "/health")
        self.version_path = os.environ.get("VERSION_CHECK_PATH", "/version")
        self.health_timeout = float(os.environ.get("HEALTH_CHECK_TIMEOUT_SECONDS", "60"))
        self.health_interval = float(os.environ.get("HEALTH_CHECK_INTERVAL_SECONDS", "2"))
        self.sla_seconds = float(os.environ.get("ROLLBACK_SLA_SECONDS", "900"))
        self.fail_step = os.environ.get("DRILL_FAIL_STEP", "").strip()

        self.start_snapshot: dict[str, Any] = {}
        self.end_snapshot: dict[str, Any] = {}
        self.before_version: dict[str, Any] = {}
        self.after_version: dict[str, Any] = {}
        self.rendered_image = ""
        self.health_attempts: list[dict[str, Any]] = []
        self.drill_started_at = ""
        self.drill_finished_at = ""
        self.total_seconds = 0.0

    # ---------------- 小工具 ---------------- #
    def image_ref(self, tag: str) -> str:
        """镜像完整引用只由环境变量拼出，脚本内不写死任何地址。"""
        return f"{self.image_repo}:{tag}"

    def tag_env(self, tag: str) -> dict[str, str]:
        return {"APP_IMAGE": self.image_ref(tag), "APP_IMAGE_TAG": tag}

    def compose_argv(self) -> list[str]:
        argv = ["docker", "compose"]
        path = Path(self.compose_file)
        if not path.is_absolute():
            path = REPO_ROOT / path
        argv += ["-f", str(path)]
        if self.env_file:
            env_path = Path(self.env_file)
            if not env_path.is_absolute():
                env_path = REPO_ROOT / env_path
            argv += ["--env-file", str(env_path)]
        argv += ["--project-name", self.compose_project]
        return argv

    @staticmethod
    def _ps_format() -> str:
        return "table {{.Names}}\t{{.Image}}\t{{.Status}}\t{{.Ports}}"

    def http_get(self, path: str, timeout: float = 3.0) -> tuple[int, str]:
        url = f"{self.base_url}{path}"
        req = urllib.request.Request(url, headers={"Accept": "application/json"})
        try:
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                return resp.getcode(), resp.read().decode("utf-8", "replace")
        except urllib.error.HTTPError as exc:
            return exc.code, exc.read().decode("utf-8", "replace")
        except (urllib.error.URLError, OSError) as exc:
            return 0, str(getattr(exc, "reason", exc))

    def collect_ps(self) -> dict[str, Any]:
        compose_ps = self.runner.run(
            self.compose_argv() + ["ps", "-a"], check=False, quiet=True
        )
        docker_ps = self.runner.run(
            ["docker", "ps", "-a", "--format", self._ps_format()], check=False, quiet=True
        )
        # 容器清单（不含时间戳），用于前后对比
        names = self.runner.run(
            ["docker", "ps", "-a", "--format", "{{.Names}}\t{{.Image}}\t{{.Status}}"],
            check=False,
            quiet=True,
        )
        return {
            "captured_at": now_iso(),
            "compose_ps": redact(compose_ps.stdout.strip()),
            "docker_ps": redact(docker_ps.stdout.strip()),
            "containers": redact(names.stdout.strip()),
        }

    def _maybe_inject(self, key: str) -> None:
        """非 health_check 的失败注入：本步动作正常完成后判定失败。"""
        if self.fail_step and self.fail_step == key and key != "health_check":
            raise StepFailure(f"已注入故障：DRILL_FAIL_STEP={key}，本步骤按预期失败")

    def _run_step(self, key: str, fn) -> None:
        rec = self.records[key]
        rec.started_at = now_iso()
        t0 = time.perf_counter()
        log(f"==> [{key}] {rec.title}")
        try:
            detail = fn()
            rec.detail = detail or ""
            if self.dry_run and key != "report":
                rec.status = "planned"
                print(f"    [dry-run] 步骤已规划，未执行（不影响容器）", flush=True)
            else:
                rec.status = "ok"
                print(f"    [ok] {rec.detail}", flush=True)
        except StepFailure as exc:
            rec.status = "failed"
            rec.error = redact(str(exc))
            self.failed_step = key
            self.failure_reason = redact(str(exc))
            print(f"    [failed] {rec.error}", flush=True)
        finally:
            rec.ended_at = now_iso()
            rec.duration_seconds = time.perf_counter() - t0
            rec.commands = [h["command"] for h in self.runner.history]
            if rec.status in ("ok", "failed", "planned"):
                log(
                    f"<== [{key}] status={rec.status} duration={rec.duration_seconds:.2f}s"
                )

    # ---------------- 各步骤 ---------------- #
    def step_preflight(self) -> str:
        missing = [k for k in REQUIRED_ENV if not os.environ.get(k)]
        if missing:
            raise PreflightError(
                "缺少必需环境变量: " + ", ".join(missing) + "（示例见 .env.example）"
            )
        if self.current_tag == self.rollback_tag:
            raise PreflightError(
                f"APP_CURRENT_TAG 与 APP_ROLLBACK_TAG 相同（{self.current_tag}），无法构成回滚"
            )
        compose_path = Path(self.compose_file)
        if not compose_path.is_absolute():
            compose_path = REPO_ROOT / compose_path
        if not compose_path.is_file():
            raise PreflightError(f"compose 文件不存在: {compose_path}")

        docker_ver = self.runner.run(
            ["docker", "version", "--format", "{{.Server.Version}}"], check=False, quiet=True
        )
        if not self.dry_run and docker_ver.returncode != 0:
            raise PreflightError("docker daemon 不可用: " + docker_ver.stderr.strip())

        for tag in (self.current_tag, self.rollback_tag):
            ref = self.image_ref(tag)
            res = self.runner.run(["docker", "image", "inspect", ref], check=False, quiet=True)
            if not self.dry_run and res.returncode != 0:
                raise PreflightError(
                    f"镜像不存在: {ref}；请先执行 bash scripts/build_images.sh"
                )

        # 校验 compose 在“当前版本”下可解析
        cfg = self.runner.run(
            self.compose_argv() + ["config", "-q"],
            env_extra=self.tag_env(self.current_tag),
            check=False,
            quiet=True,
        )
        if not self.dry_run and cfg.returncode != 0:
            raise PreflightError("docker compose config 解析失败: " + redact(cfg.stderr.strip())[:800])

        if self.fail_step and self.fail_step not in STEP_KEYS:
            raise PreflightError(
                f"DRILL_FAIL_STEP={self.fail_step} 不是合法步骤名，可选: {', '.join(STEP_KEYS)}"
            )

        target = self.image_ref(self.rollback_tag)
        detail = (
            f"环境变量齐备；镜像 {self.image_ref(self.current_tag)} 与 "
            f"{target} 均存在；compose 解析通过；故障注入={self.fail_step or '未启用'}"
        )
        if self.dry_run:
            detail = f"预检项: {', '.join(REQUIRED_ENV)} + 两个镜像标签 + compose 解析（dry-run 不做实际校验）"
        return detail

    def step_snapshot_before(self) -> str:
        self.start_snapshot = self.collect_ps()
        if self.dry_run:
            print(
                f"    [dry-run] 将读取 {self.base_url}{self.version_path} 作为回滚前版本，"
                "并落盘 docker ps / compose ps 快照",
                flush=True,
            )
            return "计划：采集回滚前 docker ps / compose ps / version"
        code, body = self.http_get(self.version_path)
        if code != 200:
            raise StepFailure(
                f"回滚前无法读取 {self.base_url}{self.version_path}（HTTP {code}）："
                "演练环境可能未启动，请先启动演示环境并确认运行在 APP_CURRENT_TAG"
            )
        try:
            self.before_version = json.loads(body)
        except json.JSONDecodeError as exc:
            raise StepFailure(f"/version 返回非 JSON: {body[:200]}") from exc
        return (
            f"回滚前版本 image_version={self.before_version.get('image_version')} "
            f"runtime_label={self.before_version.get('runtime_label')}；"
            f"主机可见容器 {len(self.start_snapshot.get('containers', '').splitlines())} 个"
        )

    def step_switch_version(self) -> str:
        cfg = self.runner.run(
            self.compose_argv() + ["config", "--format", "json"],
            env_extra=self.tag_env(self.rollback_tag),
            check=True,
            quiet=True,
        )
        if self.dry_run:
            return (
                f"计划：以 APP_IMAGE_TAG={self.rollback_tag} 渲染 compose 并校验镜像引用为 "
                f"{self.image_ref(self.rollback_tag)}"
            )
        try:
            rendered = json.loads(cfg.stdout)
            self.rendered_image = rendered["services"]["api"]["image"]
        except (json.JSONDecodeError, KeyError) as exc:
            raise StepFailure(f"无法解析 compose 渲染结果: {exc}") from exc

        expected = self.image_ref(self.rollback_tag)
        if self.rendered_image != expected:
            raise StepFailure(
                f"渲染出的镜像引用不符：期望 {expected}，实际 {self.rendered_image}"
            )
        self._maybe_inject("switch_version")
        return f"目标镜像引用已确认为 {self.rendered_image}"

    def step_compose_up(self) -> str:
        env_extra = self.tag_env(self.rollback_tag)
        forced_unhealthy = self.fail_step == "health_check"
        if forced_unhealthy:
            env_extra["APP_FORCE_UNHEALTHY"] = "1"
            log("    注入：APP_FORCE_UNHEALTHY=1（新容器健康探针返回 503）")
        res = self.runner.run(
            self.compose_argv() + ["up", "-d", "--no-deps", "api"],
            env_extra=env_extra,
            check=True,
            timeout=300,
        )
        if self.dry_run:
            return f"计划：应用镜像 {self.rollback_tag} 到 api 服务"
        changed = [
            l.strip() for l in (res.stdout + "\n" + res.stderr).splitlines() if l.strip()
        ]
        self._maybe_inject("compose_up")
        return (
            f"已应用镜像 {self.rollback_tag}，容器动作: "
            f"{'; '.join(changed)[:300] or '无变化'}"
        )

    def step_health_check(self) -> str:
        if self.dry_run:
            print(
                f"    [dry-run] 将轮询 {self.base_url}{self.health_path}，"
                f"超时 {self.health_timeout:.0f}s，间隔 {self.health_interval:.1f}s",
                flush=True,
            )
            return f"计划：轮询 {self.health_path}，超时 {self.health_timeout:.0f} 秒"

        deadline = time.monotonic() + self.health_timeout
        attempt = 0
        last = ""
        while True:
            attempt += 1
            code, body = self.http_get(self.health_path, timeout=3.0)
            ok = code == 200
            self.health_attempts.append(
                {"attempt": attempt, "at": now_iso(), "http_status": code, "ok": ok}
            )
            snippet = body.strip().replace("\n", " ")[:160]
            print(f"    #尝试 {attempt}: HTTP {code} {snippet}", flush=True)
            if ok:
                return f"健康检查通过，共轮询 {attempt} 次"
            last = f"HTTP {code} {snippet}"
            if time.monotonic() >= deadline:
                raise StepFailure(
                    f"健康检查超时（{self.health_timeout:.0f}s / {attempt} 次）：最后一次 {last}"
                )
            time.sleep(min(self.health_interval, max(0.0, deadline - time.monotonic())))

    def step_verify_version(self) -> str:
        if self.dry_run:
            print(
                f"    [dry-run] 将校验 {self.base_url}{self.version_path} 的 "
                f"image_version == {self.rollback_tag}",
                flush=True,
            )
            return f"计划：校验 /version 返回 {self.rollback_tag}"
        code, body = self.http_get(self.version_path)
        if code != 200:
            raise StepFailure(f"/version 不可用（HTTP {code}）：{body[:200]}")
        try:
            self.after_version = json.loads(body)
        except json.JSONDecodeError as exc:
            raise StepFailure(f"/version 返回非 JSON: {body[:200]}") from exc

        actual = self.after_version.get("image_version")
        if actual != self.rollback_tag:
            raise StepFailure(
                f"版本校验失败：期望 {self.rollback_tag}，实际 {actual}"
                f"（回滚前为 {self.before_version.get('image_version')}）"
            )
        before = self.before_version.get("image_version")
        self._maybe_inject("verify_version")
        return f"版本已由 {before} 变为 {actual}，回滚生效"

    def step_snapshot_after(self) -> str:
        self.end_snapshot = self.collect_ps()
        if self.dry_run:
            print("    [dry-run] 将再次采集 docker ps / compose ps / version", flush=True)
            return "计划：采集回滚后 docker ps / compose ps / version"
        code, body = self.http_get(self.version_path)
        if code == 200 and not self.after_version:
            try:
                self.after_version = json.loads(body)
            except json.JSONDecodeError:
                pass
        self._maybe_inject("snapshot_after")
        return (
            f"已记录回滚后状态；主机可见容器 "
            f"{len(self.end_snapshot.get('containers', '').splitlines())} 个"
        )

    def step_report(self) -> str:
        self.drill_finished_at = now_iso()
        ok = self.failed_step is None
        sla_met = (not self.dry_run) and ok and self.total_seconds <= self.sla_seconds

        status = "dry_run" if self.dry_run else ("succeeded" if ok else "failed")
        report = {
            "report_version": 1,
            "drill": {
                "name": "会员空间平台 · 应用回滚演练",
                "mode": "dry-run" if self.dry_run else "real",
                "status": status,
                "started_at": self.drill_started_at,
                "finished_at": self.drill_finished_at,
                "total_duration_seconds": round(self.total_seconds, 3),
                "total_duration_human": f"{self.total_seconds:.2f} 秒",
                "sla_limit_seconds": self.sla_seconds,
                "sla_met": sla_met,
                "failed_step": self.failed_step,
                "failure_reason": self.failure_reason,
                "fail_injection": self.fail_step or None,
                "exit_code": (
                    EXIT_OK
                    if self.dry_run or ok
                    else (EXIT_STEP_FAILED if self.failed_step else EXIT_PREFLIGHT)
                ),
            },
            "environment": {
                "compose_project": self.compose_project,
                "compose_file": redact(str(Path(self.compose_file))),
                "api_base_url": redact(self.base_url),
                "health_path": self.health_path,
                "version_path": self.version_path,
                "image_repo": os.environ.get("APP_IMAGE_REPO", ""),
                "from_tag": self.current_tag,
                "to_tag": self.rollback_tag,
                "platform": f"{platform.system()} {platform.release()}",
                "python": platform.python_version(),
            },
            "versions": {
                "before": self.before_version,
                "after": self.after_version,
                "rendered_image": self.rendered_image,
            },
            "steps": [self.records[k].as_dict() for k in STEP_KEYS],
            "health_check_attempts": self.health_attempts,
            "docker_state": {
                "before": self.start_snapshot,
                "after": self.end_snapshot,
            },
            "commands": self.runner.history,
        }

        self.report_dir.mkdir(parents=True, exist_ok=True)
        json_path = self.report_dir / "report.json"
        md_path = self.report_dir / "report.md"
        json_path.write_text(
            json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
        )
        md_path.write_text(self.render_markdown(report), encoding="utf-8")

        # dry-run 不采集容器快照：写出明确标记，而不是留一个空文件（空文件会被误读成"采集失败"）
        dry_mark = "dry-run：本步骤未执行，未采集容器快照\n" if self.dry_run else ""
        (self.report_dir / "docker_ps_before.txt").write_text(
            dry_mark or (self.start_snapshot.get("docker_ps", "") + "\n"), encoding="utf-8"
        )
        (self.report_dir / "docker_ps_after.txt").write_text(
            dry_mark or (self.end_snapshot.get("docker_ps", "") + "\n"), encoding="utf-8"
        )
        (self.report_dir / "compose_ps_before.txt").write_text(
            dry_mark or (self.start_snapshot.get("compose_ps", "") + "\n"), encoding="utf-8"
        )
        (self.report_dir / "compose_ps_after.txt").write_text(
            dry_mark or (self.end_snapshot.get("compose_ps", "") + "\n"), encoding="utf-8"
        )
        return f"已写出 {json_path.name} 与 {md_path.name} -> {self.report_dir}"

    # ---------------- Markdown 渲染 ---------------- #
    def render_markdown(self, report: dict[str, Any]) -> str:
        d = report["drill"]
        env = report["environment"]
        lines: list[str] = []
        lines.append("# 应用回滚演练报告")
        lines.append("")
        if d["mode"] == "dry-run":
            lines.append("> 模式：**--dry-run（仅打印命令，未改变任何容器状态）**")
        else:
            lines.append(f"> 模式：**真实演练**（故障注入：{d['fail_injection'] or '未启用'}）")
        lines.append("")

        if d["status"] == "succeeded":
            verdict = "✅ **回滚成功**"
        elif d["status"] == "failed":
            verdict = f"❌ **演练失败：步骤 `{d['failed_step']}` 未通过**"
        else:
            verdict = "ℹ️ **演练计划预览（dry-run，未执行）**"
        lines.append(f"## 结论：{verdict}")
        lines.append("")
        lines.append("| 指标 | 值 |")
        lines.append("| --- | --- |")
        lines.append(f"| 回滚总耗时 | **{d['total_duration_seconds']:.2f} 秒** |")
        lines.append(
            f"| SLA（≤ {d['sla_limit_seconds']:.0f} 秒 / 15 分钟） | "
            f"{'✅ 达标' if d['sla_met'] else ('—（dry-run）' if d['mode'] == 'dry-run' else '❌ 未达标')} |"
        )
        lines.append(f"| 演练开始 | {d['started_at']} |")
        lines.append(f"| 演练结束 | {d['finished_at']} |")
        lines.append(f"| 失败步骤 | {d['failed_step'] or '无'} |")
        lines.append(f"| 退出码（预期） | {d['exit_code']} |")
        lines.append(f"| 镜像仓库 | {env['image_repo']} |")
        lines.append(f"| 版本变化 | {env['from_tag']} → {env['to_tag']} |")
        lines.append(f"| 演练入口 | {env['api_base_url']} |")
        lines.append("")

        if d["failure_reason"]:
            lines.append("### 失败原因")
            lines.append("")
            lines.append("```text")
            lines.append(d["failure_reason"])
            lines.append("```")
            lines.append("")

        lines.append("## 步骤明细")
        lines.append("")
        lines.append("| # | 步骤 | 开始时间 | 结束时间 | 耗时(秒) | 结果 | 说明 |")
        lines.append("| --- | --- | --- | --- | --- | --- | --- |")
        status_label = {
            "ok": "✅ 成功",
            "failed": "❌ 失败",
            "planned": "⏭️ 计划（未执行）",
            "skipped": "➖ 跳过",
            "pending": "⏸️ 未执行",
        }
        for idx, step in enumerate(report["steps"], start=1):
            label = status_label.get(step["status"], step["status"])
            if step["status"] == "failed":
                label = f"**❌ 失败（{step['step']}）**"
            detail = (step["detail"] or step["error"] or "").replace("|", "\\|")
            detail = detail.replace("\n", "<br>")[:400]
            lines.append(
                f"| {idx} | `{step['step']}`<br>{step['title']} | {step['started_at'] or '—'} | "
                f"{step['ended_at'] or '—'} | {step['duration_seconds']:.2f} | {label} | {detail} |"
            )
        lines.append("")
        total_step = sum(s["duration_seconds"] for s in report["steps"])
        lines.append(f"步骤耗时合计（含报告生成）：{total_step:.2f} 秒")
        lines.append("")

        lines.append("## 版本校验")
        lines.append("")
        lines.append("| 时点 | image_version | runtime_label |")
        lines.append("| --- | --- | --- |")
        b = report["versions"]["before"] or {}
        a = report["versions"]["after"] or {}
        lines.append(
            f"| 回滚前 | {b.get('image_version', '—')} | {b.get('runtime_label', '—')} |"
        )
        lines.append(
            f"| 回滚后 | {a.get('image_version', '—')} | {a.get('runtime_label', '—')} |"
        )
        lines.append("")

        if report["health_check_attempts"]:
            lines.append("## 健康检查轮询记录")
            lines.append("")
            lines.append("| 次数 | 时间 | HTTP | 结果 |")
            lines.append("| --- | --- | --- | --- |")
            for att in report["health_check_attempts"]:
                lines.append(
                    f"| {att['attempt']} | {att['at']} | {att['http_status']} | "
                    f"{'✅' if att['ok'] else '❌'} |"
                )
            lines.append("")

        lines.append("## 容器状态（回滚前）")
        lines.append("")
        lines.append("```text")
        lines.append(report["docker_state"]["before"].get("docker_ps", "") or "(未采集)")
        lines.append("```")
        lines.append("")
        lines.append("## 容器状态（回滚后）")
        lines.append("")
        lines.append("```text")
        lines.append(report["docker_state"]["after"].get("docker_ps", "") or "(未采集)")
        lines.append("```")
        lines.append("")

        lines.append("## 执行过的命令")
        lines.append("")
        lines.append("```text")
        if report["commands"]:
            for cmd in report["commands"]:
                rc = "dry-run" if cmd["returncode"] is None else cmd["returncode"]
                lines.append(f"rc={rc}  ({cmd['duration_seconds']:.2f}s)  {cmd['command']}")
        else:
            lines.append("(dry-run：未执行任何命令)")
        lines.append("```")
        lines.append("")
        lines.append("---")
        lines.append("本报告由 scripts/rollback_drill.py 自动生成；环境变量与凭据均不落盘。")
        return "\n".join(lines) + "\n"

    # ---------------- 编排 ---------------- #
    def run(self) -> int:
        self.drill_started_at = now_iso()
        t0 = time.perf_counter()
        log(
            f"回滚演练开始 mode={'dry-run' if self.dry_run else 'real'} "
            f"project={self.compose_project} tag={self.current_tag} -> {self.rollback_tag}"
        )
        if self.fail_step:
            log(f"故障注入已启用: DRILL_FAIL_STEP={self.fail_step}")

        handlers = {
            "preflight": self.step_preflight,
            "snapshot_before": self.step_snapshot_before,
            "switch_version": self.step_switch_version,
            "compose_up": self.step_compose_up,
            "health_check": self.step_health_check,
            "verify_version": self.step_verify_version,
            "snapshot_after": self.step_snapshot_after,
            "report": self.step_report,
        }

        aborted_by: str | None = None
        for key in STEP_KEYS:
            if aborted_by and key != "report":
                rec = self.records[key]
                rec.status = "skipped"
                rec.started_at = rec.ended_at = now_iso()
                rec.detail = f"因 {aborted_by} 失败而跳过（回滚已中止）"
                continue
            try:
                self._run_step(key, handlers[key])
            except PreflightError as exc:
                self.total_seconds = time.perf_counter() - t0
                log(f"预检失败：{exc}")
                print(f"ERROR: {exc}", file=sys.stderr)
                return EXIT_PREFLIGHT
            if self.failed_step:
                aborted_by = self.failed_step
                if key != "report":
                    log(f"!! 步骤 {key} 失败，立即停止后续步骤（不继续回滚）")

        self.total_seconds = time.perf_counter() - t0
        # report 步骤在 total 之后生成，需要回填最终耗时
        if not self.dry_run:
            self.finalize_report()

        rec = self.records["report"]
        print()
        if self.dry_run:
            print(
                f"DRY-RUN 完成：{len(self.runner.history)} 条命令已打印，未执行任何一条，"
                "容器状态未变"
            )
            print(f"报告：{self.report_dir / 'report.md'}")
            return EXIT_OK
        if self.failed_step:
            print(
                f"演练失败：步骤 {self.failed_step} 未通过，退出码 {EXIT_STEP_FAILED}；"
                f"报告已标出失败步骤 -> {self.report_dir / 'report.md'}"
            )
            return EXIT_STEP_FAILED
        print(
            f"演练成功：回滚总耗时 {self.total_seconds:.2f} 秒"
            f"（SLA {self.sla_seconds:.0f} 秒）；报告 -> {self.report_dir / 'report.md'}"
        )
        return EXIT_OK

    def finalize_report(self) -> None:
        """用最终总耗时重写一次报告（步骤内生成时还不知道总耗时）。"""
        json_path = self.report_dir / "report.json"
        if not json_path.is_file():
            return
        data = json.loads(json_path.read_text(encoding="utf-8"))
        # 报告步骤自身在生成时尚未结束，这里用最终记录回填全部步骤
        data["steps"] = [self.records[k].as_dict() for k in STEP_KEYS]
        data["drill"]["total_duration_seconds"] = round(self.total_seconds, 3)
        data["drill"]["total_duration_human"] = f"{self.total_seconds:.2f} 秒"
        data["drill"]["sla_met"] = self.failed_step is None and self.total_seconds <= self.sla_seconds
        data["drill"]["finished_at"] = self.drill_finished_at
        data["drill"]["exit_code"] = EXIT_OK if self.failed_step is None else EXIT_STEP_FAILED
        json_path.write_text(
            json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
        )
        (self.report_dir / "report.md").write_text(
            self.render_markdown(data), encoding="utf-8"
        )


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="docker compose 环境下的应用回滚演练脚本（计时 + 报告）"
    )
    parser.add_argument(
        "--dry-run", action="store_true", help="只打印将要执行的命令，不改变任何容器状态"
    )
    parser.add_argument(
        "--report-dir",
        default=os.environ.get("REPORT_DIR", "reports"),
        help="报告输出目录（默认取 REPORT_DIR，或 ./reports）",
    )
    parser.add_argument(
        "--env-file",
        default=os.environ.get("DRILL_ENV_FILE", ".env"),
        help="环境变量文件路径（默认 .env；已存在的环境变量优先）",
    )
    args = parser.parse_args(argv)

    env_path = Path(args.env_file)
    if not env_path.is_absolute():
        env_path = REPO_ROOT / env_path
    loaded = load_env_file(env_path)
    if loaded:
        log(f"已从 {env_path.name} 载入 {len(loaded)} 个环境变量: {', '.join(loaded)}")
    os.environ.setdefault("DRILL_ENV_FILE", str(env_path))
    if env_path.is_file():
        os.environ["DRILL_ENV_FILE"] = str(env_path)

    drill = RollbackDrill(args)
    try:
        return drill.run()
    except PreflightError as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return EXIT_PREFLIGHT
    except Exception as exc:  # noqa: BLE001
        print(f"INTERNAL ERROR: {type(exc).__name__}: {exc}", file=sys.stderr)
        return EXIT_INTERNAL


if __name__ == "__main__":
    sys.exit(main())
