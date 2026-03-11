# engine.py
import asyncio
import aiohttp
import aiofiles
import os
import vdf
import json
import zipfile
import threading
import re
from typing import List, Tuple, Optional, Dict, Any, Callable

from settings import SettingsManager
from localization import LocalizationManager
from constants import APP_VERSION, GITHUB_RELEASES_API
from utils import PIL_AVAILABLE   # if needed for image downloads

MAX_CONCURRENT_DOWNLOADS = 10   # maximum simultaneous file downloads


class SDOEngine:
    """Handles all non-UI operations: repositories, Steam API, downloads, etc."""

    def __init__(self, settings_manager: SettingsManager, localization_manager: LocalizationManager):
        self.settings = settings_manager
        self.loc = localization_manager
        self.progress_callback: Optional[Callable[[str, str, Optional[Tuple[str, ...]]], None]] = None

        self.repos: Dict[str, str] = self.load_repositories()
        saved_selected_repos = self.settings.get("selected_repos", {})
        self.selected_repos: Dict[str, bool] = {
            repo: saved_selected_repos.get(repo, (repo_type == "Branch"))
            for repo, repo_type in self.repos.items()
        }

        self.steam_app_list: List[Dict[str, Any]] = []
        self.app_list_loaded_event = threading.Event()
        self.cancel_search = False

        # Cache file for Steam app list
        self.steam_cache_file = "steam_app_cache.json"

    # ----- Progress reporting -----
    def _progress(self, message: str, color: str = "default", tags: Optional[Tuple[str, ...]] = None):
        if self.progress_callback:
            self.progress_callback(message, color, tags)

    # ----- GitHub headers -----
    def _get_github_headers(self) -> Optional[Dict[str, str]]:
        if self.settings.get("use_github_api_token"):
            token = self.settings.get("github_api_token")
            if token:
                return {
                    "Authorization": f"token {token}",
                    "Accept": "application/vnd.github.v3+json",
                }
        return None

    # ----- Repository handling -----
    def load_repositories(self, filepath: Optional[str] = None) -> Dict[str, str]:
        path = filepath if filepath else "repositories.json"
        if os.path.exists(path):
            try:
                with open(path, "r", encoding="utf-8") as f:
                    repos = json.load(f)
                    cleaned_repos = {
                        k: v
                        for k, v in repos.items()
                        if isinstance(k, str) and isinstance(v, str)
                    }
                    return cleaned_repos
            except (json.JSONDecodeError, IOError) as e:
                self._progress(f"Failed to load repositories.json: {e}. Using empty list.", "red")
                return {}
        return {}

    def save_repositories(self, filepath: Optional[str] = None) -> None:
        path = filepath if filepath else "repositories.json"
        try:
            with open(path, "w", encoding="utf-8") as f:
                json.dump(self.repos, f, indent=4)
        except IOError as e:
            self._progress(f"Failed to save repositories.json: {e}", "red")
        self.settings.set("selected_repos", self.selected_repos)
        self.settings.save_settings()

    # ----- Steam app list loading (with cache) -----
    async def async_load_steam_app_list(self):
        # Try to load from cache first
        self._progress("Populating Steam App list (Please wait...)", "green")
        if os.path.exists(self.steam_cache_file):
            try:
                async with aiofiles.open(self.steam_cache_file, "r", encoding="utf-8") as f:
                    data = json.loads(await f.read())
                
                # Try to parse as dgibbs64 format first
                if isinstance(data, dict) and "applist" in data and isinstance(data["applist"], dict):
                    apps = data["applist"].get("apps", [])
                    if apps and isinstance(apps, list):
                        self.steam_app_list = apps
                        self.app_list_loaded_event.set()
                        self._progress("Steam app list loaded from cache (dgibbs64 format).", "green")
                        return
                
                # Try to parse as direct array format (jsnli style)
                if isinstance(data, list):
                    # Ensure each item has appid and name, convert appid to string
                    self.steam_app_list = [
                        {"appid": str(item["appid"]), "name": item["name"]}
                        for item in data if isinstance(item, dict) and "appid" in item and "name" in item
                    ]
                    if self.steam_app_list:
                        self.app_list_loaded_event.set()
                        self._progress("Steam app list loaded from cache (array format).", "green")
                        return
                
                self._progress("Cache file has unrecognised format, will re-download.", "yellow")
            except Exception as e:
                self._progress(f"Failed to load cache: {e}", "yellow")

        # Cache missing or invalid – attempt download
        urls = [
            "https://raw.githubusercontent.com/jsnli/steamappidlist/refs/heads/master/data/games_appid.json",
            #"https://raw.githubusercontent.com/dgibbs64/SteamCMD-AppID-List/main/steamcmd_appid.json"
        ]
        timeout = aiohttp.ClientTimeout(total=60)
        headers = {
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
            "Accept": "application/json, text/plain, */*",
            "Accept-Language": "en-US,en;q=0.9",
            "Connection": "keep-alive",
        }

        for url in urls:
            for attempt in range(2):
                try:
                    async with aiohttp.ClientSession() as session:
                        async with session.get(url, headers=headers, timeout=timeout, ssl=True) as response:
                            if response.status == 200:
                                data = await response.json(content_type=None)
                                # Try dgibbs64 format first
                                if isinstance(data, dict) and "applist" in data and isinstance(data["applist"], dict):
                                    apps = data["applist"].get("apps", [])
                                    if apps and isinstance(apps, list):
                                        self.steam_app_list = apps
                                        self._progress("Steam app list downloaded (dgibbs64 format).", "green")
                                    else:
                                        self._progress("Downloaded data has no apps list, trying array format...", "yellow")
                                        self.steam_app_list = []
                                # Try array format (jsnli)
                                elif isinstance(data, list):
                                    self.steam_app_list = [
                                        {"appid": str(item["appid"]), "name": item["name"]}
                                        for item in data if isinstance(item, dict) and "appid" in item and "name" in item
                                    ]
                                    if self.steam_app_list:
                                        self._progress("Steam app list downloaded (array format).", "green")
                                    else:
                                        self._progress("Downloaded array contains no valid entries.", "yellow")
                                        self.steam_app_list = []
                                else:
                                    self._progress(f"Downloaded data has unrecognized format: {type(data).__name__}", "red")
                                    self.steam_app_list = []

                                if self.steam_app_list:
                                    self.app_list_loaded_event.set()
                                    # Save the original data to cache
                                    try:
                                        async with aiofiles.open(self.steam_cache_file, "w", encoding="utf-8") as f:
                                            await f.write(json.dumps(data, indent=2))
                                    except Exception as e:
                                        self._progress(f"Failed to save cache: {e}", "yellow")
                                    return
                                else:
                                    self._progress("Downloaded data contained no valid app entries, trying next URL.", "yellow")
                            else:
                                self._progress(f"Failed with status {response.status} from {url.split('/')[2]}", "yellow")
                except (aiohttp.ClientError, asyncio.TimeoutError) as e:
                    self._progress(f"Attempt {attempt+1} with {url.split('/')[2]} failed: {type(e).__name__}", "yellow")
                    await asyncio.sleep(1)
                except Exception as e:
                    self._progress(f"Unexpected error with {url.split('/')[2]}: {e}", "red")
                    break

        # All download attempts failed – try cache again as last resort
        if os.path.exists(self.steam_cache_file):
            try:
                async with aiofiles.open(self.steam_cache_file, "r", encoding="utf-8") as f:
                    data = json.loads(await f.read())
                self.steam_app_list = data.get("applist", {}).get("apps", [])
                if self.steam_app_list:
                    self.app_list_loaded_event.set()
                    self._progress("Download failed. Using possibly outdated cache.", "yellow")
                    return
            except Exception as e:
                self._progress(f"Cache fallback also failed: {e}", "red")

        self._progress("All sources failed. Search by name may not work (but you can still search by AppID).", "red")

    # ----- Search -----
    async def async_search_game(self, user_input: str) -> List[Dict[str, Any]]:
        """Return list of dicts with keys: appid, name, capsule_image (bytes or None)."""
        games_found: List[Dict[str, Any]] = []
        max_results = 200

        if user_input.isdigit():
            appid_to_search = user_input
            url = f"https://store.steampowered.com/api/appdetails?appids={appid_to_search}&l=english"
            try:
                async with aiohttp.ClientSession() as session:
                    async with session.get(url, timeout=aiohttp.ClientTimeout(total=15)) as response:
                        if response.status == 200:
                            response_data = await response.json()
                            if response_data.get(appid_to_search, {}).get("success"):
                                game_data = response_data[appid_to_search]["data"]
                                game_name = game_data.get("name", f"AppID {appid_to_search}")
                                games_found.append({"appid": appid_to_search, "name": game_name})
                            else:
                                self._progress(f"No game found for AppID {appid_to_search}.", "red")
                        else:
                            self._progress(f"Failed to fetch details for AppID {appid_to_search} (Status: {response.status}).", "red")
            except (aiohttp.ClientError, asyncio.TimeoutError) as e:
                self._progress(f"Error fetching AppID {appid_to_search}: {e}", "red")
            except json.JSONDecodeError:
                self._progress(f"Failed to decode JSON for AppID {appid_to_search}.", "red")
        else:
            if not self.app_list_loaded_event.is_set():
                self._progress("Steam app list not loaded. Please wait or try AppID.", "yellow")
                return []

            search_term_lower = user_input.lower()
            for app_info in self.steam_app_list:
                if self.cancel_search:
                    self._progress("Name search cancelled.", "yellow")
                    return []
                if search_term_lower in app_info.get("name", "").lower():
                    games_found.append({"appid": str(app_info["appid"]), "name": app_info["name"]})
                    if len(games_found) >= max_results:
                        self._progress(f"Max results ({max_results}) reached. Refine search.", "yellow")
                        break

        if self.cancel_search:
            self._progress("Search cancelled.", "yellow")
            return []

        if not games_found:
            self._progress("No matching games found.", "red")
            return []

        # Download capsule images
        capsule_tasks = []
        for game in games_found:
            if PIL_AVAILABLE:
                capsule_url = f"https://cdn.akamai.steamstatic.com/steam/apps/{game['appid']}/capsule_231x87.jpg"
                capsule_tasks.append(self._download_image(capsule_url))
            else:
                capsule_tasks.append(asyncio.sleep(0, result=None))

        capsule_results = await asyncio.gather(*capsule_tasks, return_exceptions=True)

        for i, game in enumerate(games_found):
            img_data = capsule_results[i] if not isinstance(capsule_results[i], Exception) else None
            game["capsule_image"] = img_data

        return games_found

    async def _download_image(self, url: str) -> Optional[bytes]:
        if not PIL_AVAILABLE:
            return None
        try:
            async with aiohttp.ClientSession() as session:
                async with session.get(url, timeout=aiohttp.ClientTimeout(total=10)) as response:
                    if response.status == 200:
                        return await response.read()
                    elif response.status == 404:
                        return None
                    else:
                        self._progress(f"Failed to download image (Status {response.status}): {url}", "yellow")
                        return None
        except Exception as e:
            self._progress(f"Error downloading image {url}: {e}", "red")
            return None

    # ----- Game details -----
    async def async_fetch_game_details(self, appid: str) -> Dict[str, Any]:
        """Return dict with keys: name, logo, header, description, genres, release_date."""
        result = {
            "name": None,
            "logo": None,
            "header": None,
            "short_description": None,
            "genres": [],
            "release_date": None,
        }

        logo_url = f"https://cdn.akamai.steamstatic.com/steam/apps/{appid}/logo.png"
        header_url = f"https://cdn.akamai.steamstatic.com/steam/apps/{appid}/header.jpg"
        appdetails_url = f"https://store.steampowered.com/api/appdetails?appids={appid}&l=english"

        async with aiohttp.ClientSession() as session:
            tasks = []
            if PIL_AVAILABLE:
                tasks.append(self._download_image(logo_url))
                tasks.append(self._download_image(header_url))
            tasks.append(session.get(appdetails_url, timeout=aiohttp.ClientTimeout(total=20)))

            results = await asyncio.gather(*tasks, return_exceptions=True)

            idx = 0
            if PIL_AVAILABLE:
                logo_res = results[idx] if not isinstance(results[idx], Exception) else None
                result["logo"] = logo_res if isinstance(logo_res, bytes) else None
                idx += 1
                header_res = results[idx] if not isinstance(results[idx], Exception) else None
                result["header"] = header_res if isinstance(header_res, bytes) else None
                idx += 1

            api_response = results[idx]
            if not isinstance(api_response, Exception) and api_response.status == 200:
                try:
                    api_json = await api_response.json()
                    if api_json.get(appid, {}).get("success"):
                        data = api_json[appid]["data"]
                        result["name"] = data.get("name")
                        result["short_description"] = data.get("short_description")
                        result["genres"] = [g["description"] for g in data.get("genres", [])]
                        result["release_date"] = data.get("release_date", {}).get("date")
                except Exception as e:
                    self._progress(f"Error parsing details for AppID {appid}: {e}", "red")

        return result

    # ----- File download helpers -----
    async def get_file(self, sha: str, path: str, repo: str) -> Optional[bytes]:
        """Download a single file from a GitHub repo at a given commit SHA."""
        url_list: List[str] = [
            f"https://gcore.jsdelivr.net/gh/{repo}@{sha}/{path}",
            f"https://fastly.jsdelivr.net/gh/{repo}@{sha}/{path}",
            f"https://cdn.jsdelivr.net/gh/{repo}@{sha}/{path}",
            f"https://ghproxy.org/https://raw.githubusercontent.com/{repo}/{sha}/{path}",
            f"https://raw.dgithub.xyz/{repo}/{sha}/{path}",
            f"https://raw.githubusercontent.com/{repo}/{sha}/{path}",
        ]
        max_retries_per_url, overall_attempts = 1, 2
        github_auth_headers = self._get_github_headers()

        async with aiohttp.ClientSession() as session:
            for attempt in range(overall_attempts):
                if self.cancel_search:
                    break
                for url in url_list:
                    if self.cancel_search:
                        self._progress(f"Download cancelled for {path} from {url.split('/')[2]}", "yellow")
                        return None

                    headers = {}
                    if "raw.githubusercontent.com" in url and github_auth_headers:
                        headers = github_auth_headers.copy()
                        if "Accept" in headers and "json" in headers["Accept"]:
                            del headers["Accept"]

                    for retry in range(max_retries_per_url + 1):
                        if self.cancel_search:
                            return None
                        try:
                            self._progress(f"... Trying {url.split('/')[2]} for {os.path.basename(path)} (Attempt {retry+1})", "default")
                            async with session.get(url, headers=headers, ssl=False, timeout=aiohttp.ClientTimeout(total=20)) as r:
                                if r.status == 200:
                                    self._progress(f"OK from {url.split('/')[2]}", "green")
                                    return await r.read()
                                if r.status == 404:
                                    self._progress(f"404 from {url.split('/')[2]}", "yellow")
                                    break
                                self._progress(f"Status {r.status} from {url.split('/')[2]}", "yellow")
                        except (aiohttp.ClientError, asyncio.TimeoutError) as e_req:
                            self._progress(f"Error with {url.split('/')[2]}: {e_req}", "yellow")
                        except KeyboardInterrupt:
                            self._progress(f"Download interrupted for {path}", "yellow")
                            self.cancel_search = True
                            return None
                        if self.cancel_search:
                            return None
                        if retry < max_retries_per_url:
                            await asyncio.sleep(0.5)

                if self.cancel_search:
                    return None
                if attempt < overall_attempts - 1:
                    self._progress(f"Retrying download cycle for {path} (Cycle {attempt+2}/{overall_attempts})", "yellow")
                    await asyncio.sleep(1)

        if not self.cancel_search:
            self._progress(f"Maximum attempts exceeded for {path}. File could not be downloaded.", "red")
        return None

    async def process_manifest(
        self, sha: str, path: str, processing_dir: str, repo: str
    ) -> List[Tuple[str, str]]:
        """Download/use local file, extract keys from VDF, return list of (depot_id, key)."""
        collected_depots: List[Tuple[str, str]] = []
        file_save_path = os.path.join(processing_dir, path)
        parent_dir = os.path.dirname(file_save_path)
        if parent_dir and not os.path.exists(parent_dir):
            os.makedirs(parent_dir, exist_ok=True)

        content_bytes: Optional[bytes] = None
        should_download = True

        if os.path.exists(file_save_path):
            if path.lower().endswith(".manifest"):
                should_download = False
                self._progress(f"Manifest file {path} already exists. Using local version.", "default")
            elif path.lower().endswith((".vdf")):
                try:
                    async with aiofiles.open(file_save_path, "rb") as f_existing_bytes:
                        content_bytes = await f_existing_bytes.read()
                    should_download = False
                    self._progress(f"Key/Config VDF {path} already exists. Using local version for key extraction.", "default")
                except Exception as e_read:
                    self._progress(f"Could not read existing local file {path}: {e_read}. Attempting fresh download.", "yellow")
                    content_bytes = None
                    should_download = True

        if should_download and not self.cancel_search:
            self._progress(f"Downloading: {path} from repo {repo} (commit: {sha[:7]})", "default")
            content_bytes = await self.get_file(sha, path, repo)

        if self.cancel_search:
            return collected_depots

        if content_bytes:
            if should_download:
                async with aiofiles.open(file_save_path, "wb") as f_new:
                    await f_new.write(content_bytes)
                self._progress(f"File downloaded and saved: {path}", "green")

            if path.lower().endswith((".vdf")):
                try:
                    vdf_content_str = content_bytes.decode(encoding="utf-8", errors="ignore")
                    depots_config = vdf.loads(vdf_content_str)
                    depots_data = depots_config.get("depots", {})
                    if not isinstance(depots_data, dict):
                        depots_data = {}
                    new_keys_count = 0
                    for depot_id_str, depot_info in depots_data.items():
                        if isinstance(depot_info, dict) and "DecryptionKey" in depot_info:
                            key_tuple = (str(depot_id_str), depot_info["DecryptionKey"])
                            if key_tuple not in collected_depots:
                                collected_depots.append(key_tuple)
                                new_keys_count += 1
                    if new_keys_count > 0:
                        self._progress(f"Extracted {new_keys_count} new decryption keys from {path}", "magenta")
                    elif not depots_data and os.path.basename(path.lower()) in ["key.vdf", "config.vdf"]:
                        self._progress(f"Warning: No 'depots' section in {path}.", "yellow")
                except Exception as e_vdf:
                    self._progress(f"Failed to parse VDF content for {path}: {e_vdf}.", "red")
        elif should_download and not os.path.exists(file_save_path):
            self._progress(f"Failed to download or find local file: {path}", "red")

        return collected_depots

    async def _fetch_branch_zip(self, repo_full_name: str, app_id: str) -> Optional[bytes]:
        api_url = f"https://api.github.com/repos/{repo_full_name}/zipball/{app_id}"
        headers = self._get_github_headers() or {}
        self._progress(f"Attempting to download branch zip (API): {api_url}" + (" (with token)" if headers else " (no token)"), "default")
        async with aiohttp.ClientSession() as session:
            try:
                async with session.get(api_url, headers=headers, timeout=aiohttp.ClientTimeout(total=600)) as r:
                    if r.status == 200:
                        self._progress(f"Successfully started downloading branch zip for AppID {app_id} from {repo_full_name}.", "green")
                        content = await r.read()
                        self._progress(f"Finished downloading branch zip (Size: {len(content)/1024:.2f} KB).", "green")
                        return content
                    else:
                        error_msg = f"Failed to download branch zip (Status: {r.status}) from {api_url}"
                        if r.status == 401 and headers:
                            error_msg += " - Unauthorized. Check token."
                        elif r.status == 404:
                            error_msg += f" - Not Found. Ensure repo '{repo_full_name}' and branch '{app_id}' exist."
                        self._progress(error_msg, "red")
                        return None
            except (aiohttp.ClientError, asyncio.TimeoutError) as e:
                self._progress(f"Network/Timeout error downloading branch zip: {e}", "red")
                return None
            except Exception as e:
                self._progress(f"Unexpected error fetching branch zip: {e}", "red")
                return None

    # ----- Main download logic (with concurrency) -----
    async def perform_download(
        self, app_id_input: str, game_name: str, selected_repos: List[str]
    ) -> Tuple[List[Tuple[str, str]], Optional[str], bool]:
        """
        Returns (collected_depots, output_path_or_processing_dir, source_was_branch)
        """
        app_id_match = re.match(r"^\d+", app_id_input)
        if not app_id_match:
            self._progress(f"Invalid AppID format: {app_id_input}.", "red")
            return [], None, False
        app_id = app_id_match.group(0)
        sanitized_game_name = "".join(c if c.isalnum() or c in " -_" else "" for c in game_name).strip() or f"AppID_{app_id}"
        output_base_dir = self.settings.get("download_path")
        final_output_name_stem = f"{sanitized_game_name} - {app_id}"
        try:
            os.makedirs(output_base_dir, exist_ok=True)
        except OSError as e:
            self._progress(f"Error creating base output directory: {e}", "red")
            return [], None, False

        overall_collected_depots: List[Tuple[str, str]] = []
        github_auth_headers = self._get_github_headers()

        for repo_full_name in selected_repos:
            if self.cancel_search:
                self._progress(f"Download cancelled before processing repo {repo_full_name}.", "yellow")
                return overall_collected_depots, None, False

            repo_type = self.repos.get(repo_full_name)
            if not repo_type:
                self._progress(f"Repository {repo_full_name} type not found. Skipping.", "yellow")
                continue

            if repo_type == "Branch":
                self._progress(f"\nProcessing BRANCH repository: {repo_full_name} for AppID: {app_id}", "cyan")
                final_branch_zip_path = os.path.join(output_base_dir, f"{final_output_name_stem}.zip")
                if os.path.exists(final_branch_zip_path):
                    self._progress(f"Branch ZIP already exists: {final_branch_zip_path}. Skipping.", "blue")
                    return [], final_branch_zip_path, True

                zip_content = await self._fetch_branch_zip(repo_full_name, app_id)
                if self.cancel_search:
                    self._progress("Download cancelled during branch zip fetch.", "yellow")
                    return [], None, False
                if zip_content:
                    try:
                        async with aiofiles.open(final_branch_zip_path, "wb") as f_zip:
                            await f_zip.write(zip_content)
                        self._progress(f"Successfully saved branch download to {final_branch_zip_path}", "green")
                        return [], final_branch_zip_path, True
                    except Exception as e_save:
                        self._progress(f"Failed to save branch zip: {e_save}", "red")
                else:
                    self._progress(f"Failed to download branch zip for {repo_full_name}. Trying next repo.", "yellow")
                continue

            # Non-Branch (Encrypted/Decrypted)
            processing_dir = os.path.join(output_base_dir, f"_{final_output_name_stem}_temp")
            try:
                os.makedirs(processing_dir, exist_ok=True)
            except OSError as e_mkdir:
                self._progress(f"Error creating temp dir {processing_dir}: {e_mkdir}. Skipping repo.", "red")
                continue

            self._progress(f"\nSearching NON-BRANCH repository: {repo_full_name} for AppID: {app_id} (Type: {repo_type})", "cyan")
            branch_api_url = f"https://api.github.com/repos/{repo_full_name}/branches/{app_id}"

            async with aiohttp.ClientSession() as session:
                try:
                    headers = github_auth_headers.copy() if github_auth_headers else {}
                    async with session.get(branch_api_url, headers=headers, ssl=False, timeout=aiohttp.ClientTimeout(total=15)) as r_branch:
                        if r_branch.status != 200:
                            status_msg = f"AppID {app_id} not found as a branch in {repo_full_name} (Status: {r_branch.status})."
                            if r_branch.status == 401 and headers:
                                status_msg += " Auth failed. Check token."
                            self._progress(status_msg + " Trying next repo.", "yellow")
                            continue

                        branch_json = await r_branch.json()
                        commit_data = branch_json.get("commit", {})
                        sha = commit_data.get("sha")
                        tree_url_base = commit_data.get("commit", {}).get("tree", {}).get("url")
                        commit_date = commit_data.get("commit", {}).get("author", {}).get("date", "Unknown date")

                        if not sha or not tree_url_base:
                            self._progress(f"Invalid branch data for {repo_full_name}/{app_id}. Trying next repo.", "red")
                            continue

                        tree_url = f"{tree_url_base}?recursive=1"
                        async with session.get(tree_url, headers=headers, ssl=False, timeout=aiohttp.ClientTimeout(total=30)) as r_tree:
                            if r_tree.status != 200:
                                self._progress(f"Failed to get file tree for {repo_full_name}/{app_id} (Status: {r_tree.status}). Trying next.", "red")
                                continue
                            tree_json = await r_tree.json()
                            if tree_json.get("truncated"):
                                self._progress(f"Warning: File tree for {repo_full_name}/{app_id} is TRUNCATED. Some files may be missed.", "yellow")
                            tree_items = tree_json.get("tree", [])
                            if not tree_items:
                                self._progress(f"No files found in tree for {repo_full_name}/{app_id}. Trying next.", "yellow")
                                continue

                            strict = self.settings.get("strict_validation")

                            # Determine which files to process based on strict mode
                            items_to_process = []
                            for item in tree_items:
                                if item.get("type") != "blob":
                                    continue
                                path = item.get("path", "")
                                if strict:
                                    # Strict mode: only .manifest and key VDFs
                                    if path.lower().endswith(".manifest") or os.path.basename(path).lower() in ["key.vdf", "config.vdf"]:
                                        items_to_process.append(item)
                                else:
                                    # Non-strict: all files
                                    items_to_process.append(item)

                            if not items_to_process:
                                self._progress("No files to download in this branch.", "yellow")
                                continue

                            # Concurrent downloads with semaphore
                            semaphore = asyncio.Semaphore(MAX_CONCURRENT_DOWNLOADS)

                            async def process_one(item):
                                async with semaphore:
                                    return await self.process_manifest(sha, item["path"], processing_dir, repo_full_name)

                            tasks = [process_one(item) for item in items_to_process]
                            results = await asyncio.gather(*tasks, return_exceptions=True)

                            repo_specific_depots: List[Tuple[str, str]] = []
                            files_downloaded = False

                            for res in results:
                                if isinstance(res, Exception):
                                    self._progress(f"Error processing file: {res}", "red")
                                else:
                                    # res is a list of (depot_id, key)
                                    if res:
                                        repo_specific_depots.extend(res)
                                    files_downloaded = True  # at least one file was processed

                            if self.cancel_search:
                                self._progress(f"Download cancelled during processing of {repo_full_name}.", "yellow")
                                break

                            # Determine success
                            if strict:
                                success = bool(repo_specific_depots) and files_downloaded
                            else:
                                success = files_downloaded

                            if success:
                                self._progress(f"\nData successfully processed for AppID {app_id} from {repo_full_name}.", "green")
                                for dk in repo_specific_depots:
                                    if dk not in overall_collected_depots:
                                        overall_collected_depots.append(dk)
                                return overall_collected_depots, processing_dir, False
                            else:
                                self._progress(f"AppID {app_id} could not be processed from {repo_full_name} with current settings. Trying next.", "yellow")

                except (aiohttp.ClientError, asyncio.TimeoutError, json.JSONDecodeError) as e_api:
                    self._progress(f"Network/API error while processing {repo_full_name}: {e_api}. Trying next.", "red")
                except KeyboardInterrupt:
                    self._progress("Processing interrupted by user.", "yellow")
                    self.cancel_search = True
                    break
            if self.cancel_search:
                break

        self._progress(f"\nAppID {app_id} ({game_name}) could not be processed from any selected repository.", "red")
        return overall_collected_depots, None, False

    # ----- Lua generation -----
    def generate_lua(self, depot_info: List[Tuple[str, str]], appid: str, processing_dir: str) -> str:
        lua_lines = [f"addappid({appid})"]
        processed_depots = set()

        for depot_id, decryption_key in depot_info:
            lua_lines.append(f'addappid({depot_id},1,"{decryption_key}")')
            processed_depots.add(depot_id)

        if os.path.isdir(processing_dir):
            manifest_files = []
            for root, _, files in os.walk(processing_dir):
                for f in files:
                    if f.lower().endswith(".manifest"):
                        manifest_files.append(os.path.join(root, f))

            def sort_key(fp):
                fname = os.path.basename(fp)
                name = fname.rsplit(".manifest", 1)[0]
                parts = name.split("_", 1)
                depot = parts[0]
                gid = parts[1] if len(parts) > 1 else ""
                try:
                    depot_int = int(depot) if depot.isdigit() else 0
                except ValueError:
                    depot_int = 0
                return (depot_int, gid)

            manifest_files.sort(key=sort_key)

            for full_path in manifest_files:
                fname = os.path.basename(full_path)
                name = fname.rsplit(".manifest", 1)[0]
                parts = name.split("_", 1)
                depot = parts[0]
                gid = parts[1] if len(parts) > 1 else ""

                if depot.isdigit():
                    if depot not in processed_depots:
                        lua_lines.append(f"addappid({depot})")
                        processed_depots.add(depot)
                    if gid:
                        lua_lines.append(f'setManifestid({depot},"{gid}",0)')
                    else:
                        self._progress(f"Could not parse Manifest GID from {fname}. setManifestid skipped.", "yellow")
                else:
                    self._progress(f"Could not parse numeric DepotID from {fname}. Skipped.", "yellow")

        return "\n".join(lua_lines)

    # ----- Zipping -----
    def zip_outcome(self, processing_dir: str, selected_repos_for_zip: List[str]) -> Optional[str]:
        if not os.path.isdir(processing_dir):
            self._progress(f"Processing directory {processing_dir} not found. Skipping zip.", "red")
            return None

        is_encrypted = any(self.repos.get(r, "") == "Encrypted" for r in selected_repos_for_zip)
        strict = self.settings.get("strict_validation")
        exclude_keys_in_strict = ["key.vdf", "config.vdf"] if strict else []

        base_name = os.path.basename(os.path.normpath(processing_dir))
        if base_name.startswith("_") and base_name.endswith("_temp"):
            base_name = base_name[1:-5]
        final_zip_name = base_name + (" - encrypted.zip" if is_encrypted else ".zip")
        final_zip_path = os.path.join(os.path.dirname(processing_dir), final_zip_name)

        if os.path.exists(final_zip_path):
            try:
                os.remove(final_zip_path)
                self._progress(f"Removed existing zip: {final_zip_path}", "yellow")
            except OSError as e:
                self._progress(f"Error removing existing zip {final_zip_path}: {e}", "red")
                return None

        try:
            with zipfile.ZipFile(final_zip_path, "w", zipfile.ZIP_DEFLATED, compresslevel=6) as zipf:
                for root, _, files in os.walk(processing_dir):
                    for file in files:
                        if strict and file.lower() in exclude_keys_in_strict:
                            self._progress(f"Excluding '{file}' from zip (strict mode).", "yellow")
                            continue
                        full = os.path.join(root, file)
                        arcname = os.path.relpath(full, start=processing_dir)
                        zipf.write(full, arcname)
            self._progress(f"Successfully created outcome zip: {final_zip_path}", "cyan")

            import shutil
            shutil.rmtree(processing_dir)
            self._progress(f"Temporary folder {processing_dir} deleted.", "green")
            return final_zip_path
        except Exception as e:
            self._progress(f"Error creating zip: {e}", "red")
            return None

    # ----- Update check -----
    async def async_check_for_updates(self) -> Optional[Dict[str, str]]:
        """Return dict with 'latest_version', 'current_version', 'release_url' if newer, else None."""
        headers = self._get_github_headers() or {}
        try:
            async with aiohttp.ClientSession() as session:
                async with session.get(
                    GITHUB_RELEASES_API,
                    headers=headers,
                    timeout=aiohttp.ClientTimeout(total=10)
                ) as resp:
                    resp.raise_for_status()
                    data = await resp.json()
            latest_tag_raw = data.get("tag_name", "v0.0.0")
            latest_version = re.sub(r"[^0-9.]", "", latest_tag_raw).strip(".")
            release_url = data.get("html_url", "https://github.com/fairy-root/steam-depot-online/releases")

            def to_int_list(v):
                return list(map(int, v.split(".")))
            if to_int_list(latest_version) > to_int_list(APP_VERSION):
                return {
                    "latest_version": latest_version,
                    "current_version": APP_VERSION,
                    "release_url": release_url,
                }
        except Exception as e:
            self._progress(f"Update check failed: {e}", "red")
        return None

    # ----- Rate limit check -----
    async def async_check_rate_limit(self, use_token: bool) -> Optional[Tuple[int, int]]:
        """Return (remaining, limit) or None on failure."""
        headers = self._get_github_headers() if use_token else {}
        url = "https://api.github.com/rate_limit"
        try:
            async with aiohttp.ClientSession() as session:
                async with session.get(url, headers=headers, timeout=aiohttp.ClientTimeout(total=10)) as resp:
                    if resp.status == 200:
                        data = await resp.json()
                        core = data.get("resources", {}).get("core", {})
                        if not core and not use_token:
                            core = data.get("rate", {})
                        limit = core.get("limit")
                        remaining = core.get("remaining")
                        if limit is not None and remaining is not None:
                            return (remaining, limit)
                    else:
                        self._progress(f"Rate limit check failed (Status {resp.status})", "red")
        except Exception as e:
            self._progress(f"Rate limit check error: {e}", "red")
        return None