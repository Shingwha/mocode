"""URL parsing and GitHub API utilities for plugin installation."""

import json
import re
import shutil
import subprocess
import tempfile
import urllib.error
import urllib.request
import zipfile
from dataclasses import dataclass
from pathlib import Path
from typing import Any


@dataclass
class RepoInfo:
    """Parsed GitHub repository information."""
    owner: str
    repo: str
    branch: str = "main"
    subpath: str | None = None  # For deep linking to subdirectory

    @property
    def url(self) -> str:
        """Full GitHub URL."""
        return f"https://github.com/{self.owner}/{self.repo}"

    @property
    def clone_url(self) -> str:
        """Git clone URL."""
        return f"https://github.com/{self.owner}/{self.repo}.git"

    @property
    def zip_url(self) -> str:
        """ZIP archive download URL."""
        return f"https://github.com/{self.owner}/{self.repo}/archive/refs/heads/{self.branch}.zip"

    def raw_url(self, path: str) -> str:
        """Raw file URL."""
        return f"https://raw.githubusercontent.com/{self.owner}/{self.repo}/{self.branch}/{path}"


class GitHubApiError(Exception):
    """GitHub API error."""
    pass


class NetworkError(Exception):
    """Network-related error."""
    pass


class GitNotAvailableError(Exception):
    """Git command not available."""
    pass


# GitHub URL patterns
GITHUB_PATTERNS = [
    # https://github.com/owner/repo/tree/branch/path
    (r'^https?://github\.com/(?P<owner>[\w-]+)/(?P<repo>[\w.-]+)/tree/(?P<branch>[\w.-]+)/(?P<subpath>.+)$',
     lambda m: (m.group('owner'), m.group('repo'), m.group('branch'), m.group('subpath'))),
    # https://github.com/owner/repo/tree/branch
    (r'^https?://github\.com/(?P<owner>[\w-]+)/(?P<repo>[\w.-]+)/tree/(?P<branch>[\w.-]+)$',
     lambda m: (m.group('owner'), m.group('repo'), m.group('branch'), None)),
    # https://github.com/owner/repo
    (r'^https?://github\.com/(?P<owner>[\w-]+)/(?P<repo>[\w.-]+)/?$',
     lambda m: (m.group('owner'), m.group('repo'), None, None)),
    # owner/repo format
    (r'^(?P<owner>[\w-]+)/(?P<repo>[\w.-]+)$',
     lambda m: (m.group('owner'), m.group('repo'), None, None)),
]


def parse_github_url(url: str) -> RepoInfo:
    """Parse GitHub URL into structured information.

    Supports:
    - https://github.com/owner/repo
    - https://github.com/owner/repo/tree/branch
    - https://github.com/owner/repo/tree/branch/path/to/plugin
    - owner/repo (shorthand)

    Args:
        url: GitHub URL or owner/repo shorthand

    Returns:
        RepoInfo with parsed components

    Raises:
        ValueError: If URL format is not recognized
    """
    url = url.strip()

    for pattern, extractor in GITHUB_PATTERNS:
        match = re.match(pattern, url, re.IGNORECASE)
        if match:
            owner, repo, branch, subpath = extractor(match)
            return RepoInfo(
                owner=owner,
                repo=repo,
                branch=branch or "main",
                subpath=subpath,
            )

    raise ValueError(
        f"Invalid GitHub URL format: {url}\n"
        "Expected: owner/repo or https://github.com/owner/repo"
    )


def fetch_json(url: str, timeout: int = 30) -> dict | list:
    """Fetch JSON from URL.

    Args:
        url: URL to fetch
        timeout: Request timeout in seconds

    Returns:
        Parsed JSON data

    Raises:
        NetworkError: If request fails
        GitHubApiError: If API returns error
    """
    try:
        req = urllib.request.Request(
            url,
            headers={"Accept": "application/json", "User-Agent": "mocode"}
        )
        with urllib.request.urlopen(req, timeout=timeout) as response:
            return json.loads(response.read().decode("utf-8"))
    except urllib.error.HTTPError as e:
        if e.code == 404:
            raise GitHubApiError(f"Not found: {url}")
        elif e.code == 403:
            raise GitHubApiError(f"Rate limited or forbidden: {url}")
        else:
            raise NetworkError(f"HTTP error {e.code}: {url}")
    except urllib.error.URLError as e:
        raise NetworkError(f"Network error: {e.reason}")
    except json.JSONDecodeError as e:
        raise NetworkError(f"Invalid JSON: {e}")


def fetch_repo_info(owner: str, repo: str) -> dict:
    """Fetch repository info from GitHub API.

    Args:
        owner: Repository owner
        repo: Repository name

    Returns:
        Repository info dict with default_branch, etc.
    """
    url = f"https://api.github.com/repos/{owner}/{repo}"
    return fetch_json(url)


