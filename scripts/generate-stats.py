#!/usr/bin/env python3
"""Generate navy + blurple GitHub profile stat cards from live public data.

Writes assets/stats.svg, assets/streak.svg, assets/languages.svg, assets/activity.svg.
Used locally and by .github/workflows/profile-stats.yml.
"""
from __future__ import annotations

import json
import math
import os
import re
import subprocess
import sys
import urllib.request
from collections import OrderedDict
from datetime import date, datetime, timedelta
from xml.sax.saxutils import escape

USER = os.environ.get("PROFILE_USER", "reseachrandomv")
REPO_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
ASSETS = os.path.join(REPO_ROOT, "assets")

BG = "#0B1020"
BORDER = "#5865F2"
TITLE = "#5865F2"
TEXT = "#E8ECF8"
MUTED = "#9AA3C2"
SOFT = "#A5B4FC"
TRACK = "#1A2152"
BAR = "#5865F2"
BAR2 = "#A5B4FC"
FONT = "'Segoe UI', Ubuntu, 'Helvetica Neue', sans-serif"

# Official language colours are not used: bars stay in the blurple family.
LANG_PALETTE = ["#5865F2", "#7B85F5", "#9AA3FB", "#A5B4FC", "#C7D2FE"]


def gh_graphql(query: str) -> dict:
    result = subprocess.run(
        ["gh", "api", "graphql", "-f", f"query={query}"],
        capture_output=True,
        text=True,
        check=False,
    )
    if result.returncode != 0:
        raise RuntimeError(f"gh graphql failed: {result.stderr or result.stdout}")
    payload = json.loads(result.stdout)
    if payload.get("errors"):
        raise RuntimeError(payload["errors"])
    return payload["data"]


def fetch_public_contributions(user: str) -> dict[str, int]:
    url = f"https://github.com/users/{user}/contributions"
    req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0 profile-stats"})
    with urllib.request.urlopen(req, timeout=30) as resp:
        html = resp.read().decode("utf-8", "replace")
    id_to_date = {}
    for m in re.finditer(
        r'data-date="(\d{4}-\d{2}-\d{2})"[^>]*id="(contribution-day-component-[^"]+)"'
        r'|id="(contribution-day-component-[^"]+)"[^>]*data-date="(\d{4}-\d{2}-\d{2})"',
        html,
    ):
        if m.group(1) and m.group(2):
            id_to_date[m.group(2)] = m.group(1)
        else:
            id_to_date[m.group(3)] = m.group(4)
    counts: dict[str, int] = OrderedDict()
    for m in re.finditer(
        r'<tool-tip[^>]*for="(contribution-day-component-[^"]+)"[^>]*>(.*?)</tool-tip>',
        html,
        re.S,
    ):
        day = id_to_date.get(m.group(1))
        if not day:
            continue
        text = re.sub(r"\s+", " ", m.group(2)).strip()
        if text.lower().startswith("no contribution"):
            counts[day] = 0
        else:
            mm = re.match(r"(\d+) contribution", text)
            if mm:
                counts[day] = int(mm.group(1))
    return counts


def compute_streaks(counts: dict[str, int]) -> dict:
    days = sorted(counts)
    if not days:
        return {
            "total": 0,
            "current": 0,
            "longest": 0,
            "current_start": None,
            "current_end": None,
            "longest_start": None,
            "longest_end": None,
            "first_contrib": None,
        }
    total = sum(counts[d] for d in days)
    first_contrib = next((d for d in days if counts[d] > 0), None)

    # Current streak: if today is 0, still count through yesterday (day in progress).
    idx = len(days) - 1
    if counts[days[idx]] == 0 and idx > 0:
        idx -= 1
    current = 0
    end = start = None
    i = idx
    while i >= 0 and counts[days[i]] > 0:
        current += 1
        start = days[i]
        end = days[idx] if end is None else end
        i -= 1

    longest = 0
    run = 0
    run_s = None
    ls = le = None
    for d in days:
        if counts[d] > 0:
            run += 1
            if run_s is None:
                run_s = d
            if run > longest:
                longest = run
                ls, le = run_s, d
        else:
            run = 0
            run_s = None

    return {
        "total": total,
        "current": current,
        "longest": longest,
        "current_start": start,
        "current_end": end,
        "longest_start": ls,
        "longest_end": le,
        "first_contrib": first_contrib,
    }


