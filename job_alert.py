import json
import os
import re
import smtplib
import sys
import time
from email.message import EmailMessage
from pathlib import Path
from urllib.parse import urljoin

import requests
from bs4 import BeautifulSoup

# ---------------- YOUR SPEC (edit these) ----------------
KEYWORDS = ["laravel", "react", "next.js", "full stack", "intern"]  # match ANY of these
EXCLUDE = ["senior", "lead", "manager", "director"]                 # skip if title has ANY
LOCATION = "Nepal"

# Local sites: the script grabs every link on these pages whose text matches KEYWORDS.
# Works for server-rendered pages only. Add/remove URLs freely.
OTHER_PAGES = [
    "https://merojob.com/",
    "https://www.jobsnepal.com/",
]
# ---------------------------------------------------------

LINKEDIN_DELAY_SECONDS = 5  # pause between LinkedIn requests to avoid 429

SEEN_FILE = Path("seen.json")
HEADERS = {"User-Agent": "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
                         "(KHTML, like Gecko) Chrome/124.0 Safari/537.36"}
LINKEDIN_URL = "https://www.linkedin.com/jobs-guest/jobs/api/seeMoreJobPostings/search"


def has_word(text: str, word: str) -> bool:
    return re.search(rf"(?<!\w){re.escape(word.lower())}(?!\w)", text.lower()) is not None


def matches(title: str) -> bool:
    return any(has_word(title, k) for k in KEYWORDS) and not any(
        has_word(title, x) for x in EXCLUDE
    )


def scrape_linkedin() -> list[dict]:
    jobs = []
    for i, kw in enumerate(KEYWORDS):
        if i > 0:
            time.sleep(LINKEDIN_DELAY_SECONDS)
        try:
            r = requests.get(
                LINKEDIN_URL,
                params={"keywords": kw, "location": LOCATION, "f_TPR": "r86400", "start": 0},
                headers=HEADERS,
                timeout=20,
            )
            if r.status_code == 429:
                print(f"[linkedin] rate limited at '{kw}', stopping LinkedIn for this run")
                break
            r.raise_for_status()
        except requests.RequestException as e:
            print(f"[linkedin] '{kw}' failed: {e}")
            continue
        soup = BeautifulSoup(r.text, "html.parser")
        for card in soup.select("div.base-card"):
            a = card.select_one("a.base-card__full-link")
            title = card.select_one("h3.base-search-card__title")
            company = card.select_one("h4.base-search-card__subtitle")
            place = card.select_one("span.job-search-card__location")
            if not (a and title):
                continue
            t = title.get_text(strip=True)
            if any(has_word(t, x) for x in EXCLUDE):
                continue
            jobs.append({
                "title": t,
                "company": company.get_text(strip=True) if company else "",
                "location": place.get_text(strip=True) if place else "",
                "url": a["href"].split("?")[0],
                "source": "LinkedIn",
            })
    return jobs


def scrape_pages() -> list[dict]:
    jobs = []
    for page in OTHER_PAGES:
        try:
            r = requests.get(page, headers=HEADERS, timeout=20)
            r.raise_for_status()
        except requests.RequestException as e:
            print(f"[{page}] failed: {e}")
            continue
        soup = BeautifulSoup(r.text, "html.parser")
        for a in soup.find_all("a", href=True):
            text = a.get_text(" ", strip=True)
            if len(text) < 5 or not matches(text):
                continue
            jobs.append({
                "title": text,
                "company": "",
                "location": "",
                "url": urljoin(page, a["href"]),
                "source": page.split("//")[1].split("/")[0],
            })
    return jobs


def send_email(jobs: list[dict]) -> None:
    user = os.environ["GMAIL_USER"]
    password = os.environ["GMAIL_APP_PASSWORD"].replace(" ", "")
    to = os.environ.get("MAIL_TO") or user

    lines = []
    for j in jobs:
        meta = " | ".join(x for x in (j["company"], j["location"], j["source"]) if x)
        lines.append(f"{j['title']}\n{meta}\n{j['url']}\n")

    msg = EmailMessage()
    msg["Subject"] = f"{len(jobs)} new job(s) in {LOCATION}"
    msg["From"] = user
    msg["To"] = to
    msg.set_content("\n".join(lines))

    try:
        with smtplib.SMTP_SSL("smtp.gmail.com", 465, timeout=30) as s:
            s.login(user, password)
            s.send_message(msg)
    except (smtplib.SMTPAuthenticationError, smtplib.SMTPServerDisconnected) as e:
        sys.exit(
            f"Gmail rejected the login ({type(e).__name__}). GMAIL_APP_PASSWORD must be a "
            "16-letter Google App Password (myaccount.google.com/apppasswords), "
            "not your normal password."
        )


def main() -> None:
    seen = set(json.loads(SEEN_FILE.read_text())) if SEEN_FILE.exists() else set()

    found = {}
    for j in scrape_linkedin() + scrape_pages():
        found.setdefault(j["url"], j)

    new = [j for url, j in found.items() if url not in seen]
    print(f"found {len(found)}, new {len(new)}")

    if new:
        send_email(new)
        seen.update(j["url"] for j in new)
        SEEN_FILE.write_text(json.dumps(sorted(seen)[-3000:], indent=1))


if __name__ == "__main__":
    main()