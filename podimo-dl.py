from gql import gql, Client
from gql.transport.aiohttp import AIOHTTPTransport
import urllib.request
import argparse
import importlib
import os
import re
import shutil
import subprocess
import eyed3
from pathlib import Path
from mutations import *
from queries import *

UUID_RE = re.compile(
    r"^[0-9a-fA-F]{8}-"
    r"[0-9a-fA-F]{4}-"
    r"[0-9a-fA-F]{4}-"
    r"[0-9a-fA-F]{4}-"
    r"[0-9a-fA-F]{12}$"
)


def download_direct(url, fn, debug=False):
    if debug:
        print(f"  [debug] direct download: {url}")
    with urllib.request.urlopen(url) as resp, open(fn, 'wb') as output:
        output.write(resp.read())


def download_hls(url, fn, debug=False):
    """Download an HLS (.m3u8) stream and mux it to MP3 using ffmpeg."""
    ffmpeg = shutil.which('ffmpeg')
    if not ffmpeg:
        raise RuntimeError(
            "ffmpeg is required to download HLS (.m3u8) audio but was not found on "
            "PATH. Install it from https://ffmpeg.org/download.html"
        )
    cmd = [
        ffmpeg, '-y', '-loglevel', 'error',
        '-headers',
        'User-Agent: Mozilla/5.0 (Windows NT 10.0; Win64; x64)\r\n'
        'Referer: https://open.podimo.com/\r\n',
        '-i', url,
        '-vn',
        '-acodec', 'libmp3lame',
        '-q:a', '2',
        fn,
    ]
    if debug:
        print(f"  [debug] ffmpeg: {' '.join(cmd)}")
    subprocess.run(cmd, check=True)


def download_video(url, fn, debug=False):
    """Download video URL and normalize output to MP4 (H.264/AAC)."""
    ffmpeg = shutil.which('ffmpeg')
    if not ffmpeg:
        raise RuntimeError(
            "ffmpeg is required to download video but was not found on PATH. "
            "Install it from https://ffmpeg.org/download.html"
        )
    cmd = [
        ffmpeg, '-y', '-loglevel', 'error',
        '-headers',
        'User-Agent: Mozilla/5.0 (Windows NT 10.0; Win64; x64)\r\n'
        'Referer: https://open.podimo.com/\r\n',
        '-i', url,
        '-map', '0:v:0',
        '-map', '0:a:0?',
        '-c:v', 'libx264',
        '-c:a', 'aac',
        '-movflags', '+faststart',
        fn,
    ]
    if debug:
        print(f"  [debug] ffmpeg: {' '.join(cmd)}")
    subprocess.run(cmd, check=True)