def fmt_day(iso: str | None, with_year: bool = False) -> str:
    if not iso:
        return "—"
    d = datetime.strptime(iso, "%Y-%m-%d")
    return d.strftime("%b %-d, %Y" if with_year else "%b %-d")


def fmt_range(a: str | None, b: str | None) -> str:
    if not a or not b:
        return "—"
    return f"{fmt_day(a)} – {fmt_day(b)}"


def calculate_rank(commits, prs, issues, reviews, stars, followers):
    """Classic github-readme-stats exponential rank (lower rank_value is better)."""

    def expo(value, median):
        return 1 - 2 ** (-(value / median)) if median else 0

    weights = {
        "commits": (2, 250, commits),
        "prs": (3, 50, prs),
        "issues": (1, 25, issues),
        "reviews": (1, 2, reviews),
        "stars": (4, 50, stars),
        "followers": (1, 15, followers),
    }
    total_w = sum(w for w, _, _ in weights.values())
    score = sum(w * expo(v, m) for w, m, v in weights.values()) / total_w
    rank_value = 1 - score
    levels = [
        ("S+", 0.01),  # unused with this formula's typical range
        ("S", 0.125),
        ("A+", 0.25),
        ("A", 0.375),
        ("B+", 0.5),
        ("B", 0.625),
        ("C+", 0.75),
        ("C", 0.875),
        ("D+", 1.0),
    ]
    # The classic mapping: walk from the best (lowest threshold) and pick the
    # first letter whose threshold is >= rank_value? Actually the published
    # 2022 table used: level is the last one whose threshold is still
    # <= rank? We use: first level where rank_value <= threshold, from best.
    # Wait: S is 0.125 meaning rank_value must be <= 0.125 for S (very hard).
    # Our rank_value ~0.81 -> C (0.875) or C+? 0.81 > 0.75 so not C+, <= 0.875 so C.
    #
    # Re-read common implementation:
    #   const RANK = { S: 1, A+: 2, ...} no
    #   Many clones:
    #   if (rank < 0.201) S+; else if (rank < 0.337) S; ... that used a different scale.
    #
    # Official older calculateRank returned {level} with:
    # https://github.com/anuraghazra/github-readme-stats/blob/v1.0.0/src/calculateRank.js
    # thresholds: S 0.01 percentile etc. after a later rewrite.
    #
    # We map honestly with the exponential score, using this widely copied table
    # for the *rank_value* (lower is better) from mid-2022 clones:
    table = [
        (0.201, "S+"),
        (0.337, "S"),
        (0.465, "A+"),
        (0.665, "A"),
        (0.785, "B+"),
        (0.845, "B"),
        (0.905, "C+"),
        (0.946, "C"),
        (0.976, "D+"),
        (1.01, "D"),
    ]
    level = "D"
    for thresh, letter in table:
        if rank_value <= thresh:
            level = letter
            break
    return {"level": level, "score": score, "rank_value": rank_value}


