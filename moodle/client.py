from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path
from typing import Optional
from urllib.parse import urljoin, urlparse

import httpx
from bs4 import BeautifulSoup

from config import CACHE_DIR, MOODLE_BASE_URL


@dataclass
class MoodleFileHit:
    title: str
    url: str
    score: int
    ext: str


class MoodleClient:
    def __init__(self, base_url: str = MOODLE_BASE_URL, timeout: float = 30.0):
        self.base_url = base_url.rstrip("/")
        self.client = httpx.Client(timeout=timeout, follow_redirects=True, headers={
            "User-Agent": "ufaragent/1.0 (+study-bot)"
        })

    def close(self) -> None:
        self.client.close()

    def __enter__(self) -> "MoodleClient":
        return self

    def __exit__(self, *args) -> None:
        self.close()

    def login(self, username: str, password: str) -> tuple[bool, str]:
        login_url = f"{self.base_url}/login/index.php"
        try:
            r = self.client.get(login_url)
            r.raise_for_status()
        except httpx.HTTPError as e:
            return False, f"Could not reach Moodle: {e}"

        soup = BeautifulSoup(r.text, "html.parser")
        token_el = soup.select_one("input[name=logintoken]")
        token = token_el["value"] if token_el else ""

        try:
            r2 = self.client.post(
                login_url,
                data={
                    "username": username,
                    "password": password,
                    "logintoken": token,
                    "anchor": "",
                },
            )
            r2.raise_for_status()
        except httpx.HTTPError as e:
            return False, f"Moodle login failed: {e}"

        if "login/index.php" in str(r2.url) and "loginerrormessage" in r2.text.casefold():
            return False, "Invalid Moodle username or password."
        # Moodle often redirects to /my/ on success
        if "login/index.php" in str(r2.url):
            # Check for error box
            soup2 = BeautifulSoup(r2.text, "html.parser")
            err = soup2.select_one(".loginerrors, #loginerrormessage, .alert-danger")
            if err:
                return False, "Invalid Moodle username or password."
            # Still on login page
            if soup2.select_one("form#login"):
                return False, "Invalid Moodle username or password."
        return True, "ok"

    def _abs(self, href: str) -> str:
        return urljoin(self.base_url + "/", href)

    def _score_file(self, title: str, url: str) -> int:
        blob = f"{title} {url}".casefold()
        score = 0
        keywords = {
            "դասացուցակ": 50,
            "dasacucak": 40,
            "timetable": 45,
            "schedule": 40,
            "emploi du temps": 40,
            "խմբեր": 35,
            "groupes": 30,
            "groups": 25,
            "իկմ": 20,
            "ikm": 20,
            "ima": 15,
            "informatique": 10,
        }
        for k, v in keywords.items():
            if k in blob:
                score += v
        if blob.endswith(".pdf") or ".pdf" in blob:
            score += 5
        if blob.endswith(".xlsx") or ".xlsx" in blob or "spreadsheet" in blob:
            score += 5
        # Date-ish patterns
        if re.search(r"\d{2}[.\-_]\d{2}[.\-_]\d{2,4}", blob):
            score += 10
        return score

    def _collect_links_from_html(self, html: str, page_url: str) -> list[MoodleFileHit]:
        soup = BeautifulSoup(html, "html.parser")
        hits: list[MoodleFileHit] = []
        for a in soup.find_all("a", href=True):
            href = a["href"]
            title = a.get_text(" ", strip=True) or href
            abs_url = urljoin(page_url, href)
            low = abs_url.casefold()
            ext = ""
            if ".pdf" in low:
                ext = "pdf"
            elif ".xlsx" in low or "spreadsheet" in low:
                ext = "xlsx"
            elif "/mod/resource/view.php" in low or "/pluginfile.php" in low:
                # Moodle resource — keep and score; download will reveal type
                ext = "resource"
            else:
                continue
            score = self._score_file(title, abs_url)
            if ext == "resource" and score < 15:
                continue
            if score <= 0 and ext not in {"pdf", "xlsx"}:
                continue
            hits.append(MoodleFileHit(title=title, url=abs_url, score=score, ext=ext))
        return hits

    def search_schedule_files(self) -> list[MoodleFileHit]:
        seeds = [
            f"{self.base_url}/my/",
            f"{self.base_url}/course/index.php",
            f"{self.base_url}/course/index.php?categoryid=79",  # IMA
            f"{self.base_url}/course/index.php?categoryid=51",
            f"{self.base_url}/course/search.php?search=դասացուցակ",
            f"{self.base_url}/course/search.php?search=timetable",
            f"{self.base_url}/course/search.php?search=schedule",
            f"{self.base_url}/course/search.php?search=IKM",
        ]
        all_hits: dict[str, MoodleFileHit] = {}
        course_links: list[str] = []

        for url in seeds:
            try:
                r = self.client.get(url)
                if r.status_code >= 400:
                    continue
            except httpx.HTTPError:
                continue
            for hit in self._collect_links_from_html(r.text, str(r.url)):
                prev = all_hits.get(hit.url)
                if not prev or hit.score > prev.score:
                    all_hits[hit.url] = hit
            soup = BeautifulSoup(r.text, "html.parser")
            for a in soup.find_all("a", href=True):
                href = a["href"]
                if "/course/view.php" in href:
                    course_links.append(urljoin(str(r.url), href))

        # Crawl a limited number of course pages
        seen_courses = set()
        for curl in course_links:
            if curl in seen_courses:
                continue
            seen_courses.add(curl)
            if len(seen_courses) > 25:
                break
            try:
                r = self.client.get(curl)
                if r.status_code >= 400:
                    continue
            except httpx.HTTPError:
                continue
            for hit in self._collect_links_from_html(r.text, str(r.url)):
                prev = all_hits.get(hit.url)
                if not prev or hit.score > prev.score:
                    all_hits[hit.url] = hit

        ranked = sorted(all_hits.values(), key=lambda h: h.score, reverse=True)
        return [h for h in ranked if h.score >= 20][:15]

    def download_file(self, url: str, dest_dir: Path = CACHE_DIR) -> Optional[Path]:
        dest_dir.mkdir(parents=True, exist_ok=True)
        try:
            r = self.client.get(url)
            r.raise_for_status()
        except httpx.HTTPError:
            return None

        ctype = r.headers.get("content-type", "").casefold()
        # filename from disposition or URL
        cd = r.headers.get("content-disposition", "")
        fname = None
        m = re.search(r'filename\*?=(?:UTF-8\'\')?"?([^";]+)"?', cd, re.I)
        if m:
            fname = m.group(1).strip()
        if not fname:
            path = urlparse(url).path
            fname = Path(path).name or "moodle_file"
        if "pdf" in ctype and not fname.casefold().endswith(".pdf"):
            fname += ".pdf"
        if ("sheet" in ctype or "excel" in ctype) and not fname.casefold().endswith(".xlsx"):
            fname += ".xlsx"

        # Only keep pdf/xlsx
        low = fname.casefold()
        if not (low.endswith(".pdf") or low.endswith(".xlsx")):
            # sniff magic
            if r.content[:4] == b"%PDF":
                fname += ".pdf"
            elif r.content[:2] == b"PK":
                fname += ".xlsx"
            else:
                return None

        safe = re.sub(r"[^\w.\-]+", "_", fname, flags=re.UNICODE)[:120]
        dest = dest_dir / safe
        dest.write_bytes(r.content)
        return dest


def try_fetch_moodle_schedules(username: str, password: str) -> tuple[bool, str, list[Path]]:
    """Login and download best schedule candidates. Returns (login_ok, message, files)."""
    files: list[Path] = []
    with MoodleClient() as client:
        ok, msg = client.login(username, password)
        if not ok:
            return False, msg, []
        hits = client.search_schedule_files()
        if not hits:
            return True, "Couldn't find the schedule on Moodle; using local fallback files.", []
        for hit in hits[:5]:
            path = client.download_file(hit.url)
            if path:
                files.append(path)
        if not files:
            return True, "Couldn't find the schedule on Moodle; using local fallback files.", []
        return True, f"Found {len(files)} schedule file(s) on Moodle.", files
