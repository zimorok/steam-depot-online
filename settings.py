import os
import json
from typing import Any, Dict

class SettingsManager:
    """Manages application settings, including loading from and saving to a config file."""

    def __init__(self, config_file: str = "settings.json"):
        self.config_file = config_file
        self._settings: Dict[str, Any] = {}
        self._load_settings()

    def _load_settings(self) -> None:
        self._settings = {
            "window_geometry": "1320x750",
            "appearance_mode": "dark",
            "color_theme": "blue",
            "download_path": os.path.join(os.getcwd(), "Games"),
            "strict_validation": True,
            "selected_repos": {},
            "app_update_check_on_startup": True,
            "language": "en",
            "github_api_token": "",
            "use_github_api_token": False,
        }
        if os.path.exists(self.config_file):
            try:
                with open(self.config_file, "r", encoding="utf-8") as f:
                    loaded_settings = json.load(f)
                    self._settings.update(loaded_settings)
            except (json.JSONDecodeError, IOError):
                pass

        configured_path = self._settings.get("download_path")
        if configured_path and not os.path.exists(configured_path):
            try:
                os.makedirs(configured_path, exist_ok=True)
            except OSError:
                self._settings["download_path"] = os.path.join(os.getcwd(), "Games")
                os.makedirs(self._settings["download_path"], exist_ok=True)

    def save_settings(self) -> None:
        try:
            with open(self.config_file, "w", encoding="utf-8") as f:
                json.dump(self._settings, f, indent=4)
        except IOError:
            pass

    def get(self, key: str, default: Any = None) -> Any:
        return self._settings.get(key, default)

    def set(self, key: str, value: Any) -> None:
        self._settings[key] = value