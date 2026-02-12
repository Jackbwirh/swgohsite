from flask import Flask, render_template, request, Response
import asyncio
import json
from crawl4ai import AsyncWebCrawler
from bs4 import BeautifulSoup
from collections import defaultdict
import os

app = Flask(__name__)

MAX_MATCHES = 6
TOP_LIMIT = 10


# -------------------------------------------------------
# GET MOST RECENT MATCH ENDINGS
# -------------------------------------------------------
async def get_gac_match_endings(player_id):
    url = f"https://swgoh.gg/p/{player_id}/gac-history/"

    async with AsyncWebCrawler(verbose=False) as crawler:
        result = await crawler.arun(
            url=url,
            wait_until="networkidle",
            delay_before_return_html=2
        )

    html = getattr(result, "html", "") or result.markdown
    soup = BeautifulSoup(html, "html.parser")

    tags = soup.select("a.d-block.brighten-on-hover.text-white")

    endings = []
    for tag in tags:
        href = tag.get("href", "")
        part = href.replace(f"/p/{player_id}/gac-history/", "").strip("/")
        endings.append(part)

    return endings[:MAX_MATCHES]


# -------------------------------------------------------
# PARSE OFFENSE WINS & LOSSES
# -------------------------------------------------------
def extract_offense_battles(html):
    soup = BeautifulSoup(html, "html.parser")

    attack_section = soup.find("div", id="battles-attack")
    if not attack_section:
        return [], []

    battle_wrappers = attack_section.select(
        ".paper.mt-2.paper--positive, .paper.mt-2.paper--negative"
    )

    wins_raw = []
    losses_raw = []

    for wrapper in battle_wrappers:
        is_win = "paper--positive" in wrapper.get("class", [])
        containers = wrapper.select(
            ".d-flex.col-gap-2.align-items-center.justify-content-md-center.justify-content-lg-start"
        )

        results_here = []

        for c in containers:
            inner_divs = c.find_all("div", class_=False)
            for d in inner_divs:
                text = d.get_text(strip=True)
                if text:
                    results_here.append(text)

        filtered = [txt for i, txt in enumerate(results_here) if i % 2 == 1]

        if is_win:
            wins_raw.extend(filtered)
        else:
            losses_raw.extend(filtered)

    return wins_raw, losses_raw


# -------------------------------------------------------
# STREAM ANALYSIS WITH DETAILED PROGRESS
# -------------------------------------------------------
@app.route("/analyze")
def analyze():
    player_id = request.args.get("player_id", "").strip()

    if not player_id:
        return Response(
            f"data: {json.dumps({'done': True, 'error': 'Player ID cannot be empty'})}\n\n",
            mimetype="text/event-stream"
        )

    def generate():

        yield f"data: {json.dumps({'progress': 5})}\n\n"

        try:
            endings = asyncio.run(get_gac_match_endings(player_id))
        except Exception:
            yield f"data: {json.dumps({'done': True, 'error': 'Failed to fetch match endings'})}\n\n"
            return

        if not endings:
            yield f"data: {json.dumps({'done': True, 'error': 'No matches found'})}\n\n"
            return

        total = len(endings)
        win_totals = defaultdict(int)
        loss_totals = defaultdict(int)

        yield f"data: {json.dumps({'progress': 15})}\n\n"

        for i, ending in enumerate(endings):

            url = f"https://swgoh.gg/p/{player_id}/gac-history/{ending}/"

            async def fetch_match():
                async with AsyncWebCrawler(verbose=False) as crawler:
                    result = await crawler.arun(
                        url=url,
                        wait_until="networkidle",
                        delay_before_return_html=2
                    )
                return getattr(result, "html", "") or result.markdown

            try:
                html = asyncio.run(fetch_match())
            except Exception:
                html = ""

            wins, losses = extract_offense_battles(html)

            for w in wins:
                win_totals[w] += 1

            for l in losses:
                loss_totals[l] += 1

            # Progress distributed between 15% and 95%
            progress = 15 + int(((i + 1) / total) * 80)
            yield f"data: {json.dumps({'progress': progress})}\n\n"

        # Sort and cut to top 10
        sorted_wins = dict(
            sorted(win_totals.items(), key=lambda x: x[1], reverse=True)[:TOP_LIMIT]
        )

        sorted_losses = dict(
            sorted(loss_totals.items(), key=lambda x: x[1], reverse=True)[:TOP_LIMIT]
        )

        final_data = {
            "done": True,
            "wins": sorted_wins,
            "losses": sorted_losses
        }

        yield f"data: {json.dumps({'progress': 100})}\n\n"
        yield f"data: {json.dumps(final_data)}\n\n"

    return Response(
        generate(),
        mimetype="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "X-Accel-Buffering": "no"
        }
    )


@app.route("/")
def home():
    return render_template("index.html")


if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5000))  # Render assigns PORT
    app.run(host="0.0.0.0", port=port, debug=True, threaded=True)