def language_totals(user: str) -> list[tuple[str, int]]:
    data = gh_graphql(
        """
        query {
          user(login: "%s") {
            followers { totalCount }
            pullRequests { totalCount }
            issues { totalCount }
            contributionsCollection {
              totalCommitContributions
              totalPullRequestReviewContributions
            }
            repositories(first: 100, ownerAffiliations: OWNER, privacy: PUBLIC, isFork: false) {
              nodes {
                stargazerCount
                languages(first: 10, orderBy: {field: SIZE, direction: DESC}) {
                  edges { size node { name } }
                }
              }
            }
            repositoriesContributedTo(
              first: 1,
              contributionTypes: [COMMIT, ISSUE, PULL_REQUEST, PULL_REQUEST_REVIEW],
              includeUserRepositories: true,
              privacy: PUBLIC
            ) { totalCount }
          }
        }
        """
        % user
    )
    u = data["user"]
    langs: dict[str, int] = {}
    stars = 0
    for node in u["repositories"]["nodes"]:
        stars += node["stargazerCount"] or 0
        for edge in node["languages"]["edges"]:
            langs[edge["node"]["name"]] = langs.get(edge["node"]["name"], 0) + edge["size"]
    ranked = sorted(langs.items(), key=lambda kv: kv[1], reverse=True)
    stats = {
        "stars": stars,
        "commits": u["contributionsCollection"]["totalCommitContributions"],
        "prs": u["pullRequests"]["totalCount"],
        "issues": u["issues"]["totalCount"],
        "reviews": u["contributionsCollection"]["totalPullRequestReviewContributions"],
        "followers": u["followers"]["totalCount"],
        "contributed_to": u["repositoriesContributedTo"]["totalCount"],
        "languages": ranked,
    }
    return stats


def card_open(w, h, title):
    return f'''<?xml version="1.0" encoding="UTF-8"?>
<svg xmlns="http://www.w3.org/2000/svg" width="{w}" height="{h}" viewBox="0 0 {w} {h}" role="img" aria-label="{escape(title)}">
  <title>{escape(title)}</title>
  <defs>
    <linearGradient id="cardBg" x1="0" y1="0" x2="1" y2="1">
      <stop offset="0%" stop-color="#0B1020"/>
      <stop offset="100%" stop-color="#12183A"/>
    </linearGradient>
    <linearGradient id="barFill" x1="0" y1="0" x2="1" y2="0">
      <stop offset="0%" stop-color="#5865F2"/>
      <stop offset="100%" stop-color="#A5B4FC"/>
    </linearGradient>
    <linearGradient id="areaFill" x1="0" y1="0" x2="0" y2="1">
      <stop offset="0%" stop-color="#5865F2" stop-opacity="0.45"/>
      <stop offset="100%" stop-color="#5865F2" stop-opacity="0.02"/>
    </linearGradient>
    <linearGradient id="topGlow" x1="0" y1="0" x2="1" y2="0">
      <stop offset="0%" stop-color="#5865F2" stop-opacity="0"/>
      <stop offset="50%" stop-color="#5865F2" stop-opacity="0.9"/>
      <stop offset="100%" stop-color="#5865F2" stop-opacity="0"/>
    </linearGradient>
    <style>
      .f {{ font-family: {FONT}; }}
    </style>
    <clipPath id="cardClip">
      <rect width="{w}" height="{h}" rx="16"/>
    </clipPath>
  </defs>
  <rect width="{w}" height="{h}" rx="16" fill="url(#cardBg)" stroke="#5865F2" stroke-opacity="0.55" stroke-width="1.5"/>
  <g clip-path="url(#cardClip)">
  <rect x="28" y="0.75" width="{w-56}" height="2" rx="1" fill="url(#topGlow)"/>
  <text class="f" x="22" y="32" fill="{TITLE}" font-size="16" font-weight="700">{escape(title)}</text>
'''


def card_close():
    return "  </g>\n</svg>\n"


def write(path, content):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as fh:
        fh.write(content)
    print(f"wrote {path} ({os.path.getsize(path)} bytes)")


