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
    {"arg": "lr", "key": "log-requests", "env": "LOG_REQUESTS", "type": "bool", "default": False, "help": "Run with every request logged."},
    {"arg": "fb", "key": "fresh-build",  "env": "FRESH_BUILD",  "type": "bool", "default": False, "help": "Ignore the existing anime_ids.json and look every AniList ID up again."},
    {"arg": "ie", "key": "ignore-edits", "env": "IGNORE_EDITS", "type": "bool", "default": False, "help": "Ignore anime_id_edits.json so the output is only what the sources produce."}
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

# AnimeMap is the authority for every ID it has. Anime-Lists and AnimeAggregations
# only fill in AniDB IDs that AnimeMap has no entry for, and an ID AnimeMap already
# handed to one AniDB entry is never handed to another, so reverse lookups
# (MyAnimeList/AniList/TMDb -> AniDB) stay unambiguous.
ID_ATTRS = ["mal_id", "anilist_id", "imdb_id", "tmdb_show_id", "tmdb_movie_id"]
anime_dicts = {}
fallback_dicts = {}
claimed_ids = {attr: set() for attr in ID_ATTRS}


def split_ids(value):
    return [i.strip() for i in str(value).split(",") if i.strip()]


def claim(attr, values):
    found = []
    for value in values:
        value = str(value)
        if value not in found:
            found.append(value)
    if not found:
        return None
    claimed_ids[attr].update(found)
    return int(found[0]) if len(found) == 1 and found[0].isdigit() else found[0] if len(found) == 1 else ",".join(found)


logger.info("Scanning AnimeMap")
animemap_url = "https://mapping.animemap.dev/api/v1/export.json"
response = requests.get(animemap_url)
response.raise_for_status()
export = response.json()
logger.info(f"AnimeMap Export Generated At: {export['generated_at']}")
logger.info(f"AnimeMap Entries: {export['count']}")

# AnimeMap is keyed on AniList ID while anime_ids.json is keyed on AniDB ID, and
# several AniList entries can share one AniDB ID, so entries are grouped first.
anidb_groups = {}
for entry in sorted(export["entries"], key=lambda e: e["anilist_id"]):
    anidb = entry["anidb"]
    if not anidb or anidb.get("id") is None:
        continue
    anidb_groups.setdefault(int(anidb["id"]), []).append(entry)


def resource_id(entry, source):
    resource = entry[source]
    return resource["id"] if resource and resource.get("id") is not None else None


for anidb_id, group in sorted(anidb_groups.items()):
    ids = {}

    tvdb = next((entry["tvdb"] for entry in group if resource_id(entry, "tvdb") is not None), None)
    if tvdb:
        ids["tvdb_id"] = int(tvdb["id"])
        tvdb_season = tvdb.get("season")
        if tvdb_season is not None:
            ids["tvdb_season"] = -1 if str(tvdb_season) == "a" else int(tvdb_season)
    ids["tvdb_epoffset"] = int(tvdb["episode_offset"] or 0) if tvdb else 0

    for attr, values in [
        ("imdb_id", [resource_id(entry, "imdb") for entry in group]),
        ("mal_id", [resource_id(entry, "mal") for entry in group]),
        ("anilist_id", [entry["anilist_id"] for entry in group]),
        ("tmdb_show_id", [resource_id(entry, "tmdb") for entry in group if entry["tmdb"] and entry["tmdb"].get("media_type") == "tv"]),
        ("tmdb_movie_id", [resource_id(entry, "tmdb") for entry in group if entry["tmdb"] and entry["tmdb"].get("media_type") == "movie"]),
    ]:
        value = claim(attr, [v for v in values if v is not None])
        if value is not None:
            ids[attr] = value

    anime_dicts[anidb_id] = ids
logger.info(f"{len(anime_dicts)} AniDB IDs mapped by AnimeMap")

logger.info("Scanning Anime-Lists")
anidb_url = "https://raw.githubusercontent.com/Anime-Lists/anime-lists/master/anime-list-master.xml"
for anime in html.fromstring(requests.get(anidb_url).content).xpath("//anime"):
    anidb_id = str(anime.xpath("@anidbid")[0])
    if not anidb_id:
        continue
    anidb_id = int(anidb_id[1:]) if anidb_id[0] == "a" else int(anidb_id)
    if anidb_id in anime_dicts:
        continue
    if anidb_id not in fallback_dicts:
        fallback_dicts[anidb_id] = {}
    tvdb_id = str(anime.xpath("@tvdbid")[0])
    try:
        if tvdb_id:
            fallback_dicts[anidb_id]["tvdb_id"] = int(tvdb_id)
    except ValueError:
        pass
    tvdb_season = str(anime.xpath("@defaulttvdbseason")[0])
    if tvdb_season == "a":
        tvdb_season = "-1"
    try:
        if tvdb_season:
            fallback_dicts[anidb_id]["tvdb_season"] = int(tvdb_season)
    except ValueError:
        pass
    try:
        fallback_dicts[anidb_id]["tvdb_epoffset"] = int(str(anime.xpath("@episodeoffset")[0]))
    except ValueError:
        fallback_dicts[anidb_id]["tvdb_epoffset"] = 0

    imdb_id = str(anime.xpath("@imdbid")[0])
    if imdb_id.startswith("tt"):
        fallback_dicts[anidb_id]["imdb_id"] = imdb_id

