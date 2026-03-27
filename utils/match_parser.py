"""
Match page parser for bauman_league.join.football.

Fetches a match page by URL and extracts goal events from the HTML timeline,
determining which team scored and the resulting score.
"""

import logging
from dataclasses import dataclass
from typing import List, Optional

import requests
import urllib3
from bs4 import BeautifulSoup

logger = logging.getLogger(__name__)

TEAM_NAME = "Bauman United"


@dataclass
class GoalEvent:
    minute: str
    score: str  # e.g. "1 : 0"
    is_our_goal: bool
    scorer_name: Optional[str] = None  # full name for our goals
    scorer_surname: Optional[str] = None  # surname only, for GPT prompt


def _looks_like_ssl_failure(exc: BaseException) -> bool:
    chain: list[BaseException] = []
    e: Optional[BaseException] = exc
    while e is not None and len(chain) < 10:
        chain.append(e)
        e = e.__cause__ or e.__context__

    for item in chain:
        err = str(item).lower()
        if "ssl" in err or "certificate" in err or "tls" in err or "hostname" in err:
            return True
    return False


def fetch_match_html(url: str) -> str:
    """
    Fetch match HTML. Join.Football may serve a cert that does not list the
    league subdomain (hostname mismatch); in that case we retry without verify.
    """
    try:
        resp = requests.get(url, timeout=15)
        resp.raise_for_status()
        return resp.text
    except requests.exceptions.RequestException as e:
        if not _looks_like_ssl_failure(e):
            raise
        logger.warning(
            "SSL verification failed for match URL (retrying without verify): %s — %s",
            url,
            e,
        )
        urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)
        resp = requests.get(url, timeout=15, verify=False)
        resp.raise_for_status()
        return resp.text


def _extract_surname(full_name: str) -> str:
    """Extract surname (last word) from a full name like 'Егор Шевченко'."""
    parts = full_name.strip().split()
    return parts[-1] if parts else full_name.strip()


def parse_match_page(html: str) -> List[GoalEvent]:
    """
    Parse a match HTML page and return a list of GoalEvent objects
    in chronological order (as they appear in the timeline).
    """
    soup = BeautifulSoup(html, "html.parser")

    events_section = soup.find("div", class_="js-game-live-timeline")
    if not events_section:
        raise ValueError("Could not find events section (js-game-live-timeline)")

    team_links = events_section.find_all("a", class_="game-header__team", limit=2)
    if len(team_links) < 2:
        raise ValueError("Could not find both team headers in events section")

    first_team_name = team_links[0].find("div", class_="game-header__text")
    our_team_position = None
    if first_team_name and TEAM_NAME in first_team_name.get_text():
        our_team_position = 1
    else:
        second_team_name = team_links[1].find("div", class_="game-header__text")
        if second_team_name and TEAM_NAME in second_team_name.get_text():
            our_team_position = 2

    if our_team_position is None:
        raise ValueError(f"Could not find '{TEAM_NAME}' in either team header")

    logger.info(f"{TEAM_NAME} is team {our_team_position}")

    goals: List[GoalEvent] = []

    timeline_items = events_section.find_all("li", class_="timeline__item")

    for item in timeline_items:
        event_divs = item.find_all("div", class_="timeline__event")
        if len(event_divs) < 2:
            continue

        team1_event = event_divs[0]
        team2_event = event_divs[1]

        goal_team = None
        goal_event_div = None

        icon1 = team1_event.find("div", class_="timeline__icon")
        if icon1 and icon1.get("title") == "Гол":
            goal_team = 1
            goal_event_div = team1_event

        if goal_team is None:
            icon2 = team2_event.find("div", class_="timeline__icon")
            if icon2 and icon2.get("title") == "Гол":
                goal_team = 2
                goal_event_div = team2_event

        if goal_team is None:
            continue

        minute_div = item.find("div", class_="timeline__minute-text")
        minute = minute_div.get_text(strip=True) if minute_div else "?"

        score_div = item.find("div", class_="timeline__score-text")
        score = score_div.get_text(strip=True) if score_div else "?"

        is_our_goal = (goal_team == our_team_position)

        scorer_name = None
        scorer_surname = None
        if is_our_goal:
            name_tag = goal_event_div.find("a", class_="timeline__name")
            if name_tag:
                scorer_name = name_tag.get_text(strip=True)
                scorer_surname = _extract_surname(scorer_name)

        goals.append(GoalEvent(
            minute=minute,
            score=score,
            is_our_goal=is_our_goal,
            scorer_name=scorer_name,
            scorer_surname=scorer_surname,
        ))

    logger.info(f"Parsed {len(goals)} goal(s) from match page")
    return goals
