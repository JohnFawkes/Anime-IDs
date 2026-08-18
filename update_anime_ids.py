import json, os, sys, time
from datetime import datetime, UTC

if sys.version_info[0] != 3 or sys.version_info[1] < 11:
    print("Version Error: Version: %s.%s.%s incompatible please use Python 3.11+" % (sys.version_info[0], sys.version_info[1], sys.version_info[2]))
    sys.exit(0)

try:
    import requests
    from git import Repo
    from lxml import html
    from kometautils import KometaArgs, KometaLogger
except (ModuleNotFoundError, ImportError):
    print("Requirements Error: Requirements are not installed")
    sys.exit(0)

options = [
    {"arg": "tr", "key": "trace",        "env": "TRACE",        "type": "bool", "default": False, "help": "Run with extra trace logs."},
    {"arg": "lr", "key": "log-requests", "env": "LOG_REQUESTS", "type": "bool", "default": False, "help": "Run with every request logged."}
]
script_name = "Anime IDs"
base_dir = os.path.dirname(os.path.abspath(__file__))
args = KometaArgs("Kometa-Team/Anime-IDs", base_dir, options, use_nightly=False)
logger = KometaLogger(script_name, "anime_ids", os.path.join(base_dir, "logs"), is_trace=args["trace"], log_requests=args["log-requests"])
logger.screen_width = 160
logger.header(args, sub=True)
logger.separator()
logger.start()

anime_ids_file = os.path.join(base_dir, "anime_ids.json")
anime_dicts = {}

logger.info("Scanning Anime-Lists")
anidb_url = "https://raw.githubusercontent.com/Anime-Lists/anime-lists/master/anime-list-master.xml"
for anime in html.fromstring(requests.get(anidb_url).content).xpath("//anime"):
    anidb_id = str(anime.xpath("@anidbid")[0])
    if not anidb_id:
        continue
    anidb_id = int(anidb_id[1:]) if anidb_id[0] == "a" else int(anidb_id)
    if anidb_id not in anime_dicts:
        anime_dicts[anidb_id] = {}
    tvdb_id = str(anime.xpath("@tvdbid")[0])
    try:
        if tvdb_id:
            anime_dicts[anidb_id]["tvdb_id"] = int(tvdb_id)
    except ValueError:
        pass
    tvdb_season = str(anime.xpath("@defaulttvdbseason")[0])
    if tvdb_season == "a":
        tvdb_season = "-1"
    try:
        if tvdb_season:
            anime_dicts[anidb_id]["tvdb_season"] = int(tvdb_season)
    except ValueError:
        pass
    try:
        anime_dicts[anidb_id]["tvdb_epoffset"] = int(str(anime.xpath("@episodeoffset")[0]))
    except ValueError:
        anime_dicts[anidb_id]["tvdb_epoffset"] = 0

    imdb_id = str(anime.xpath("@imdbid")[0])
    if imdb_id.startswith("tt"):
        anime_dicts[anidb_id]["imdb_id"] = imdb_id

logger.info("Scanning AnimeAggregations")
aggregations_url = "https://raw.githubusercontent.com/notseteve/AnimeAggregations/main/aggregate/AnimeToExternal.json"
for anidb_id, anime in requests.get(aggregations_url).json()["animes"].items():
    anidb_id = int(anidb_id)
    resources = anime["resources"]
    if anidb_id not in anime_dicts:
        if not any(k in resources for k in ["IMDB", "MAL", "TMDB"]):
            continue
        anime_dicts[anidb_id] = {}
    if "IMDB" in resources and "imdb_id" not in anime_dicts[anidb_id]:
        anime_dicts[anidb_id]["imdb_id"] = ",".join(resources["IMDB"])
    if "MAL" in resources:
        anime_dicts[anidb_id]["mal_id"] = int(resources["MAL"][0]) if len(resources["MAL"]) == 1 else ",".join(resources["MAL"])
    if "TMDB" in resources:
        tmdb_tv_id = next((r for r in resources["TMDB"] if r.startswith("tv")), None)
        if tmdb_tv_id:
            anime_dicts[anidb_id]["tmdb_show_id"] = int(tmdb_tv_id[3:])
        else:
            tmdb_movie_ids = [r[6:] for r in resources["TMDB"] if r.startswith("movie")]
            anime_dicts[anidb_id]["tmdb_movie_id"] = int(tmdb_movie_ids[0]) if len(tmdb_movie_ids) == 1 else ",".join(tmdb_movie_ids)

