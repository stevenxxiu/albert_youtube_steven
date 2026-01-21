import json
import re
import tempfile
import time
from collections.abc import Generator
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, TypedDict, override
from urllib.parse import urlencode
from urllib.request import Request, urlopen

from albert import openUrl  # pyright: ignore[reportUnknownVariableType]
from albert import setClipboardText  # pyright: ignore[reportUnknownVariableType]
from albert import (
    Action,
    GeneratorQueryHandler,
    Icon,
    Item,
    PluginInstance,
    QueryContext,
    StandardItem,
)

setClipboardText: Callable[[str], None]
openUrl: Callable[[str], None]

_default_critical: Callable[[str], None] = lambda _: None  # noqa: E731
critical: Callable[[str], None] = globals().get('critical', _default_critical)  # pyright: ignore[reportAny]
_default_info: Callable[[str], None] = lambda _: None  # noqa: E731
info: Callable[[str], None] = globals().get('info', _default_info)  # pyright: ignore[reportAny]

md_iid = '5.0'
md_version = '1.8'
md_name = 'YouTube Steven'
md_description = 'TriggerQuery and open YouTube videos and channels'
md_license = 'MIT'
md_url = 'https://github.com/stevenxxiu/albert_youtube_steven'
md_authors = ['@stevenxxiu']

ICON_PATH = Path(__file__).parent / 'icons/youtube.svg'
DATA_REGEX = re.compile(r'\b(var\s|window\[")ytInitialData("\])?\s*=\s*(.*?)\s*;</script>', re.MULTILINE)

HEADERS = {
    'User-Agent': (
        'Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/108.0.0.0 Safari/537.36'
    )
}


def log_html(html: bytes) -> None:
    log_time = time.strftime('%Y%m%d-%H%M%S')
    log_name = 'albert.plugins.youtube_dump'
    log_path = Path(f'/tmp/{log_name}-{log_time}.html')

    with log_path.open('wb') as sr:
        _ = sr.write(html)

    critical(f'The HTML output has been dumped to {log_path}')
    critical('If the page looks ok in a browser, please include the dump in a new issue:')
    critical('  https://www.github.com/albertlauncher/albert/issues/new')


def urlopen_with_headers(url: str) -> Any:  # pyright: ignore[reportAny, reportExplicitAny]
    req = Request(headers=HEADERS, url=url)
    return urlopen(req)  # pyright: ignore[reportAny]


def text_from(val: dict[str, Any]) -> str:  # pyright: ignore[reportExplicitAny]
    text = val['simpleText'] if 'runs' not in val else ''.join(str(v['text']) for v in val['runs'])  # pyright: ignore[reportAny]

    return text.strip()


@dataclass
class ItemData:
    title: str
    subtext: str
    url: str
    action_name: str
    icon_url: str | None
    icon_path: Path | None


def download_item_icon(item_data: ItemData, temp_dir: Path) -> None:
    if item_data.icon_url is None:
        return
    video_id = item_data.icon_url.split('/')[-2]
    item_data.icon_path = temp_dir / f'{video_id}.png'
    with urlopen_with_headers(item_data.icon_url) as response, item_data.icon_path.open('wb') as sr:  # pyright: ignore[reportAny]
        _ = sr.write(response.read())  # pyright: ignore[reportAny]


class YtThumbnail(TypedDict):
    url: str


class YtThumbnailRes(TypedDict):
    thumbnails: list[YtThumbnail]


class YtEntry(TypedDict):
    videoId: str
    title: dict[str, Any]  # pyright: ignore[reportExplicitAny]
    channelId: str
    lengthText: dict[str, Any]  # pyright: ignore[reportExplicitAny]
    shortViewCountText: dict[str, Any]  # pyright: ignore[reportExplicitAny]
    publishedTimeText: dict[str, Any]  # pyright: ignore[reportExplicitAny]
    thumbnail: YtThumbnailRes
    videoCountText: dict[str, Any]  # pyright: ignore[reportExplicitAny]
    subscriberCountText: dict[str, Any]  # pyright: ignore[reportExplicitAny]


def entry_to_item_data(type_: str, data: YtEntry) -> ItemData | None:
    icon_url = None
    match type_:
        case 'videoRenderer':
            subtext = ['Video']
            action_name = 'Watch on Youtube'
            url_path = f'watch?v={data["videoId"]}'
            if 'lengthText' in data:
                subtext.append(text_from(data['lengthText']))
            if 'shortViewCountText' in data:
                subtext.append(text_from(data['shortViewCountText']))
            if 'publishedTimeText' in data:
                subtext.append(text_from(data['publishedTimeText']))
            if data['thumbnail']['thumbnails']:
                icon_url = data['thumbnail']['thumbnails'][0]['url'].split('?', 1)[0]
        case 'channelRenderer':
            subtext = ['Channel']
            action_name = 'Show on Youtube'
            url_path = f'channel/{data["channelId"]}'
            if 'videoCountText' in data:
                subtext.append(text_from(data['videoCountText']))
            if 'subscriberCountText' in data:
                subtext.append(text_from(data['subscriberCountText']))
        case _:
            return None

    title = text_from(data['title'])
    url = f'https://www.youtube.com/{url_path}'
    return ItemData(title, ' | '.join(subtext), url, action_name, icon_url, None)


