"""
Match page parser for bauman_league.join.football.

Fetches a match page by URL and extracts goal events from the HTML timeline,
determining which team scored and the resulting score.
"""

import logging
import os
import re
from dataclasses import dataclass
from typing import List, Optional, Tuple

import requests
import urllib3
from bs4 import BeautifulSoup

logger = logging.getLogger(__name__)

DEFAULT_TEAM_NAME = "Bauman United"
TEAM_NAME = (os.getenv("MATCH_PAGE_TEAM_NAME") or DEFAULT_TEAM_NAME).strip() or DEFAULT_TEAM_NAME


@dataclass
class GoalEvent:
    minute: str
    score: str  # BU first, then opponent (see _site_score_to_bu_first); e.g. "1 : 0"
    is_our_goal: bool
    scorer_name: Optional[str] = None  # full name for our goals
    scorer_surname: Optional[str] = None  # surname only, for GPT prompt


@dataclass
class MatchParseResult:
    """Outcome of parsing a match page: goals (may be empty) and team lineup."""

    goals: List[GoalEvent]
    home_team: str  # team 1, хозяева (first side on the page)
    away_team: str  # team 2, гости
    our_team_position: int  # 1 = home, 2 = away
    timeline_present: bool  # False when js-game-live-timeline is missing (e.g. pre-kickoff)


def format_match_teams_summary(result: MatchParseResult) -> str:
    """Human-readable team detection for Telegram (Russian)."""
    side = (
        "хозяева (команда 1)"
        if result.our_team_position == 1
        else "гости (команда 2)"
    )
    return (
        f"🏠 Хозяева (1): {result.home_team}\n"
        f"✈️ Гости (2): {result.away_team}\n"
        f"{TEAM_NAME}: {side}."
    )


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


def _site_score_to_bu_first(score: str, our_team_position: int) -> str:
    """
    Join.Football timeline shows home (team 1) first, away (team 2) second.
    GPT prompts assume the first number is always Bauman United.

    When we are away (position 2), swap the two sides so score strings match
    that convention everywhere (seen_scores, channel text, GPT).
    """
    if our_team_position != 2:
        return score
    s = score.strip()
    m = re.match(r"^(\d+)(\s*[\-–—:：]\s*)(\d+)$", s)
    if m:
        return f"{m.group(3)}{m.group(2)}{m.group(1)}"
    nums = re.findall(r"\d+", s)
    if len(nums) >= 2:
        return f"{nums[1]} : {nums[0]}"
    return score


def _element_classes(val) -> List[str]:
    if val is None:
        return []
    if isinstance(val, str):
        return val.split()
    return list(val)


def _team_name_from_game_header_link(link) -> Optional[str]:
    div = link.find("div", class_="game-header__text")
    if not div:
        return None
    title = div.get("title")
    if title and str(title).strip():
        return str(title).strip()
    text = div.get_text(strip=True)
    return text or None


def _extract_teams_from_game_section(soup: BeautifulSoup) -> Optional[Tuple[str, str]]:
    """Fallback: team names from the main match scoreboard (works before kickoff)."""
    section = soup.find(
        "section", class_=lambda c: c and "game--shadow" in _element_classes(c),
    )
    if not section:
        section = soup.find("section", class_=lambda c: c and "game" in _element_classes(c))
    if not section:
        return None
    units = section.find_all("div", class_="game__unit", limit=2)
    if len(units) < 2:
        return None
    names: List[str] = []
    for unit in units:
        name = None
        name_div = unit.find("div", class_="game__team-name")
        if name_div:
            name = name_div.get_text(strip=True)
        if not name:
            tlink = unit.find("a", class_="game__team-link")
            if tlink and tlink.get("title"):
                name = str(tlink["title"]).strip()
        if not name:
            return None
        names.append(name)
    return names[0], names[1]


def _extract_match_teams(soup: BeautifulSoup) -> Tuple[str, str]:
    """
    Resolve home (1) and away (2) team names.
    Prefer headers next to the live timeline; fall back to the main game block.
    """
    timeline = soup.find("div", class_="js-game-live-timeline")
    for root in ([timeline] if timeline else []) + [soup]:
        if root is None:
            continue
        links = root.find_all("a", class_="game-header__team", limit=2)
        if len(links) >= 2:
            n1 = _team_name_from_game_header_link(links[0])
            n2 = _team_name_from_game_header_link(links[1])
            if n1 and n2:
                return n1, n2
    from_section = _extract_teams_from_game_section(soup)
    if from_section:
        return from_section
    raise ValueError("Could not find both team names on the match page")


def parse_match_page(html: str) -> MatchParseResult:
    """
    Parse a match HTML page: team sides and goal events from the live timeline.

    If the match has not started, the timeline block may be missing; team names
    are still read from the scoreboard and goals list is empty.
    """
    soup = BeautifulSoup(html, "html.parser")

    home_team, away_team = _extract_match_teams(soup)

    our_team_position: Optional[int] = None
    if TEAM_NAME in home_team:
        our_team_position = 1
    elif TEAM_NAME in away_team:
        our_team_position = 2

    if our_team_position is None:
        raise ValueError(f"Could not find '{TEAM_NAME}' in either team name")

    logger.info(f"{TEAM_NAME} is team {our_team_position}")

    goals: List[GoalEvent] = []
    events_section = soup.find("div", class_="js-game-live-timeline")

    if not events_section:
        logger.info("No js-game-live-timeline on page — treating as no parsed events yet")
        return MatchParseResult(
            goals=goals,
            home_team=home_team,
            away_team=away_team,
            our_team_position=our_team_position,
            timeline_present=False,
        )

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
        score = _site_score_to_bu_first(score, our_team_position)

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
    return MatchParseResult(
        goals=goals,
        home_team=home_team,
        away_team=away_team,
        our_team_position=our_team_position,
        timeline_present=True,
    )