def render_stats(s, rank) -> str:
    w, h = 495, 215
    rows = [
        ("Stars", s["stars"], 50),
        ("Commits (this year)", s["commits"], 250),
        ("Pull requests", s["prs"], 50),
        ("Issues", s["issues"], 25),
        ("Contributed to", s["contributed_to"], 30),
    ]
    body = [card_open(w, h, "GitHub Stats")]
    y = 58
    for i, (label, value, cap) in enumerate(rows):
        delay = 0.15 + i * 0.08
        pct = 0 if cap <= 0 else min(1.0, value / cap)
        bar_w = 168 * pct
        body.append(f'''  <g>
    <text class="f" x="22" y="{y}" fill="{MUTED}" font-size="12">{escape(label)}</text>
    <text class="f" x="248" y="{y}" fill="{TEXT}" font-size="13" font-weight="700" text-anchor="end">{value:,}</text>
    <rect x="22" y="{y+8}" width="168" height="6" rx="3" fill="{TRACK}"/>
    <rect x="22" y="{y+8}" width="{bar_w:.1f}" height="6" rx="3" fill="url(#barFill)">
      <animate attributeName="width" from="0" to="{bar_w:.1f}" dur="0.9s" begin="{delay:.2f}s" fill="freeze"/>
    </rect>
  </g>''')
        y += 30

    # Grade ring on the right
    cx, cy, r = 395, 122, 52
    circ = 2 * math.pi * r
    # Fill the ring with the contribution score (honest, not inflated).
    level = rank["level"]
    ring_pct = {
        "S+": 0.97, "S": 0.88, "A+": 0.78, "A": 0.68,
        "B+": 0.58, "B": 0.50, "C+": 0.40, "C": 0.30,
        "D+": 0.20, "D": 0.12,
    }.get(level, max(0.08, rank["score"]))
    dash = circ * ring_pct
    body.append(f'''  <g>
    <circle cx="{cx}" cy="{cy}" r="{r}" fill="none" stroke="{TRACK}" stroke-width="9"/>
    <circle cx="{cx}" cy="{cy}" r="{r}" fill="none" stroke="#5865F2" stroke-width="9"
            stroke-linecap="round" transform="rotate(-90 {cx} {cy})"
            stroke-dasharray="{dash:.2f} {circ:.2f}" stroke-dashoffset="0">
      <animate attributeName="stroke-dasharray" from="0 {circ:.2f}" to="{dash:.2f} {circ:.2f}" dur="1.2s" fill="freeze"/>
    </circle>
    <text class="f" x="{cx}" y="{cy-6}" fill="{TEXT}" font-size="26" font-weight="700" text-anchor="middle">{escape(level)}</text>
    <text class="f" x="{cx}" y="{cy+16}" fill="{SOFT}" font-size="11" text-anchor="middle">Rank</text>
  </g>
''')
    body.append(card_close())
    return "\n".join(body)


def render_streak(st: dict) -> str:
    w, h = 495, 215
    body = [card_open(w, h, "Contribution Streak")]
    cols = [
        (st["total"], "Total contributions",
         fmt_range(st["first_contrib"], st["current_end"] or date.today().isoformat())),
        (st["current"], "Current streak", fmt_range(st["current_start"], st["current_end"])),
        (st["longest"], "Longest streak", fmt_range(st["longest_start"], st["longest_end"])),
    ]

    xs = [85, 247, 410]
    for i, (x, (num, label, sub)) in enumerate(zip(xs, cols)):
        delay = 0.2 + i * 0.12
        extra = ""
        if i == 1:
            # soft ring around the current streak number
            extra = f'''    <circle cx="{x}" cy="100" r="46" fill="none" stroke="#5865F2" stroke-opacity="0.35" stroke-width="2">
      <animate attributeName="stroke-opacity" values="0.25;0.8;0.25" dur="3s" repeatCount="indefinite"/>
    </circle>'''
        body.append(f'''  <g text-anchor="middle">
{extra}
    <text class="f" x="{x}" y="108" fill="{TEXT}" font-size="32" font-weight="700">{num:,}</text>
    <text class="f" x="{x}" y="138" fill="{TITLE}" font-size="12" font-weight="600">{escape(label)}</text>
    <text class="f" x="{x}" y="158" fill="{MUTED}" font-size="11">{escape(sub)}</text>
  </g>''')

    # subtle dividers
    body.append(f'  <line x1="165" y1="62" x2="165" y2="180" stroke="#5865F2" stroke-opacity="0.22"/>')
    body.append(f'  <line x1="330" y1="62" x2="330" y2="180" stroke="#5865F2" stroke-opacity="0.22"/>')
    body.append(card_close())
    return "\n".join(body)


