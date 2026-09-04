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