def results_to_items_data(results: list[dict[str, YtEntry]]) -> list[ItemData]:
    items_data: list[ItemData] = []
    for result in results:
        for type_, data in result.items():
            try:
                item_data = entry_to_item_data(type_, data)
                if item_data is None:
                    continue
                items_data.append(item_data)
            except KeyError as e:
                critical(str(e))
                critical(json.dumps(result, indent=4))
    return items_data


TMP_PREFIX = 'albert_yt_'


def clean_tmp() -> None:
    """
    Delete any temporary directories, that could've been created from a previous crash.
    """
    for temp_dir in Path(tempfile.gettempdir()).glob(f'{TMP_PREFIX}*'):
        for child in temp_dir.iterdir():
            child.unlink()
        temp_dir.rmdir()


class Plugin(PluginInstance, GeneratorQueryHandler):
    temp_dir: Path

    def __init__(self):
        PluginInstance.__init__(self)
        GeneratorQueryHandler.__init__(self)
        clean_tmp()
        self.temp_dir = Path(tempfile.mkdtemp(prefix=TMP_PREFIX))

    def __del__(self) -> None:
        clean_tmp()

    @override
    def synopsis(self, _query: str) -> str:
        return 'query'

    @override
    def defaultTrigger(self):
        return 'yt '

    @override
    def items(self, ctx: QueryContext) -> Generator[list[Item]]:
        query_str = ctx.query.strip()
        if not query_str:
            return

        # Avoid rate limiting
        for _ in range(50):
            time.sleep(0.01)
            if not ctx.isValid:
                return

        info(f"Searching YouTube for '{query_str}'")
        url = f'https://www.youtube.com/results?{urlencode({"search_query": query_str})}'

        with urlopen_with_headers(url) as response:  # pyright: ignore[reportAny]
            response_bytes: bytes = response.read()  # pyright: ignore[reportAny]
            match = re.search(DATA_REGEX, response_bytes.decode())
            if match is None:
                critical(
                    'Failed to receive expected data from YouTube. This likely means API changes, but could just be a '
                    + 'failed request.'
                )
                log_html(response_bytes)
                return

            results = json.loads(match.group(3))  # pyright: ignore[reportAny]
            primary_contents = results['contents']['twoColumnSearchResultsRenderer']['primaryContents']  # pyright: ignore[reportAny]
            contents = primary_contents['sectionListRenderer']['contents']  # pyright: ignore[reportAny]
            items_data: list[ItemData] = []
            for content_item in contents:  # pyright: ignore[reportAny]
                items_data.extend(
                    results_to_items_data(content_item.get('itemSectionRenderer', {}).get('contents', []))  # pyright: ignore[reportAny]
                )

            # Purge previous icons
            for child in self.temp_dir.iterdir():
                child.unlink()

            # Download icons
            with ThreadPoolExecutor(max_workers=10) as e:
                for item_data in items_data:
                    _ = e.submit(download_item_icon, item_data, self.temp_dir)
                    if not ctx.isValid:
                        return

            items: list[Item] = []
            for i, item_data in enumerate(items_data):
                icon_path = item_data.icon_path or ICON_PATH
                open_call = lambda item_data_=item_data: openUrl(item_data_.url)  # noqa: E731
                copy_call = lambda item_data_=item_data: setClipboardText(f'[{item_data_.title}]({item_data_.url})')  # noqa: E731
                item = StandardItem(
                    id=str(i),
                    text=item_data.title,
                    subtext=item_data.subtext,
                    icon_factory=lambda path=icon_path: Icon.image(path),
                    actions=[
                        Action('open', item_data.action_name, open_call),
                        Action('copy', 'Copy to clipboard', copy_call),
                    ],
                )
                items.append(item)
            # Add a link to the *YouTube* page, in case there's more results, including results we didn't include
            item = StandardItem(
                id='show_more',
                text='Show more in browser',
                icon_factory=lambda: Icon.image(ICON_PATH),
                actions=[
                    Action(
                        'show_more',
                        'Show more in browser',
                        lambda: openUrl(f'https://www.youtube.com/results?search_query={query_str}'),
                    )
                ],
            )
            items.append(item)

            yield items