class PodimoAPI:
    def __init__(self):
        self.transport1 = AIOHTTPTransport(url="https://studio.podimo.com/graphql")
        self.transport2 = AIOHTTPTransport(url="https://podimo.com/graphql")
        self.studio_client = Client(transport=self.transport1, fetch_schema_from_transport=False)
        self.public_client = Client(transport=self.transport2, fetch_schema_from_transport=False)

    def login(self, email, password, is_creator):
        if is_creator:
            result = self.studio_client.execute(queryTokenWithCredentials, variable_values={
                'email': email,
                'password': password,
                'scope': 'CREATOR',
            })
        else:
            result = self.public_client.execute(queryTokenWithCredentials, variable_values={
                'email': email,
                'password': password,
                'scope': 'MOBILE',
            })

        token = result["tokenWithCredentials"]["token"]
        self.transport1.headers = self.transport2.headers = {
            'accept': '*/*',
            'user-os': 'android',
            'user-locale': 'en-US',
            'user-agent': 'okhttp/4.9.1',
            'authorization': token,
        }

    def search_podcast(self, search_query, region="de"):
        result = self.public_client.execute(queryUsePodcastsExistQuery, variable_values={
            'search': search_query,
        })
        search_result = [r for r in result["podcastsAutocomplete"] if search_query in r["title"]]
        if len(search_result) > 0:
            return search_result[0]
        return None

    def get_podcast_episodes(self, podcast_id):
        episodes = []
        batch = 100
        offset = 0
        while True:
            result = self.public_client.execute(queryPodcastEpisodes, variable_values={
                'podcastId': podcast_id,
                'limit': batch,
                'offset': offset,
            })
            page = result["podcastEpisodes"]
            episodes.extend(page)
            if len(page) < batch:
                break
            offset += batch
        return episodes


    def get_playback_track(self, podcast_episode):
        """Resolve typed playback track data for an episode."""
        result = self.public_client.execute(queryPlaybackByPodcastEpisode, variable_values={
            'podcastId': podcast_episode["podcastId"],
            'episodeId': podcast_episode["id"],
        })
        playback = result.get("playbackByPodcastEpisode") or {}
        track = playback.get("track") or {}
        return {
            "typename": track.get("__typename"),
            "url": track.get("url"),
        }

    def _build_filename(self, name, media_type, output_folder=None):
        sane_name = "".join([c for c in name if c.isalpha() or c.isdigit() or c in ' #']).rstrip()
        suffix = "[audio]" if media_type == "audio" else "[video]"
        ext = "mp3" if media_type == "audio" else "mp4"
        fn = f"{sane_name} {suffix}.{ext}"
        if output_folder:
            fn = f'{output_folder}/{fn}'
        return fn

    def resolve_episode_media(self, podcast_episode, debug=False):
        media = []
        direct_audio_url = podcast_episode.get("audioUrl")
        if direct_audio_url:
            media.append({
                "media_type": "audio",
                "url": direct_audio_url,
                "source": "audioUrl",
            })

        try:
            playback_track = self.get_playback_track(podcast_episode)
        except Exception as exc:  # noqa: BLE001
            if debug:
                print(f"  [debug] playback resolver failed: {exc}")
            playback_track = None

        if playback_track and playback_track.get("url"):
            track_type = playback_track.get("typename")
            playback_url = playback_track["url"]
            media.append({
                "media_type": "video" if track_type == "VideoMediaTrack" else "audio",
                "url": playback_track["url"],
                "source": "playbackByPodcastEpisode",
            })
            if track_type == "VideoMediaTrack":
                media.append({
                    "media_type": "audio",
                    "url": playback_url,
                    "source": "playbackByPodcastEpisode.videoAudioFallback",
                })

        deduped = []
        seen = set()
        for item in media:
            key = (item["media_type"], item["url"])
            if key in seen:
                continue
            seen.add(key)
            deduped.append(item)
        return deduped

    def _pick_media(self, media_candidates, media_type):
        same_type = [m for m in media_candidates if m["media_type"] == media_type]
        if not same_type:
            return None
        if media_type == "audio":
            direct = [m for m in same_type if m["source"] == "audioUrl"]
            if direct:
                return direct[0]
        return same_type[0]

    def download_episode(self, podcast_episode, output_folder=None, overwrite=False, debug=False, download_mode="both"):
        name = podcast_episode["title"]
        media_candidates = self.resolve_episode_media(podcast_episode, debug=debug)

        if download_mode == "audio":
            requested_types = ["audio"]
        elif download_mode == "video":
            requested_types = ["video"]
        else:
            requested_types = ["audio", "video"]

        downloads = []
        for media_type in requested_types:
            selected = self._pick_media(media_candidates, media_type)
            if not selected:
                print(f"Skipping {name} [{media_type}]. No playable URL available.")
                continue

            url = selected["url"]
            fn = self._build_filename(name, media_type, output_folder=output_folder)
            if not overwrite and os.path.exists(fn):
                print(f"Skipping {name} [{media_type}]. File already exists. (Overwrite with --overwrite)")
                downloads.append({"media_type": media_type, "path": fn, "skipped": True})
                continue

            print(f"Downloading {name} [{media_type}] from {url}...")
            try:
                if media_type == "audio":
                    if ".m3u8" in url:
                        download_hls(url, fn, debug=debug)
                    else:
                        download_direct(url, fn, debug=debug)
                else:
                    download_video(url, fn, debug=debug)
            except Exception as exc:  # noqa: BLE001
                print(f"Failed to download {name} [{media_type}]: {exc}")
                continue

            downloads.append({"media_type": media_type, "path": fn, "skipped": False})

        if not downloads:
            print(f"Skipping {name}. No media downloaded.")
            return []
        return downloads

