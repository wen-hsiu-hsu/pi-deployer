"""Configuration loading, merging, and hot-reload for pi-deployer."""

import copy
import logging
import os

import yaml

logger = logging.getLogger("pi-deployer")

_config = {
    "defaults": {},
    "projects": [],
    "_projects_by_repo": {},
    "_projects_by_key": {},
}


MAX_CONFIG_SIZE = 1 * 1024 * 1024  # 1 MB


def load_config(path=None):
    """Load projects.yml and merge defaults into each project."""
    config_path = path or os.environ.get("PROJECTS_CONFIG", "./projects.yml")

    file_size = os.path.getsize(config_path)
    if file_size > MAX_CONFIG_SIZE:
        raise ValueError(f"Config file too large: {file_size} bytes (max {MAX_CONFIG_SIZE})")

    with open(config_path, "r") as f:
        raw = yaml.safe_load(f)

    defaults = raw.get("defaults", {})
    projects = raw.get("projects", [])

    merged_projects = []
    by_repo = {}
    by_key = {}

    for project in projects:
        merged = _merge_defaults(defaults, project)
        merged_projects.append(merged)
        repo = merged.get("repo", "")
        by_repo[repo] = merged
        by_key[merged["name"]] = merged

    _config["defaults"] = defaults
    _config["projects"] = merged_projects
    _config["_projects_by_repo"] = by_repo
    _config["_projects_by_key"] = by_key

    logger.info("Loaded %d projects from %s", len(merged_projects), config_path)
    return _config


def _merge_defaults(defaults, project):
    """Deep-merge defaults into a project config (project values take precedence)."""
    result = copy.deepcopy(defaults)
    for key, value in project.items():
        if isinstance(value, dict) and isinstance(result.get(key), dict):
            result[key] = {**result[key], **value}
        else:
            result[key] = value
    return result


def get_config():
    """Return the current config dict."""
    return _config


def get_project(full_name):
    """Find a project by its full repo name (owner/repo)."""
    return _config["_projects_by_repo"].get(full_name)


def find_project_by_key(key):
    """Find a project by its short name (for manual trigger)."""
    return _config["_projects_by_key"].get(key)


def get_all_projects():
    """Return all merged project configs."""
    return _config["projects"]


def mask_secrets(config_dict):
    """Return a copy of config with sensitive fields masked."""
    secret_keys = {"webhook_secret", "secret", "token", "password"}
    return _recursive_mask(copy.deepcopy(config_dict), secret_keys)


def _recursive_mask(obj, secret_keys):
    if isinstance(obj, dict):
        return {
            k: "***" if k in secret_keys and v else _recursive_mask(v, secret_keys)
            for k, v in obj.items()
        }
    if isinstance(obj, list):
        return [_recursive_mask(item, secret_keys) for item in obj]
    return obj
