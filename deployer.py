"""Pi-Deployer: Unified webhook deployment service for Raspberry Pi."""

import logging
import os
import re
import signal
import threading
from datetime import datetime, timezone

from dotenv import load_dotenv
from flask import Flask, jsonify, request

from config import (
    find_project_by_key,
    get_all_projects,
    get_config,
    get_project,
    load_config,
    mask_secrets,
)
from deploy import run_deploy
from verify import verify_bearer_token, verify_signature

load_dotenv()

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
logger = logging.getLogger("pi-deployer")

app = Flask(__name__)

# Per-project concurrency locks
_locks = {}
_locks_mutex = threading.Lock()

# Deploy status tracking
_deploy_status = {}

# Server start time
_start_time = datetime.now(timezone.utc)


def _get_lock(project_name):
    """Get or create a lock for a project."""
    with _locks_mutex:
        if project_name not in _locks:
            _locks[project_name] = threading.Lock()
        return _locks[project_name]


def _sync_locks():
    """Sync locks dict with current projects after config reload."""
    with _locks_mutex:
        current_names = {p["name"] for p in get_all_projects()}
        # Remove locks for deleted projects (only if not locked)
        for name in list(_locks.keys()):
            if name not in current_names and not _locks[name].locked():
                del _locks[name]
        # New projects get locks on demand via _get_lock


def _extract_commit_info(payload):
    """Extract commit info from GitHub webhook payload."""
    head_commit = payload.get("head_commit", {})
    if not head_commit:
        return None
    return {
        "sha": head_commit.get("id", ""),
        "message": head_commit.get("message", ""),
        "author": head_commit.get("author", {}).get("name", "unknown"),
        "url": head_commit.get("url", ""),
    }


_PROJECT_KEY_RE = re.compile(r"^[a-zA-Z0-9_-]+$")


def _validate_project_key(key):
    """Validate project key format to prevent path traversal."""
    if not key or len(key) > 100 or not _PROJECT_KEY_RE.match(key):
        return None
    return key


def _deploy_in_background(project, commit_info, lock):
    """Run deployment in a background thread (lock already acquired by caller)."""
    name = project["name"]

    def _run():
        try:
            result = run_deploy(project, commit_info)
            _deploy_status[name] = {
                "last_deploy": datetime.now(timezone.utc).isoformat(),
                "success": result["success"],
                "duration": result["duration"],
            }
        except Exception as e:
            logger.error("Deploy thread error for %s: %s", name, e)
            _deploy_status[name] = {
                "last_deploy": datetime.now(timezone.utc).isoformat(),
                "success": False,
                "error": str(e),
            }
        finally:
            lock.release()

    thread = threading.Thread(target=_run, name=f"deploy-{name}", daemon=True)
    thread.start()


# --- Routes ---


@app.route("/deploy", methods=["POST"])
def webhook_deploy():
    """GitHub webhook endpoint."""
    payload = request.get_json(silent=True)
    if not payload:
        return jsonify({"error": "Invalid payload"}), 400

    repo_full_name = payload.get("repository", {}).get("full_name", "")
    if not repo_full_name:
        return jsonify({"error": "Missing repository.full_name"}), 400

    project = get_project(repo_full_name)
    if not project:
        return jsonify({"error": f"Unknown project: {repo_full_name}"}), 404

    # Verify HMAC signature
    secret = project.get("webhook_secret") or os.environ.get("GITHUB_WEBHOOK_SECRET", "")
    if not secret:
        return jsonify({"error": "No webhook secret configured"}), 401

    signature = request.headers.get("X-Hub-Signature-256", "")
    if not verify_signature(request.get_data(), signature, secret):
        return jsonify({"error": "Invalid signature"}), 401

    # Branch check
    ref = payload.get("ref", "")
    expected_branch = project.get("branch", "main")
    push_branch = ref.replace("refs/heads/", "", 1) if ref.startswith("refs/heads/") else ref
    if push_branch != expected_branch:
        return jsonify({
            "status": "skipped",
            "reason": f"Branch mismatch: got {push_branch}, expected {expected_branch}",
        }), 200

    # Concurrency check (atomic acquire to avoid race condition)
    lock = _get_lock(project["name"])
    if not lock.acquire(blocking=False):
        return jsonify({
            "error": "Deploy already in progress",
            "project": project["name"],
        }), 409

    commit_info = _extract_commit_info(payload)
    _deploy_in_background(project, commit_info, lock)

    return jsonify({
        "status": "accepted",
        "project": project["name"],
    }), 202