def do_it_question(question):
    try:
        xy = input(question + " (Y/n) > ")
    except EOFError:
        print("\nNo interactive input available. Use config.py or pass --config.")
        return False
    return not xy.lower() == "n"

def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("-p", "--podcast", )
    parser.add_argument("-c", "--config", action="store_true")
    parser.add_argument("-d", "--download", action="store_true")
    parser.add_argument("--premium-only", action="store_true")
    parser.add_argument("--overwrite", action="store_true")
    parser.add_argument("--debug", action="store_true")
    parser.add_argument("--audio", action="store_true", help="Download audio only (strict mode)")
    parser.add_argument("--video", action="store_true", help="Download video only (strict mode)")
    parser.add_argument("-o", "--output")
    args = parser.parse_args()

    use_config = (args.config and os.path.exists("config.py")) or do_it_question("Use config.py?")
    if use_config:
        app_config = importlib.import_module("config")
        email = app_config.email
        password = app_config.password
        is_creator = app_config.is_creator
        configured_podcast_searches = getattr(app_config, "podcast_searches", None)
    else:
        try:
            email = input("Email > ")
            password = input("Password > ")
        except EOFError:
            print("\nNo interactive input available. Use config.py or pass --config.")
            return
        is_creator = False
        configured_podcast_searches = None

    podimo = PodimoAPI()
    podimo.login(email, password, is_creator)

    if args.podcast:
        search_strings = [args.podcast]
    elif configured_podcast_searches:
        if isinstance(configured_podcast_searches, str):
            search_strings = [configured_podcast_searches]
        else:
            search_strings = [s for s in configured_podcast_searches if s]
    else:
        try:
            search_strings = [input("Podcast Name > ")]
        except EOFError:
            print("\nNo podcast name provided. Use -p/--podcast or config.py podcast_searches.")
            return

    if not search_strings:
        print("No podcast names configured. Set podcast_searches in config.py or use -p/--podcast.")
        return

    if args.audio and not args.video:
        download_mode = "audio"
    elif args.video and not args.audio:
        download_mode = "video"
    else:
        download_mode = "both"

    should_download = args.download or do_it_question("Download Episodes?")
    for search_string in search_strings:
        print(f"\n=== Podcast query: {search_string} ===")
        if UUID_RE.match(search_string):
            podcast_id = search_string
        else:
            match = podimo.search_podcast(search_string)
            podcast_id = match["id"] if match else None

        if not podcast_id:
            print("Not found!")
            continue

        episodes = podimo.get_podcast_episodes(podcast_id)
        if args.premium_only:
            episodes = [x for x in filter(lambda e: e["accessLevel"] == "PREMIUM", episodes)]

        for e in episodes:
            print(" > ", e["title"])

        if not should_download:
            continue

        for e in episodes:
            files = podimo.download_episode(
                e,
                args.output,
                args.overwrite,
                debug=args.debug,
                download_mode=download_mode,
            )
            if not files:
                continue

            title_with_track_num = re.match(r"^#?([0-9]{1,}) (.+)", e["title"])
            if title_with_track_num:
                track_num, title = title_with_track_num.groups()
            else:
                track_num, title = (0, e["title"])

            for file_info in files:
                if file_info["media_type"] != "audio":
                    continue
                fn = file_info["path"]
                if file_info["skipped"]:
                    continue
                audiofile = eyed3.load(fn)
                audiofile.tag.title = title
                audiofile.tag.publisher = "Podimo"
                audiofile.tag.comments.set(e["description"])
                audiofile.tag.original_release_date = audiofile.tag.release_date = e["datetime"].replace(".000Z", "Z").split("T")[0]
                audiofile.tag.track_num = track_num
                audiofile.tag.save(version=eyed3.id3.ID3_V2_4)

if __name__ == '__main__':
    main()
