"""Unit tests for the rollback drill script (stdlib unittest, no extra deps).

    python3 -m unittest discover -s tests -v
"""

import importlib.util
import os
import sys
import unittest
from datetime import datetime, timedelta
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location("drill", ROOT / "scripts" / "drill_rollback.py")
drill = importlib.util.module_from_spec(SPEC)
sys.modules["drill"] = drill
SPEC.loader.exec_module(drill)

BASE_ENV = {
    "API_BASE_URL": "http://127.0.0.1:18080",
    "WEB_BASE_URL": "http://127.0.0.1:18081",
    "API_IMAGE": "member-space-api",
    "GOOD_TAG": "v1",
    "BAD_TAG": "v2",
    "ALERT_LOG": "./run/alerts/alerts.log",
    "REDIS_CLI": "docker compose exec -T redis redis-cli",
    "REDIS_PREFIX_CLEAN": "member-space:",
    "FRONTEND_CURRENT": "./frontend/current",
    "FRONTEND_RELEASE": "./frontend/releases/v1",
    "SMOKE_USERNAME": "drill_operator",
    "SMOKE_PASSWORD": "not-a-real-password",
    "SMOKE_ORG_ID": "org_drill",
    "SMOKE_ACTIVITY_ID": "act_1001",
    "SMOKE_KB_QUERY": "回滚 演练",
}


class EnvCase(unittest.TestCase):
    def setUp(self):
        self._saved = {k: os.environ.get(k) for k in list(os.environ)}
        for key in ("CMD_SWITCH_TAG", "CMD_ROLLING_RESTART", "CMD_RESTORE_FRONTEND", "CMD_CLEAN_REDIS"):
            os.environ.pop(key, None)

    def tearDown(self):
        os.environ.clear()
        os.environ.update({k: v for k, v in self._saved.items() if v is not None})


class TestConfig(EnvCase):
    def test_missing_env_is_reported_by_name(self):
        os.environ.clear()
        os.environ.update({k: v for k, v in BASE_ENV.items() if k != "SMOKE_PASSWORD"})
        with self.assertRaises(drill.ConfigError) as ctx:
            drill.Config.from_env()
        self.assertIn("SMOKE_PASSWORD", str(ctx.exception))

    def test_full_env_loads(self):
        os.environ.clear()
        os.environ.update(BASE_ENV)
        cfg = drill.Config.from_env()
        self.assertEqual(cfg.good_tag, "v1")
        self.assertEqual(cfg.max_rollback_seconds, 900.0)

    def test_secrets_are_redacted_in_report_env(self):
        os.environ.clear()
        os.environ.update(BASE_ENV)
        os.environ["POSTGRES_PASSWORD"] = "super-secret-value"
        rows = dict(drill.Config.from_env().redacted_env())
        self.assertEqual(rows["POSTGRES_PASSWORD"], "***")
        self.assertNotIn("super-secret-value", str(rows))


class TestRecorder(EnvCase):
    def test_total_measured_from_alert_time_to_last_rollback_step(self):
        os.environ.clear()
        os.environ.update(BASE_ENV)
        rec = drill.Recorder()
        rec.add("prepare", "准备", "prepare")
        step = rec.add("switch_image_tag", "切换标签", "rollback")
        t0 = datetime(2026, 1, 1, 10, 0, 0)
        rec.alert_fired_at = t0
        step.start = t0 + timedelta(seconds=3)
        step.end = t0 + timedelta(seconds=41.5)
        step.status = "ok"
        self.assertAlmostEqual(rec.totals()["total"], 41.5, places=3)

    def test_no_alert_means_no_total(self):
        rec = drill.Recorder()
        self.assertEqual(rec.totals()["total"], 0.0)


class TestReport(EnvCase):
    def _report(self, *, failed: bool = False) -> str:
        os.environ.clear()
        os.environ.update(BASE_ENV)
        cfg = drill.Config.from_env()
        rec = drill.Recorder()
        rec.alert_fired_at = datetime(2026, 1, 1, 10, 0, 0)
        for name, title in (
            ("switch_image_tag", "切换上一镜像标签"),
            ("rolling_restart", "滚动重启"),
            ("restore_frontend", "恢复前端静态包"),
            ("clean_redis", "清理缓存键"),
            ("smoke_suite", "冒烟用例"),
        ):
            step = rec.add(name, title, "rollback")
            step.start = rec.alert_fired_at + timedelta(seconds=1)
            step.end = rec.alert_fired_at + timedelta(seconds=20)
            step.status = "failed" if failed and name == "clean_redis" else "ok"
            if step.status == "failed":
                step.detail = "boom"
        rec.smoke_results = [{"name": "健康检查 /health", "ok": True, "latency_ms": 3.2, "detail": "HTTP 200"}]
        return drill.render_report(cfg, rec, exit_code=1 if failed else 0, dry_run=False,
                                   forced_fail="clean_redis" if failed else None)

    def test_report_lists_every_timed_step_with_duration(self):
        text = self._report()
        for title in ("切换上一镜像标签", "滚动重启", "恢复前端静态包", "清理缓存键", "冒烟用例"):
            self.assertIn(title, text)
        self.assertIn("回滚总耗时", text)
        self.assertIn("PASS", text)

    def test_failed_step_is_named_in_report(self):
        text = self._report(failed=True)
        self.assertIn("失败步骤：**clean_redis**", text)
        self.assertIn("boom", text)

    def test_failed_report_does_not_claim_zero_second_rollback(self):
        text = self._report(failed=True)
        self.assertIn("未完成", text)
        self.assertIn("结论：**FAIL**", text)
        self.assertNotIn("总耗时：**0.00 s**", text)

    def test_failed_report_marks_smoke_as_baseline(self):
        text = self._report(failed=True)
        self.assertIn("准备阶段基线冒烟", text)

    def test_ac_table_points_at_reproducible_dry_run_evidence(self):
        text = self._report()
        self.assertIn("prove_dry_run.sh", text)
        self.assertIn("reports/dry_run_docker_ps_diff.txt", text)

    def test_report_never_leaks_smoke_password(self):
        text = self._report()
        self.assertNotIn(BASE_ENV["SMOKE_PASSWORD"], text)
        self.assertIn("| `SMOKE_PASSWORD` | `***` |", text)


class TestRunner(EnvCase):
    def test_dry_run_prints_without_executing(self):
        runner = drill.Runner(dry_run=True)
        rc = runner.run(["sh", "-c", "echo should-not-run"])
        self.assertEqual(rc.code, 0)
        self.assertEqual(rc.out, "")


if __name__ == "__main__":
    unittest.main()