def fetch_repo_tree(owner: str, repo: str, branch: str = "main") -> list[dict]:
    """Fetch repository directory tree via GitHub API.

    Uses the Git Trees API to get all files in the repository.

    Args:
        owner: Repository owner
        repo: Repository name
        branch: Branch name

    Returns:
        List of file/directory entries with 'path', 'type' keys

    Raises:
        GitHubApiError: If API request fails
        NetworkError: If network error occurs
    """
    url = f"https://api.github.com/repos/{owner}/{repo}/git/trees/{branch}?recursive=1"
    data = fetch_json(url)

    if isinstance(data, dict) and "tree" in data:
        return data["tree"]
    return []


def fetch_raw_file(owner: str, repo: str, path: str, branch: str = "main") -> str | None:
    """Fetch raw file content from repository.

    Args:
        owner: Repository owner
        repo: Repository name
        path: File path in repository
        branch: Branch name

    Returns:
        File content as string, or None if not found
    """
    url = f"https://raw.githubusercontent.com/{owner}/{repo}/{branch}/{path}"
    try:
        req = urllib.request.Request(url, headers={"User-Agent": "mocode"})
        with urllib.request.urlopen(req, timeout=30) as response:
            return response.read().decode("utf-8")
    except urllib.error.HTTPError:
        return None
    except urllib.error.URLError:
        return None


def download_repo_zip(owner: str, repo: str, branch: str = "main", timeout: int = 60) -> bytes:
    """Download repository as ZIP archive.

    Args:
        owner: Repository owner
        repo: Repository name
        branch: Branch name
        timeout: Request timeout in seconds

    Returns:
        ZIP file content as bytes

    Raises:
        NetworkError: If download fails
    """
    url = f"https://github.com/{owner}/{repo}/archive/refs/heads/{branch}.zip"
    try:
        req = urllib.request.Request(url, headers={"User-Agent": "mocode"})
        with urllib.request.urlopen(req, timeout=timeout) as response:
            return response.read()
    except urllib.error.HTTPError as e:
        if e.code == 404:
            raise NetworkError(f"Repository or branch not found: {owner}/{repo}@{branch}")
        raise NetworkError(f"HTTP error {e.code}")
    except urllib.error.URLError as e:
        raise NetworkError(f"Network error: {e.reason}")


def extract_zip_to_dir(zip_data: bytes, target_dir: Path, strip_prefix: str | None = None) -> list[str]:
    """Extract ZIP archive to directory.

    Args:
        zip_data: ZIP file content
        target_dir: Target directory for extraction
        strip_prefix: Optional prefix to strip from paths (e.g., "repo-branch/")

    Returns:
        List of extracted file paths (relative to target_dir)
    """
    target_dir.mkdir(parents=True, exist_ok=True)
    extracted_files = []

    with tempfile.NamedTemporaryFile(suffix=".zip", delete=False) as tmp:
        tmp.write(zip_data)
        tmp_path = Path(tmp.name)

    try:
        with zipfile.ZipFile(tmp_path, "r") as zf:
            for info in zf.infolist():
                if info.is_dir():
                    continue

                path = info.filename

                # Strip prefix if provided
                if strip_prefix and path.startswith(strip_prefix):
                    path = path[len(strip_prefix):]
                    if not path:
                        continue

                # Security: prevent path traversal
                if path.startswith("/") or ".." in path:
                    continue

                target_path = target_dir / path
                target_path.parent.mkdir(parents=True, exist_ok=True)

                with zf.open(info) as src, open(target_path, "wb") as dst:
                    dst.write(src.read())

                extracted_files.append(path)
    finally:
        tmp_path.unlink()

    return extracted_files


def check_git_available() -> bool:
    """Check if git command is available.

    Returns:
        True if git is available
    """
    return shutil.which("git") is not None


def run_git_clone(
    repo_info: RepoInfo,
    target_dir: Path,
    shallow: bool = True,
    timeout: int = 120,
) -> tuple[bool, str]:
    """Run git clone command.

    Args:
        repo_info: Repository information
        target_dir: Target directory for clone
        shallow: Whether to do shallow clone (--depth 1)
        timeout: Command timeout in seconds

    Returns:
        Tuple of (success, output/error message)
    """
    if not check_git_available():
        return False, "Git command not found"

    cmd = ["git", "clone"]
    if shallow:
        cmd.extend(["--depth", "1"])
    if repo_info.branch:
        cmd.extend(["--branch", repo_info.branch])
    cmd.extend([repo_info.clone_url, str(target_dir)])

    try:
        result = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            timeout=timeout,
        )
        if result.returncode == 0:
            return True, result.stdout
        else:
            return False, result.stderr or "Git clone failed"
    except subprocess.TimeoutExpired:
        return False, "Git clone timed out"
    except Exception as e:
        return False, str(e)


def get_default_branch(owner: str, repo: str) -> str:
    """Get default branch for a repository.

    Args:
        owner: Repository owner
        repo: Repository name

    Returns:
        Default branch name (e.g., "main" or "master")
    """
    try:
        info = fetch_repo_info(owner, repo)
        return info.get("default_branch", "main")
    except (GitHubApiError, NetworkError):
        # Fallback: try common branches
        return "main"
