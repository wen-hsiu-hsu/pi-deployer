"""Deployment execution engine for pi-deployer."""

import logging
import os
import subprocess
from datetime import datetime, timezone

from health import run_health_check
from notify import send_notification

logger = logging.getLogger("pi-deployer")


def run_deploy(project, commit_info=None):
    """Execute the deployment pipeline for a project.

    Args:
        project: Merged project config dict.
        commit_info: Optional dict with commit metadata.

    Returns:
        dict with "success" (bool), "output" (str), "duration" (float).
    """
    name = project["name"]
    repo_dir = project["path"]
    timeout = project.get("timeout", 300)
    deploy_script = project.get("deploy_script")
    deploy_mode = project.get("deploy_mode", "pull-only")

    log_dir = os.environ.get("LOG_DIR", "./logs")
    os.makedirs(log_dir, exist_ok=True)
    log_file = os.path.join(log_dir, f"{name}.log")

    env = _build_env(project, commit_info)

    send_notification("triggered", project, commit_info)

    start = datetime.now(timezone.utc)
    output_lines = []

    try:
        # Step 1: git pull (always, unless script-only)
        if deploy_mode != "script-only" and not deploy_script:
            result = _run_cmd(
                ["git", "-C", repo_dir, "pull", "--ff-only"],
                env=env, timeout=timeout,
            )
            output_lines.append(result)

        # Step 2: deploy action
        if deploy_script:
            result = _run_cmd(
                ["bash", deploy_script],
                env=env, timeout=timeout, cwd=repo_dir,
            )
            output_lines.append(result)
        elif deploy_mode == "docker-compose":
            result = _run_cmd(
                ["docker", "compose", "down"],
                env=env, timeout=timeout, cwd=repo_dir,
            )
            output_lines.append(result)
            result = _run_cmd(
                ["docker", "compose", "up", "-d"],
                env=env, timeout=timeout, cwd=repo_dir,
            )
            output_lines.append(result)
        elif deploy_mode == "systemd":
            service = project.get("service_name", name)
            result = _run_cmd(
                ["sudo", "systemctl", "restart", service],
                env=env, timeout=timeout,
            )
            output_lines.append(result)
        elif deploy_mode == "script-only":
            script = project.get("deploy_script", "")
            if script:
                result = _run_cmd(
                    ["bash", script],
                    env=env, timeout=timeout, cwd=repo_dir,
                )
                output_lines.append(result)
        # pull-only: git pull already done above

        # Step 3: health check
        hc = project.get("health_check", {})
        if hc.get("enabled") and hc.get("url"):
            healthy = run_health_check(
                url=hc["url"],
                retries=hc.get("retries", 3),
                interval=hc.get("interval", 5),
            )
            if not healthy:
                raise RuntimeError(f"Health check failed: {hc['url']}")
            output_lines.append(f"Health check passed: {hc['url']}")

        duration = (datetime.now(timezone.utc) - start).total_seconds()
        output = "\n".join(output_lines)
        _write_log(log_file, name, "success", output, duration)
        send_notification("success", project, commit_info,
                          f"Deployed in {duration:.1f}s")
        return {"success": True, "output": output, "duration": duration}

    except subprocess.TimeoutExpired:
        duration = (datetime.now(timezone.utc) - start).total_seconds()
        output = "\n".join(output_lines)
        _write_log(log_file, name, "timeout", output, duration)
        send_notification("timeout", project, commit_info)
        return {"success": False, "output": output, "duration": duration}

    except Exception as e:
        duration = (datetime.now(timezone.utc) - start).total_seconds()
        output = "\n".join(output_lines) + f"\nERROR: {e}"
        _write_log(log_file, name, "failed", output, duration)
        send_notification("failed", project, commit_info, output)
        return {"success": False, "output": output, "duration": duration}


def _sanitize_env_value(value, max_length=500):
    """Sanitize a value for use as an environment variable."""
    if not isinstance(value, str):
        value = str(value)
    sanitized = "".join(c for c in value if c.isprintable())
    return sanitized[:max_length]


def _build_env(project, commit_info):
    """Build environment variables for subprocess."""
    env = os.environ.copy()
    env["DEPLOYER_PROJECT_NAME"] = _sanitize_env_value(project.get("name", ""))
    env["DEPLOYER_REPO_DIR"] = project.get("path", "")
    env["DEPLOYER_DEPLOY_MODE"] = _sanitize_env_value(project.get("deploy_mode", ""))
    env["DEPLOYER_BRANCH"] = _sanitize_env_value(project.get("branch", "main"))
    if commit_info:
        env["DEPLOYER_COMMIT_SHA"] = _sanitize_env_value(commit_info.get("sha", ""))
        env["DEPLOYER_COMMIT_AUTHOR"] = _sanitize_env_value(commit_info.get("author", ""))
        env["DEPLOYER_COMMIT_MESSAGE"] = _sanitize_env_value(commit_info.get("message", ""))
    return env


def _run_cmd(cmd, env=None, timeout=300, cwd=None):
    """Run a shell command and return its combined output."""
    logger.info("Running: %s", " ".join(cmd))
    result = subprocess.run(
        cmd,
        capture_output=True,
        text=True,
        timeout=timeout,
        env=env,
        cwd=cwd,
    )
    output = result.stdout + result.stderr
    if result.returncode != 0:
        raise RuntimeError(
            f"Command failed (exit {result.returncode}): {' '.join(cmd)}\n{output}"
        )
    return output.strip()


def _write_log(log_file, name, status, output, duration):
    """Append deploy result to the project's log file."""
    timestamp = datetime.now(timezone.utc).isoformat()
    entry = (
        f"\n{'=' * 60}\n"
        f"[{timestamp}] {name} - {status} ({duration:.1f}s)\n"
        f"{'=' * 60}\n"
        f"{output}\n"
    )
    with open(log_file, "a") as f:
        f.write(entry)
