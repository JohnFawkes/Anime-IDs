import json, os, sys
from datetime import datetime, UTC

if sys.version_info[0] != 3 or sys.version_info[1] < 11:
    print("Version Error: Version: %s.%s.%s incompatible please use Python 3.11+" % (sys.version_info[0], sys.version_info[1], sys.version_info[2]))
    sys.exit(0)

try:
    import requests
    from git import Repo
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

anime_dicts = {}

logger.info("Scanning AnimeMap")
animemap_url = "https://mapping.animemap.dev/api/v1/export.json"
response = requests.get(animemap_url)
response.raise_for_status()
export = response.json()
logger.info(f"AnimeMap Export Generated At: {export['generated_at']}")
logger.info(f"AnimeMap Entries: {export['count']}")

# AnimeMap is keyed on AniList ID while anime_ids.json is keyed on AniDB ID, and
# several AniList entries can share one AniDB ID, so the entries are grouped
# before anything is written out.
anidb_groups = {}
for entry in sorted(export["entries"], key=lambda e: e["anilist_id"]):
    anidb = entry["anidb"]
    if not anidb or anidb.get("id") is None:
        continue
    anidb_groups.setdefault(int(anidb["id"]), []).append(entry)
logger.info(f"{len(anidb_groups)} AniDB IDs mapped")


def resource_id(entry, source):
    resource = entry[source]
    return resource["id"] if resource and resource.get("id") is not None else None


def join_ids(values):
    found = []
    for value in values:
        if value is not None and value not in found:
            found.append(value)
    if not found:
        return None
    return found[0] if len(found) == 1 else ",".join(str(value) for value in found)


for anidb_id, group in sorted(anidb_groups.items()):
    ids = {}

    tvdb = next((entry["tvdb"] for entry in group if resource_id(entry, "tvdb") is not None), None)
    if tvdb:
        ids["tvdb_id"] = int(tvdb["id"])
        tvdb_season = tvdb.get("season")
        if tvdb_season is not None:
            ids["tvdb_season"] = -1 if str(tvdb_season) == "a" else int(tvdb_season)
    ids["tvdb_epoffset"] = int(tvdb["episode_offset"] or 0) if tvdb else 0

    imdb_id = join_ids([resource_id(entry, "imdb") for entry in group])
    if imdb_id is not None:
        ids["imdb_id"] = imdb_id

    mal_id = join_ids([resource_id(entry, "mal") for entry in group])
    if mal_id is not None:
        ids["mal_id"] = mal_id

    anilist_id = join_ids([entry["anilist_id"] for entry in group])
    if anilist_id is not None:
        ids["anilist_id"] = anilist_id

    tmdb_show_id = join_ids([resource_id(entry, "tmdb") for entry in group if entry["tmdb"] and entry["tmdb"].get("media_type") == "tv"])
    if tmdb_show_id is not None:
        ids["tmdb_show_id"] = tmdb_show_id

    tmdb_movie_id = join_ids([resource_id(entry, "tmdb") for entry in group if entry["tmdb"] and entry["tmdb"].get("media_type") == "movie"])
    if tmdb_movie_id is not None:
        ids["tmdb_movie_id"] = tmdb_movie_id

    anime_dicts[anidb_id] = ids

logger.info("Scanning Anime ID Edits")
with open(os.path.join(base_dir, "anime_id_edits.json"), "r") as f:
    for anidb_id, ids in json.load(f).items():
        anidb_id = int(anidb_id)
        if anidb_id in anime_dicts:
            for attr in ["tvdb_id", "mal_id", "anilist_id", "imdb_id", "tmdb_show_id", "tmdb_movie_id"]:
                if attr in ids:
                    anime_dicts[anidb_id][attr] = ids[attr]

with open(os.path.join(base_dir, "anime_ids.json"), "w") as write:
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
