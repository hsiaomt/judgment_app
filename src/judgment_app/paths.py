"""Keep writable files outside the one-file executable's extraction directory."""

import os
import sys
from datetime import date
from pathlib import Path

from dotenv import dotenv_values, load_dotenv


def default_output_dir() -> Path:
    load_settings() 
    return resolve_output_path(os.environ.get("JUDGMENTS_DIR") or "data/judgments")


def jid_json_output_dir() -> Path:
    load_settings() 
    return resolve_output_path(os.environ.get("JID_JSON_DIR") or "data/judgments")


def load_settings() -> None:
    load_dotenv(config_file())
    cache = dotenv_values(user_data_dir() / "token.env")
    today = date.today().isoformat()
    if os.environ.get("API_TOKEN_DATE") != today and cache.get("API_TOKEN_DATE") == today and cache.get("API_TOKEN"):
        os.environ["API_TOKEN"] = cache["API_TOKEN"]
        os.environ["API_TOKEN_DATE"] = today


def resolve_output_path(value: str | Path) -> Path:
    path = Path(value).expanduser()
    return path if path.is_absolute() else user_data_dir() / path


def config_file() -> Path:
    portable = application_dir() / ".env"
    return portable if portable.is_file() else user_data_dir() / ".env"


def user_data_dir() -> Path:
    if getattr(sys, "frozen", False):
        return Path(os.environ.get("LOCALAPPDATA") or Path.home() / "AppData" / "Local") / "JudgmentApp"
    return application_dir()


def application_dir() -> Path:
    if getattr(sys, "frozen", False):
        return Path(sys.executable).resolve().parent
    return Path(__file__).resolve().parents[2]