@app.route("/deploy/<project_key>", methods=["POST"])
def manual_deploy(project_key):
    """Manual deploy trigger (requires Bearer token)."""
    if not _validate_project_key(project_key):
        return jsonify({"error": "Invalid project key"}), 400

    token = os.environ.get("DEPLOY_TOKEN", "")
    auth = request.headers.get("Authorization", "")
    if not verify_bearer_token(auth, token):
        return jsonify({"error": "Unauthorized"}), 401

    project = find_project_by_key(project_key)
    if not project:
        return jsonify({"error": f"Unknown project: {project_key}"}), 404

    lock = _get_lock(project["name"])
    if not lock.acquire(blocking=False):
        return jsonify({
            "error": "Deploy already in progress",
            "project": project["name"],
        }), 409

    _deploy_in_background(project, None, lock)

    return jsonify({
        "status": "accepted",
        "project": project["name"],
    }), 202


@app.route("/health", methods=["GET"])
def health():
    """Server health check."""
    uptime_seconds = (datetime.now(timezone.utc) - _start_time).total_seconds()
    return jsonify({
        "status": "ok",
        "uptime": _format_uptime(uptime_seconds),
        "uptime_seconds": round(uptime_seconds),
        "projects": len(get_all_projects()),
    })


@app.route("/status", methods=["GET"])
def status():
    """Status overview of all projects."""
    projects = []
    for p in get_all_projects():
        name = p["name"]
        lock = _get_lock(name)
        deploy_info = _deploy_status.get(name, {})
        projects.append({
            "name": name,
            "repo": p.get("repo", ""),
            "branch": p.get("branch", "main"),
            "deploy_mode": p.get("deploy_mode", ""),
            "deploying": lock.locked(),
            **deploy_info,
        })
    return jsonify({"projects": projects})


@app.route("/logs/<project_key>", methods=["GET"])
def logs(project_key):
    """Return last 50 lines of a project's deploy log."""
    if not _validate_project_key(project_key):
        return jsonify({"error": "Invalid project key"}), 400

    token = os.environ.get("DEPLOY_TOKEN", "")
    auth = request.headers.get("Authorization", "")
    if not verify_bearer_token(auth, token):
        return jsonify({"error": "Unauthorized"}), 401

    project = find_project_by_key(project_key)
    if not project:
        return jsonify({"error": f"Unknown project: {project_key}"}), 404

    log_dir = os.environ.get("LOG_DIR", "./logs")
    log_file = os.path.join(log_dir, f"{project_key}.log")

    # Symlink protection: ensure resolved path stays within log_dir
    log_file_real = os.path.realpath(log_file)
    log_dir_real = os.path.realpath(log_dir)
    if not log_file_real.startswith(log_dir_real + os.sep):
        return jsonify({"error": "Invalid log path"}), 400

    if not os.path.isfile(log_file_real):
        return jsonify({"project": project_key, "lines": []})

    with open(log_file_real, "r") as f:
        all_lines = f.readlines()

    tail = all_lines[-50:]
    return jsonify({
        "project": project_key,
        "lines": [line.rstrip("\n") for line in tail],
    })


@app.route("/config", methods=["GET"])
def config_endpoint():
    """Return current config with secrets masked."""
    cfg = get_config()
    return jsonify(mask_secrets({
        "defaults": cfg["defaults"],
        "projects": cfg["projects"],
    }))


@app.route("/reload", methods=["POST"])
def reload_config():
    """Hot-reload the projects config file."""
    token = os.environ.get("DEPLOY_TOKEN", "")
    auth = request.headers.get("Authorization", "")
    if not verify_bearer_token(auth, token):
        return jsonify({"error": "Unauthorized"}), 401

    try:
        load_config()
        _sync_locks()
        return jsonify({
            "status": "reloaded",
            "projects": len(get_all_projects()),
        })
    except Exception as e:
        logger.error("Config reload failed: %s", e)
        return jsonify({"error": str(e)}), 500


def _format_uptime(seconds):
    days = int(seconds // 86400)
    hours = int((seconds % 86400) // 3600)
    minutes = int((seconds % 3600) // 60)
    parts = []
    if days:
        parts.append(f"{days}d")
    if hours:
        parts.append(f"{hours}h")
    parts.append(f"{minutes}m")
    return " ".join(parts)


def _sighup_handler(signum, frame):
    """Reload config on SIGHUP."""
    logger.info("Received SIGHUP, reloading config...")
    try:
        load_config()
        _sync_locks()
        logger.info("Config reloaded via SIGHUP")
    except Exception as e:
        logger.error("SIGHUP config reload failed: %s", e)


if __name__ == "__main__":
    load_config()

    signal.signal(signal.SIGHUP, _sighup_handler)

    host = os.environ.get("FLASK_HOST", "0.0.0.0")
    port = int(os.environ.get("FLASK_PORT", "5000"))
    debug = os.environ.get("FLASK_DEBUG", "false").lower() == "true"

    logger.info("Starting pi-deployer on %s:%d", host, port)
    app.run(host=host, port=port, debug=debug)
