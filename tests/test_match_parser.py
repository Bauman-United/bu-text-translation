"""Tests for the match page parser."""

import pytest

from utils.match_parser import parse_match_page


# Trimmed copy of the scoreboard header Join.Football renders on every match
# page — including one that has not kicked off yet (no js-game-live-timeline).
UPCOMING_MATCH_HTML = """
<html lang="ru"><body>
<section class="blk-matchheader">
  <div class="blk-matchheader__main">
    <div class="blk-matchheader__unit">
      <a class="blk-matchheader__logo" href="/tournament/1066703/teams/application?team_id=1089660"></a>
      <div class="blk-matchheader__team">
        <a class="blk-matchheader__team-link" href="#" title="Bauman United">
          <div class="blk-matchheader__team-name">Bauman United</div>
        </a>
      </div>
    </div>
    <div class="blk-matchheader__middle">
      <div class="blk-matchheader__future"><div class="blk-matchheader__future-number">-&nbsp;:&nbsp;-</div></div>
    </div>
    <div class="blk-matchheader__unit">
      <a class="blk-matchheader__logo" href="/tournament/1066703/teams/application?team_id=1256048"></a>
      <div class="blk-matchheader__team">
        <a class="blk-matchheader__team-link" href="#" title="Bauman Junior">
          <div class="blk-matchheader__team-name">Bauman Junior</div>
        </a>
      </div>
    </div>
  </div>
</section>
</body></html>
"""


def test_upcoming_match_reads_teams_without_timeline():
    result = parse_match_page(UPCOMING_MATCH_HTML)

    assert result.home_team == "Bauman United"
    assert result.away_team == "Bauman Junior"
    assert result.our_team_position == 1
    assert result.timeline_present is False
    assert result.goals == []


# Live timeline from match 5877228 (Квазар 3:3 Bauman United), trimmed to the
# goal entries. The 16' own goal is drawn on the side of the team it counts for
# (Bauman United) but names the opponent who put it into his own net.
OWN_GOAL_MATCH_HTML = """
<html lang="ru"><body>
<section class="blk-matchheader">
  <div class="blk-matchheader__main">
    <div class="blk-matchheader__unit"><div class="blk-matchheader__team">
      <a class="blk-matchheader__team-link" href="#" title="Квазар"><div class="blk-matchheader__team-name">Квазар</div></a>
    </div></div>
    <div class="blk-matchheader__unit"><div class="blk-matchheader__team">
      <a class="blk-matchheader__team-link" href="#" title="Bauman United"><div class="blk-matchheader__team-name">Bauman United</div></a>
    </div></div>
  </div>
</section>
<div class="js-game-live-timeline"><ul>
  <li class="timeline__item">
    <div class="timeline__event"><div class="timeline__icon" title="Гол"></div><a class="timeline__name">Махмудов Ислам</a></div>
    <div class="timeline__event"></div>
    <div class="timeline__minute-text">12'</div><div class="timeline__score-text">2 : 2</div>
  </li>
  <li class="timeline__item">
    <div class="timeline__event"></div>
    <div class="timeline__event"><div class="timeline__icon" title="Автогол"></div><a class="timeline__name">Кожевников Евгений</a></div>
    <div class="timeline__minute-text">16'</div><div class="timeline__score-text">2 : 3</div>
  </li>
</ul></div>
</body></html>
"""


def test_own_goal_counts_for_the_side_it_is_drawn_on_without_a_scorer():
    result = parse_match_page(OWN_GOAL_MATCH_HTML)

    own_goal = result.goals[-1]
    assert own_goal.score == "3 : 2"  # Bauman United first
    assert own_goal.is_our_goal is True
    # The named player is the opponent who scored it — not one of ours.
    assert own_goal.scorer_name is None
    assert own_goal.scorer_surname is None