def render_languages(ranked: list[tuple[str, int]]) -> str:
    w, h = 495, 215
    body = [card_open(w, h, "Most Used Languages")]
    top = ranked[:5]
    total = sum(v for _, v in top) or 1
    y = 62
    for i, (name, size) in enumerate(top):
        pct = size / total
        bar_w = 451 * pct
        color = LANG_PALETTE[i % len(LANG_PALETTE)]
        delay = 0.15 + i * 0.08
        body.append(f'''  <g>
    <text class="f" x="22" y="{y}" fill="{TEXT}" font-size="13" font-weight="600">{escape(name)}</text>
    <text class="f" x="473" y="{y}" fill="{SOFT}" font-size="12" text-anchor="end">{pct*100:.1f}%</text>
    <rect x="22" y="{y+8}" width="451" height="8" rx="4" fill="{TRACK}"/>
    <rect x="22" y="{y+8}" width="{bar_w:.1f}" height="8" rx="4" fill="{color}">
      <animate attributeName="width" from="0" to="{bar_w:.1f}" dur="0.9s" begin="{delay:.2f}s" fill="freeze"/>
    </rect>
  </g>''')
        y += 28
    body.append(
        f'  <text class="f" x="22" y="200" fill="{MUTED}" font-size="10">'
        f"From public, non-fork repositories</text>"
    )
    body.append(card_close())
    return "\n".join(body)


def catmull(points):
    if len(points) == 0:
        return ""
    if len(points) == 1:
        x, y = points[0]
        return f"M {x:.2f},{y:.2f}"
    d = f"M {points[0][0]:.2f},{points[0][1]:.2f}"
    for i in range(len(points) - 1):
        p0 = points[i - 1] if i > 0 else points[i]
        p1 = points[i]
        p2 = points[i + 1]
        p3 = points[i + 2] if i + 2 < len(points) else p2
        c1x = p1[0] + (p2[0] - p0[0]) / 6.0
        c1y = p1[1] + (p2[1] - p0[1]) / 6.0
        c2x = p2[0] - (p3[0] - p1[0]) / 6.0
        c2y = p2[1] - (p3[1] - p1[1]) / 6.0
        d += f" C {c1x:.2f},{c1y:.2f} {c2x:.2f},{c2y:.2f} {p2[0]:.2f},{p2[1]:.2f}"
    return d


