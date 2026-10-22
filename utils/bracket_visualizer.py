"""Bracket visualizer — renders tournament brackets into high-quality images."""

from __future__ import annotations

import io
from typing import Any
import matplotlib
matplotlib.use("Agg")  # Non-interactive headless backend
import matplotlib.pyplot as plt
import matplotlib.patches as patches

# Modern gaming dark palette
BG_COLOR = "#0f172a"        # Deep slate background
CARD_BG = "#1e293b"         # Slate card background
CARD_BORDER = "#334155"     # Subtle card border
WINNER_BORDER = "#22c55e"   # Green accent for winner
ACCENT_BLUE = "#38bdf8"     # Cyan / Sky blue for headers
TEXT_MAIN = "#f8fafc"       # Crisp white
TEXT_MUTED = "#94a3b8"      # Light slate for secondary info
LINE_COLOR = "#475569"      # Connector bracket lines
GOLD = "#fbbf24"            # Trophy gold


def render_bracket_image(
    tournament_name: str,
    round_data: list[dict[str, Any]],
) -> io.BytesIO:
    """Render a tournament knockout bracket as a PNG image in memory.

    Parameters
    ----------
    tournament_name:
        Name of the tournament.
    round_data:
        List of dicts per round in order:
        [
            {
                "round": Round object (has .round_number, .label),
                "matches": list of Match objects,
                "teams": dict[team_id, Team],
                "legs": dict[match_id, list of MatchLeg],
            },
            ...
        ]

    Returns
    -------
    io.BytesIO
        In-memory PNG buffer ready to send via Discord.
    """
    num_rounds = len(round_data)
    if num_rounds == 0:
        # Return minimal empty canvas
        fig, ax = plt.subplots(figsize=(6, 4), facecolor=BG_COLOR)
        ax.set_facecolor(BG_COLOR)
        ax.text(0.5, 0.5, "No bracket data available", color=TEXT_MAIN, ha="center", va="center")
        ax.axis("off")
        buf = io.BytesIO()
        plt.savefig(buf, format="png", bbox_inches="tight", dpi=150)
        plt.close(fig)
        buf.seek(0)
        return buf

    # Determine dimensions
    # First round match count sets height scale
    first_round_matches = len(round_data[0].get("matches", []))
    max_matches_in_col = max(first_round_matches, 1)

    card_width = 240
    card_height = 80
    col_gap = 120
    row_gap = 40

    total_content_height = max_matches_in_col * (card_height + row_gap)
    canvas_height = max(total_content_height + 160, 420)
    canvas_width = max(num_rounds * (card_width + col_gap) + 80, 800)

    # Set up matplotlib figure (100 dpi scale)
    fig_w = canvas_width / 100
    fig_h = canvas_height / 100
    fig, ax = plt.subplots(figsize=(fig_w, fig_h), facecolor=BG_COLOR, dpi=120)
    ax.set_facecolor(BG_COLOR)
    ax.set_xlim(0, canvas_width)
    ax.set_ylim(0, canvas_height)
    ax.axis("off")

    # Tournament Title Header
    ax.text(
        40,
        canvas_height - 45,
        f"{tournament_name.upper()} — TOURNAMENT BRACKET",
        fontsize=15,
        fontweight="bold",
        color=GOLD,
        ha="left",
        va="center",
        family="Segoe UI",
    )

    # Calculate match card centers per round
    # round_match_coords[round_idx][match_idx] = (cx, cy)
    round_match_coords: list[list[tuple[float, float]]] = []

    y_content_bottom = 50
    y_content_top = canvas_height - 90
    usable_h = y_content_top - y_content_bottom

    for r_idx, r_dict in enumerate(round_data):
        matches = r_dict.get("matches", [])
        m_count = len(matches)
        cx = 40 + (card_width / 2) + r_idx * (card_width + col_gap)

        coords_this_round: list[tuple[float, float]] = []
        if m_count > 0:
            step = usable_h / m_count
            for m_idx in range(m_count):
                # Distribute evenly vertically from top to bottom
                cy = y_content_top - (m_idx + 0.5) * step
                coords_this_round.append((cx, cy))

        round_match_coords.append(coords_this_round)

    # 1. Draw bracket connecting lines between consecutive rounds
    for r_idx in range(num_rounds - 1):
        curr_coords = round_match_coords[r_idx]
        next_coords = round_match_coords[r_idx + 1]

        for next_m_idx, (next_cx, next_cy) in enumerate(next_coords):
            # Each match in next round connects from 2 matches in current round
            m1_idx = next_m_idx * 2
            m2_idx = next_m_idx * 2 + 1

            in_coords = []
            if m1_idx < len(curr_coords):
                in_coords.append(curr_coords[m1_idx])
            if m2_idx < len(curr_coords):
                in_coords.append(curr_coords[m2_idx])

            for in_cx, in_cy in in_coords:
                start_x = in_cx + (card_width / 2)
                start_y = in_cy
                end_x = next_cx - (card_width / 2)
                end_y = next_cy
                mid_x = (start_x + end_x) / 2

                # Step elbow line: (start_x, start_y) -> (mid_x, start_y) -> (mid_x, end_y) -> (end_x, end_y)
                ax.plot([start_x, mid_x], [start_y, start_y], color=LINE_COLOR, lw=2.2, zorder=1)
                ax.plot([mid_x, mid_x], [start_y, end_y], color=LINE_COLOR, lw=2.2, zorder=1)
                ax.plot([mid_x, end_x], [end_y, end_y], color=LINE_COLOR, lw=2.2, zorder=1)

    # 2. Draw round columns, header tags, and match cards
    for r_idx, r_dict in enumerate(round_data):
        round_obj = r_dict.get("round")
        label = round_obj.label if round_obj else f"Round {r_idx + 1}"
        matches = r_dict.get("matches", [])
        teams_map = r_dict.get("teams", {})
        legs_map = r_dict.get("legs", {})
        coords = round_match_coords[r_idx]

        # Column Header (Round Label)
        col_cx = 40 + (card_width / 2) + r_idx * (card_width + col_gap)
        ax.text(
            col_cx,
            canvas_height - 75,
            label.upper(),
            fontsize=11,
            fontweight="bold",
            color=ACCENT_BLUE,
            ha="center",
            va="center",
            family="Segoe UI",
        )

        for m_idx, match in enumerate(matches):
            if m_idx >= len(coords):
                break
            cx, cy = coords[m_idx]

            home = teams_map.get(match.home_team_id)
            away = teams_map.get(match.away_team_id)
            home_name = home.name if home else "TBD"
            away_name = away.name if away else "TBD"

            # Truncate long names for card fitting
            if len(home_name) > 15:
                home_name = home_name[:14] + "…"
            if len(away_name) > 15:
                away_name = away_name[:14] + "…"

            match_legs = legs_map.get(match.id, [])
            agg_home = sum(l.home_score for l in match_legs if l.home_score is not None)
            agg_away = sum(l.away_score for l in match_legs if l.away_score is not None)

            is_completed = (match.status.value == "completed")
            winner_id = match.winner_team_id

            # Card box background
            card_x = cx - card_width / 2
            card_y = cy - card_height / 2

            box_border = WINNER_BORDER if (is_completed and winner_id) else CARD_BORDER
            rect = patches.FancyBboxPatch(
                (card_x, card_y),
                card_width,
                card_height,
                boxstyle="round,pad=0,rounding_size=8",
                facecolor=CARD_BG,
                edgecolor=box_border,
                linewidth=1.8 if (is_completed and winner_id) else 1.2,
                zorder=2,
            )
            ax.add_patch(rect)

            # Match ID header badge inside card
            ax.text(
                card_x + 10,
                card_y + card_height - 14,
                f"Match #{match.match_number}  (ID: {match.id})",
                fontsize=8,
                color=TEXT_MUTED,
                ha="left",
                va="center",
                family="Segoe UI",
            )

            status_str = "FINISHED" if is_completed else "PENDING"
            status_col = WINNER_BORDER if is_completed else "#eab308"
            ax.text(
                card_x + card_width - 10,
                card_y + card_height - 14,
                status_str,
                fontsize=7.5,
                fontweight="bold",
                color=status_col,
                ha="right",
                va="center",
                family="Segoe UI",
            )

            # Home team row
            home_is_win = (winner_id == match.home_team_id and is_completed)
            home_color = "#4ade80" if home_is_win else TEXT_MAIN
            home_weight = "bold" if home_is_win else "normal"
            home_score_str = str(agg_home) if (is_completed and len(match_legs) > 0) else "-"

            prefix_home = "[W] " if home_is_win else "    "
            ax.text(
                card_x + 10,
                card_y + card_height - 38,
                prefix_home + home_name,
                fontsize=9.5,
                fontweight=home_weight,
                color=home_color,
                ha="left",
                va="center",
                family="Segoe UI",
            )
            ax.text(
                card_x + card_width - 14,
                card_y + card_height - 38,
                home_score_str,
                fontsize=9.5,
                fontweight="bold",
                color=home_color,
                ha="right",
                va="center",
                family="Segoe UI",
            )

            # Divider inside card
            ax.plot(
                [card_x + 8, card_x + card_width - 8],
                [card_y + card_height - 49, card_y + card_height - 49],
                color=CARD_BORDER,
                lw=0.8,
                zorder=3,
            )

            # Away team row
            away_is_win = (winner_id == match.away_team_id and is_completed)
            away_color = "#4ade80" if away_is_win else TEXT_MAIN
            away_weight = "bold" if away_is_win else "normal"
            away_score_str = str(agg_away) if (is_completed and len(match_legs) > 0) else "-"

            prefix_away = "[W] " if away_is_win else "    "
            ax.text(
                card_x + 10,
                card_y + card_height - 62,
                prefix_away + away_name,
                fontsize=9.5,
                fontweight=away_weight,
                color=away_color,
                ha="left",
                va="center",
                family="Segoe UI",
            )
            ax.text(
                card_x + card_width - 14,
                card_y + card_height - 62,
                away_score_str,
                fontsize=9.5,
                fontweight="bold",
                color=away_color,
                ha="right",
                va="center",
                family="Segoe UI",
            )

    buf = io.BytesIO()
    plt.savefig(buf, format="png", bbox_inches="tight", pad_inches=0.2, dpi=130)
    plt.close(fig)
    buf.seek(0)
    return buf