logger.info("Scanning Anime ID Edits")
edited_anilist_ids = set()
with open(os.path.join(base_dir, "anime_id_edits.json"), "r") as f:
    for anidb_id, ids in json.load(f).items():
        anidb_id = int(anidb_id)
        if anidb_id in anime_dicts:
            for attr in ["tvdb_id", "mal_id", "anilist_id", "imdb_id", "tmdb_show_id", "tmdb_movie_id"]:
                if attr in ids:
                    anime_dicts[anidb_id][attr] = ids[attr]
            if "anilist_id" in ids:
                edited_anilist_ids.add(anidb_id)


def split_ids(value):
    return [i.strip() for i in str(value).split(",") if i.strip().isdigit()]


logger.info("Scanning AniList")
anilist_url = "https://graphql.anilist.co"
anilist_query = """
query ($mal_ids: [Int]) {
  Page(page: 1, perPage: 50) {
    media(idMal_in: $mal_ids, type: ANIME) {
      id
      idMal
    }
  }
}
"""

# AniList only exposes the MyAnimeList ID it is mapped to, so the previous run's
# output doubles as the MyAnimeList ID -> AniList ID cache. Without it every run
# would have to re-query all ~15,000 IDs against a 30 request per minute limit.
anilist_ids = {}
if os.path.exists(anime_ids_file):
    with open(anime_ids_file, "r") as f:
        for ids in json.load(f).values():
            if "mal_id" not in ids or "anilist_id" not in ids:
                continue
            cached_mal_ids = split_ids(ids["mal_id"])
            cached_anilist_ids = split_ids(ids["anilist_id"])
            if len(cached_mal_ids) != len(cached_anilist_ids):
                continue
            for mal_id, anilist_id in zip(cached_mal_ids, cached_anilist_ids):
                anilist_ids[int(mal_id)] = int(anilist_id)
logger.info(f"{len(anilist_ids)} MyAnimeList IDs cached from the previous run")

lookup_ids = sorted({
    int(mal_id)
    for ids in anime_dicts.values() if "mal_id" in ids
    for mal_id in split_ids(ids["mal_id"]) if int(mal_id) not in anilist_ids
})
logger.info(f"{len(lookup_ids)} MyAnimeList IDs to look up on AniList")

for i in range(0, len(lookup_ids), 50):
    batch = lookup_ids[i:i + 50]
    response = None
    for attempt in range(1, 6):
        try:
            response = requests.post(anilist_url, json={"query": anilist_query, "variables": {"mal_ids": batch}})
            if response.status_code == 429:
                wait = int(response.headers.get("Retry-After", 60)) + 1
                logger.warning(f"AniList Rate Limit Reached: Waiting {wait} seconds")
                time.sleep(wait)
                response = None
                continue
            response.raise_for_status()
            break
        except requests.RequestException as e:
            logger.warning(f"AniList Request Failed (Attempt {attempt}/5): {e}")
            response = None
            time.sleep(attempt * 5)
    if response is None:
        logger.error(f"AniList Error: Skipping MyAnimeList IDs {batch[0]}-{batch[-1]}")
        continue
    for media in response.json()["data"]["Page"]["media"]:
        if media["idMal"]:
            anilist_ids[int(media["idMal"])] = int(media["id"])
    looked_up = min(i + 50, len(lookup_ids))
    if looked_up % 1000 < 50 or looked_up == len(lookup_ids):
        logger.info(f"Looked up {looked_up}/{len(lookup_ids)} MyAnimeList IDs")
    if int(response.headers.get("X-RateLimit-Remaining", 1) or 0) < 1:
        logger.info("AniList Rate Limit Reached: Waiting 61 seconds")
        time.sleep(61)

for anidb_id, ids in anime_dicts.items():
    if "mal_id" not in ids or anidb_id in edited_anilist_ids:
        continue
    found_ids = [anilist_ids[int(mal_id)] for mal_id in split_ids(ids["mal_id"]) if int(mal_id) in anilist_ids]
    if found_ids:
        ids["anilist_id"] = found_ids[0] if len(found_ids) == 1 else ",".join(str(i) for i in found_ids)

with open(anime_ids_file, "w") as write:
    json.dump(anime_dicts, write, indent=2)

logger.separator()

if [item.a_path for item in Repo(path=".").index.diff(None) if item.a_path.endswith(".json")]:

    logger.info("Saving Anime ID Changes")

    with open("README.md", "r") as f:
        data = f.readlines()

    data[2] = f"Last generated at: {datetime.now(UTC).strftime('%B %d, %Y %I:%M %p')} UTC\n"

    with open("README.md", "w") as f:
        f.writelines(data)
else:
    logger.info("No Anime ID Changes Detected")

logger.separator(f"{script_name} Finished\nTotal Runtime: {logger.runtime()}")