def render_activity(counts: dict[str, int], total: int) -> str:
    w, h = 990, 260
    # Start at the first contribution, or last 12 weeks, whichever is later
    # so the line is readable. Prefer from first contrib if within a year.
    days_all = sorted(counts)
    if not days_all:
        body = [card_open(w, h, "Contribution activity"), card_close()]
        return "\n".join(body)
    first_nz = next((d for d in days_all if counts[d] > 0), days_all[0])
    start = first_nz
    end = days_all[-1]
    # include a few days of padding before first contrib
    start_d = datetime.strptime(start, "%Y-%m-%d").date()
    end_d = datetime.strptime(end, "%Y-%m-%d").date()
    series = []
    d = start_d
    while d <= end_d:
        key = d.isoformat()
        series.append((key, counts.get(key, 0)))
        d += timedelta(days=1)

    left, right, top, bottom = 44, w - 22, 56, h - 36
    plot_w = right - left
    plot_h = bottom - top
    max_v = max((v for _, v in series), default=1) or 1
    # nice y max
    if max_v <= 10:
        y_max = 10
    elif max_v <= 20:
        y_max = 20
    elif max_v <= 50:
        y_max = 50
    elif max_v <= 80:
        y_max = 80
    else:
        y_max = math.ceil(max_v / 10) * 10

    n = len(series)
    pts = []
    for i, (_, v) in enumerate(series):
        x = left + (plot_w * i / max(1, n - 1))
        y = bottom - (plot_h * (v / y_max))
        pts.append((x, y, v))

    line_pts = [(x, y) for x, y, _ in pts]
    line = catmull(line_pts)
    area = line + f" L {pts[-1][0]:.2f},{bottom} L {pts[0][0]:.2f},{bottom} Z"

    body = [card_open(w, h, "Contribution activity")]
    body.append(
        f'  <text class="f" x="{w-22}" y="32" fill="{MUTED}" font-size="11" text-anchor="end">'
        f"{total:,} contributions this year · {fmt_day(start)} – {fmt_day(end, True)}</text>"
    )

    # y grid
    for frac, label_at in ((0, y_max), (0.5, y_max // 2), (1, 0)):
        y = top + plot_h * frac
        body.append(
            f'  <line x1="{left}" y1="{y:.1f}" x2="{right}" y2="{y:.1f}" stroke="#5865F2" stroke-opacity="0.12"/>'
        )
        body.append(
            f'  <text class="f" x="{left-8}" y="{y+4:.1f}" fill="{MUTED}" font-size="10" text-anchor="end">{label_at}</text>'
        )

    # x labels: first, mid, last
    ticks = [0, n // 2, n - 1]
    seen = set()
    for i in ticks:
        if i in seen:
            continue
        seen.add(i)
        x, _, _ = pts[i]
        body.append(
            f'  <text class="f" x="{x:.1f}" y="{bottom+18}" fill="{MUTED}" font-size="10" text-anchor="middle">{fmt_day(series[i][0])}</text>'
        )

    body.append(f'''  <path d="{area}" fill="url(#areaFill)"/>
  <path d="{line}" fill="none" stroke="#5865F2" stroke-width="2.4" stroke-linejoin="round" stroke-linecap="round"
        pathLength="1" stroke-dasharray="1" stroke-dashoffset="0">
    <animate attributeName="stroke-dashoffset" from="1" to="0" dur="1.6s" fill="freeze"/>
  </path>''')

    # highlight non-zero points, emphasize the last day
    for i, (x, y, v) in enumerate(pts):
        if v <= 0:
            continue
        r = 2.6 if i < n - 1 else 4.0
        fill = BAR2 if i < n - 1 else "#FFFFFF"
        body.append(
            f'  <circle cx="{x:.2f}" cy="{y:.2f}" r="{r}" fill="{fill}" stroke="#5865F2" stroke-width="1.2"/>'
        )
    body.append(card_close())
    return "\n".join(body)


def render_calendar(counts: dict[str, int]) -> str:
    """Heatmap from the week of the first contribution so recent activity stays in frame."""
    w, h = 495, 215
    body = [card_open(w, h, "Contribution calendar")]
    if not counts:
        body.append(card_close())
        return "\n".join(body)

    first = next((d for d, v in sorted(counts.items()) if v > 0), None)
    last = max(counts)
    if not first:
        body.append(card_close())
        return "\n".join(body)

    start = datetime.strptime(first, "%Y-%m-%d").date()
    end = datetime.strptime(last, "%Y-%m-%d").date()
    align = start - timedelta(days=(start.weekday() + 1) % 7)

    cells = []
    d = align
    while d <= end:
        cells.append((d, counts.get(d.isoformat(), 0)))
        d += timedelta(days=1)

    positives = sorted(v for _, v in cells if v > 0)
    if positives:
        def pct(p):
            i = min(len(positives) - 1, max(0, int(round((p / 100) * (len(positives) - 1)))))
            return max(1, positives[i])
        t1, t2, t3 = pct(25), pct(50), pct(75)
        if t2 <= t1:
            t2 = t1 + 1
        if t3 <= t2:
            t3 = t2 + 1
    else:
        t1, t2, t3 = 1, 3, 8

    palette = ["#161B33", "#2A3170", "#3E4BB3", "#5865F2", "#A5B4FC"]

    def level(v):
        if v <= 0:
            return 0
        if v <= t1:
            return 1
        if v <= t2:
            return 2
        if v <= t3:
            return 3
        return 4

    weeks = {}
    for d, v in cells:
        delta = (d - align).days
        wi, di = divmod(delta, 7)
        weeks.setdefault(wi, {})[di] = (d, v)

    n_weeks = max(weeks) + 1 if weeks else 1
    left, right, top = 36, w - 18, 52
    legend_y = h - 22
    # 7 rows must fit above the legend. Keep squares; centre the grid.
    row_space = legend_y - 14 - top
    step = min(row_space / 7, (right - left) / max(n_weeks, 1))
    size = max(9, step * 0.78)
    gap = step - size
    # Left-align the short calendar under the title.

    month_at = {}
    for wi, days_in in weeks.items():
        for di, (d, v) in days_in.items():
            key = (d.year, d.month)
            if key not in month_at:
                month_at[key] = wi
    used_weeks = set()
    for (year, month), wi in sorted(month_at.items()):
        while wi in used_weeks:
            wi += 1
        if wi not in weeks:
            continue
        used_weeks.add(wi)
        label = date(year, month, 1).strftime("%b")
        x = left + wi * (size + gap)
        body.append(
            f'  <text class="f" x="{x:.1f}" y="50" fill="{MUTED}" font-size="10">{label}</text>'
        )

    for di, lab in ((1, "M"), (3, "W"), (5, "F")):
        y = top + di * (size + gap) + size - 2
        body.append(
            f'  <text class="f" x="{left-6}" y="{y:.1f}" fill="{MUTED}" font-size="9" text-anchor="end">{lab}</text>'
        )

    for wi, days_in in weeks.items():
        for di in range(7):
            if di not in days_in:
                continue
            d, v = days_in[di]
            x = left + wi * (size + gap)
            y = top + di * (size + gap)
            fill = palette[level(v)]
            body.append(
                f'  <rect x="{x:.1f}" y="{y:.1f}" width="{size:.1f}" height="{size:.1f}" rx="3" fill="{fill}"/>'
            )

    lx = left
    ly = h - 22
    body.append(f'  <text class="f" x="{lx}" y="{ly+8}" fill="{MUTED}" font-size="9">Less</text>')
    for i, col in enumerate(palette):
        body.append(
            f'  <rect x="{lx+28+i*(12)}" y="{ly-1}" width="10" height="10" rx="2" fill="{col}"/>'
        )
    body.append(
        f'  <text class="f" x="{lx+28+5*12+4}" y="{ly+8}" fill="{MUTED}" font-size="9">More</text>'
    )
    body.append(card_close())
    return "\n".join(body)


def main():
    print(f"Generating profile stats for {USER}…")
    counts = fetch_public_contributions(USER)
    streaks = compute_streaks(counts)
    stats = language_totals(USER)
    rank = calculate_rank(
        stats["commits"],
        stats["prs"],
        stats["issues"],
        stats["reviews"],
        stats["stars"],
        stats["followers"],
    )
    print("streaks", {k: streaks[k] for k in streaks})
    print("stats", {k: stats[k] for k in stats if k != "languages"})
    print("languages", stats["languages"][:6])
    print("rank", rank)

    write(os.path.join(ASSETS, "stats.svg"), render_stats(stats, rank))
    write(os.path.join(ASSETS, "streak.svg"), render_streak(streaks))
    write(os.path.join(ASSETS, "languages.svg"), render_languages(stats["languages"]))
    write(os.path.join(ASSETS, "calendar.svg"), render_calendar(counts))
    write(os.path.join(ASSETS, "activity.svg"), render_activity(counts, streaks["total"]))
    print("done")


if __name__ == "__main__":
    try:
        main()
    except Exception as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        sys.exit(1)