logger.info("Scanning AnimeAggregations")
aggregations_url = "https://raw.githubusercontent.com/notseteve/AnimeAggregations/main/aggregate/AnimeToExternal.json"
for anidb_id, anime in requests.get(aggregations_url).json()["animes"].items():
    anidb_id = int(anidb_id)
    if anidb_id in anime_dicts:
        continue
    resources = anime["resources"]
    if anidb_id not in fallback_dicts:
        if not any(k in resources for k in ["IMDB", "MAL", "TMDB"]):
            continue
        fallback_dicts[anidb_id] = {}
    if "IMDB" in resources and "imdb_id" not in fallback_dicts[anidb_id]:
        fallback_dicts[anidb_id]["imdb_id"] = ",".join(resources["IMDB"])
    if "MAL" in resources:
        fallback_dicts[anidb_id]["mal_id"] = int(resources["MAL"][0]) if len(resources["MAL"]) == 1 else ",".join(resources["MAL"])
    if "TMDB" in resources:
        tmdb_tv_id = next((r for r in resources["TMDB"] if r.startswith("tv")), None)
        if tmdb_tv_id:
            fallback_dicts[anidb_id]["tmdb_show_id"] = int(tmdb_tv_id[3:])
        else:
            tmdb_movie_ids = [r[6:] for r in resources["TMDB"] if r.startswith("movie")]
            fallback_dicts[anidb_id]["tmdb_movie_id"] = int(tmdb_movie_ids[0]) if len(tmdb_movie_ids) == 1 else ",".join(tmdb_movie_ids)
logger.info(f"{len(fallback_dicts)} AniDB IDs filled in from Anime-Lists and AnimeAggregations")

logger.info("Merging Sources")
for anidb_id, fallback_ids in sorted(fallback_dicts.items()):
    ids = {}
    for attr, value in fallback_ids.items():
        if attr not in ID_ATTRS:
            ids[attr] = value
            continue
        value = claim(attr, [v for v in split_ids(value) if v not in claimed_ids[attr]])
        if value is not None:
            ids[attr] = value
    anime_dicts[anidb_id] = ids
logger.info(f"{len(anime_dicts)} AniDB IDs mapped")

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

# AnimeMap already carries an AniList ID for every entry it has, so only the
# filled-in AniDB IDs need looking up, and only against the MyAnimeList IDs they
# still hold after the merge. AniList only exposes the MyAnimeList ID it is mapped
# to, so the previous run's output doubles as the MyAnimeList ID -> AniList ID
# cache to stay well inside a 30 request per minute limit.
anilist_ids = {}
if args["fresh-build"]:
    logger.info("Fresh Build: Ignoring the cached MyAnimeList IDs from the previous run")
elif os.path.exists(anime_ids_file):
    with open(anime_ids_file, "r") as f:
        for ids in json.load(f).values():
            if "mal_id" not in ids or "anilist_id" not in ids:
                continue
            cached_mal_ids = [i for i in split_ids(ids["mal_id"]) if i.isdigit()]
            cached_anilist_ids = [i for i in split_ids(ids["anilist_id"]) if i.isdigit()]
            if len(cached_mal_ids) != len(cached_anilist_ids):
                continue
            for mal_id, anilist_id in zip(cached_mal_ids, cached_anilist_ids):
                anilist_ids[int(mal_id)] = int(anilist_id)
logger.info(f"{len(anilist_ids)} MyAnimeList IDs cached from the previous run")

lookup_ids = sorted({
    int(mal_id)
    for anidb_id in fallback_dicts if "mal_id" in anime_dicts[anidb_id]
    for mal_id in split_ids(anime_dicts[anidb_id]["mal_id"]) if mal_id.isdigit() and int(mal_id) not in anilist_ids
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

for anidb_id in sorted(fallback_dicts):
    ids = anime_dicts[anidb_id]
    if "mal_id" not in ids:
        continue
    found_ids = [anilist_ids[int(mal_id)] for mal_id in split_ids(ids["mal_id"]) if mal_id.isdigit() and int(mal_id) in anilist_ids]
    anilist_id = claim("anilist_id", [i for i in found_ids if str(i) not in claimed_ids["anilist_id"]])
    if anilist_id is not None:
        ids["anilist_id"] = anilist_id

if args["ignore-edits"]:
    logger.info("Ignoring Anime ID Edits")
else:
    logger.info("Scanning Anime ID Edits")
    with open(os.path.join(base_dir, "anime_id_edits.json"), "r") as f:
        for anidb_id, ids in json.load(f).items():
            anidb_id = int(anidb_id)
            if anidb_id in anime_dicts:
                for attr in ["tvdb_id", "mal_id", "anilist_id", "imdb_id", "tmdb_show_id", "tmdb_movie_id"]:
                    if attr in ids:
                        anime_dicts[anidb_id][attr] = ids[attr]

with open(anime_ids_file, "w") as write:
    json.dump({anidb_id: anime_dicts[anidb_id] for anidb_id in sorted(anime_dicts)}, write, indent=2)

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
