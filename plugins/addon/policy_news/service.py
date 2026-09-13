"""农业政策资讯抓取、存储和查询服务。"""
import logging
import re
from datetime import datetime
from html.parser import HTMLParser
from urllib.parse import urldefrag, urljoin, urlparse, urlunparse

import httpx
from sqlalchemy import delete, select

from core.db.base import async_session_factory
from core.time_utils import china_now
from .models import PolicyNewsItem, PolicyNewsState

logger = logging.getLogger(__name__)

SOURCE_URLS = {
    "农业农村部": "https://www.moa.gov.cn/gk/zcfg/",
    "广西农业农村厅": "http://nynct.gxzf.gov.cn/xxgk/jcxxgk/wjzl/flfg/",
}
ALLOWED_HOSTS = frozenset({"www.moa.gov.cn", "nynct.gxzf.gov.cn"})
ALLOWED_SCHEMES = frozenset({"http", "https"})
MAX_PARSE_ITEMS = 20
MAX_DISPLAY_ITEMS = 5
MAX_RETENTION_ITEMS = 100
MAX_URL_LENGTH = 700
MAX_RESPONSE_BYTES = 2 * 1024 * 1024
HTTP_TIMEOUT_SECONDS = 15.0
DATE_PATTERN = re.compile(
    r"(?P<year>20\d{2})\s*(?:[-/.年]\s*)"
    r"(?P<month>\d{1,2})\s*(?:[-/.月]\s*)"
    r"(?P<day>\d{1,2})\s*日?"
)


def parse_published_at(value: str) -> datetime | None:
    """解析官网列表中的常见中文或数字日期。"""
    match = DATE_PATTERN.search(value or "")
    if not match:
        return None
    try:
        return datetime(
            int(match.group("year")), int(match.group("month")), int(match.group("day"))
        )
    except ValueError:
        return None


def normalize_official_url(value: str, base_url: str) -> str | None:
    """解析并校验官方详情地址，移除片段以便稳定去重。"""
    url = urljoin(base_url, value or "")
    url, _fragment = urldefrag(url)
    parsed = urlparse(url)
    if parsed.scheme not in ALLOWED_SCHEMES or parsed.hostname not in ALLOWED_HOSTS:
        return None
    if not parsed.netloc or parsed.username or parsed.password:
        return None
    try:
        port = parsed.port
    except ValueError:
        return None
    if port is not None and port != (443 if parsed.scheme == "https" else 80):
        return None
    normalized = urlunparse((parsed.scheme, parsed.netloc, parsed.path, "", parsed.query, ""))
    if len(normalized) > MAX_URL_LENGTH:
        logger.warning(
            "跳过超过数据库字段限制的政策地址：长度 %d，限制 %d",
            len(normalized), MAX_URL_LENGTH,
        )
        return None
    return normalized


class _PolicyLinkParser(HTMLParser):
    """使用标准库提取列表行中的链接、标题和日期。"""

    _ROW_TAGS = frozenset({"li", "tr", "dd", "dt", "p", "article"})

    def __init__(self, base_url: str, source: str):
        super().__init__(convert_charrefs=True)
        self.base_url = base_url
        self.source = source
        self.items: list[dict] = []
        self._tag_stack: list[str] = []
        self._anchor: dict | None = None
        self._row: dict | None = None
        self._orphan_anchor: dict | None = None
        self._orphan_text: list[str] = []
        self._seen: set[str] = set()

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        tag = tag.lower()
        if self._row is None and tag in {"a", "br", "p", "div"}:
            self._finish_orphan()
        self._tag_stack.append(tag)
        if tag in self._ROW_TAGS and self._row is None:
            self._row = {"tag": tag, "text": [], "anchors": []}
        if tag == "a" and self._anchor is None:
            self._anchor = {"href": dict(attrs).get("href") or "", "title": []}

    def handle_data(self, data: str) -> None:
        text = " ".join(data.split())
        if not text:
            return
        if self._row is not None:
            self._row["text"].append(text)
        if self._anchor is not None:
            self._anchor["title"].append(text)
        elif self._orphan_anchor is not None:
            self._orphan_text.append(text)

    def handle_endtag(self, tag: str) -> None:
        tag = tag.lower()
        if tag == "a" and self._anchor is not None:
            anchor = self._anchor
            self._anchor = None
            if self._row is not None:
                self._row["anchors"].append(anchor)
            else:
                self._orphan_anchor = anchor
                self._orphan_text = []
        if self._row is not None and tag == self._row["tag"]:
            row = self._row
            self._row = None
            row_text = " ".join(row["text"])
            for anchor in row["anchors"]:
                self._append_item(anchor, row_text)
        if self._tag_stack:
            self._tag_stack.pop()

    def close(self) -> None:
        """处理没有显式块级容器的页面片段中的最后一条链接。"""
        super().close()
        self._finish_orphan()

    def _finish_orphan(self) -> None:
        if self._orphan_anchor is None:
            return
        anchor = self._orphan_anchor
        nearby = " ".join(self._orphan_text)
        self._orphan_anchor = None
        self._orphan_text = []
        self._append_item(anchor, nearby)

    def _append_item(self, anchor: dict, nearby_text: str) -> None:
        """完成单条候选记录校验，日期缺失的记录不进入政策数据。"""
        title = " ".join(" ".join(anchor["title"]).split())[:512]
        url = normalize_official_url(anchor["href"], self.base_url)
        published_at = parse_published_at(f"{title} {nearby_text}")
        if len(title) < 2 or not url or published_at is None or url in self._seen:
            return
        self._seen.add(url)
        self.items.append({
            "source": self.source,
            "title": title,
            "url": url,
            "published_at": published_at,
        })


def parse_listing(html: str, base_url: str, source: str) -> list[dict]:
    """解析一个来源列表页，最多返回 ``MAX_PARSE_ITEMS`` 条。"""
    parser = _PolicyLinkParser(base_url, source)
    parser.feed(html or "")
    parser.close()
    return parser.items[:MAX_PARSE_ITEMS]


async def _download_listing(client: httpx.AsyncClient, source: str, url: str) -> list[dict]:
    """下载单个来源并校验最终重定向地址和响应大小。"""
    async with client.stream("GET", url) as response:
        response.raise_for_status()
        final = urlparse(str(response.url))
        if final.hostname not in ALLOWED_HOSTS or final.scheme not in ALLOWED_SCHEMES:
            raise ValueError("重定向地址不属于官方来源")
        payload = bytearray()
        async for chunk in response.aiter_bytes():
            payload.extend(chunk)
            if len(payload) > MAX_RESPONSE_BYTES:
                raise ValueError("响应内容超过 2 MB 限制")
        encoding = response.encoding or "utf-8"
    return parse_listing(payload.decode(encoding, errors="replace"), str(response.url), source)


async def refresh_policies() -> dict:
    """抓取所有来源，部分成功写入新数据，全部失败时保留旧数据。"""
    results: dict[str, list[dict]] = {}
    errors: dict[str, str] = {}
    async with httpx.AsyncClient(
        timeout=httpx.Timeout(HTTP_TIMEOUT_SECONDS),
        follow_redirects=True,
        max_redirects=3,
        headers={"User-Agent": "HuiYanPolicyNews/1.0"},
    ) as client:
        for source, url in SOURCE_URLS.items():
            try:
                results[source] = await _download_listing(client, source, url)
            except Exception as exc:
                errors[source] = str(exc)[:1024]
                logger.warning("政策来源抓取失败 [%s]: %s", source, errors[source])

    now = china_now()
    async with async_session_factory() as db:
        for source in SOURCE_URLS:
            state = (await db.execute(
                select(PolicyNewsState).where(PolicyNewsState.source == source)
            )).scalar_one_or_none()
            if state is None:
                state = PolicyNewsState(source=source, item_count=0)
                db.add(state)
            state.last_attempt_at = now
            if source in errors:
                state.last_error = errors[source]
                continue
            items = results[source]
            state.last_success_at = now
            state.last_error = ""
            state.item_count = len(items)
            for item in items:
                row = (await db.execute(
                    select(PolicyNewsItem).where(
                        PolicyNewsItem.source == source,
                        PolicyNewsItem.url == item["url"],
                    )
                )).scalar_one_or_none()
                if row is None:
                    db.add(PolicyNewsItem(
                        **item, first_seen_at=now, last_seen_at=now,
                    ))
                else:
                    row.title = item["title"]
                    row.published_at = item["published_at"]
                    row.last_seen_at = now
            await db.flush()
        if results:
            await _prune_items(db)
        await db.commit()
    if not results:
        raise RuntimeError("两个政策来源均抓取失败")
    return {
        "success": True,
        "source_count": len(results),
        "item_count": sum(len(items) for items in results.values()),
        "errors": errors,
    }


async def _prune_items(db) -> None:
    """保留最新 100 条政策。"""
    ids = (await db.execute(
        select(PolicyNewsItem.id).order_by(
            PolicyNewsItem.published_at.is_(None).asc(),
            PolicyNewsItem.published_at.desc(),
            PolicyNewsItem.last_seen_at.desc(),
            PolicyNewsItem.id.desc(),
        ).offset(MAX_RETENTION_ITEMS)
    )).scalars().all()
    if ids:
        await db.execute(delete(PolicyNewsItem).where(PolicyNewsItem.id.in_(ids)))


async def get_latest(limit: int = MAX_DISPLAY_ITEMS, *, per_source: bool = True) -> list[dict]:
    """返回首页和农户端使用的安全政策字段，每个来源默认最多 ``limit`` 条。"""
    limit = max(1, min(int(limit), MAX_RETENTION_ITEMS))
    async with async_session_factory() as db:
        rows = (await db.execute(
            select(PolicyNewsItem).order_by(
                PolicyNewsItem.published_at.is_(None).asc(),
                PolicyNewsItem.published_at.desc(),
                PolicyNewsItem.last_seen_at.desc(),
                PolicyNewsItem.id.desc(),
            ).limit(MAX_RETENTION_ITEMS)
        )).scalars().all()
    safe_items = []
    source_counts: dict[str, int] = {}
    for row in rows:
        if per_source and source_counts.get(row.source, 0) >= limit:
            continue
        safe_url = normalize_official_url(row.url, SOURCE_URLS.get(row.source, ""))
        if not safe_url:
            logger.warning("忽略数据库中的非官方政策地址: %s", row.url)
            continue
        safe_items.append({
        "id": row.id,
        "title": row.title,
        "source": row.source,
        "published_at": row.published_at.strftime("%Y-%m-%d") if row.published_at else None,
        "url": safe_url,
        })
        source_counts[row.source] = source_counts.get(row.source, 0) + 1
        if not per_source and len(safe_items) >= limit:
            break
    return safe_items


async def get_status() -> dict:
    """返回来源抓取状态和当前条目。"""
    async with async_session_factory() as db:
        states = (await db.execute(
            select(PolicyNewsState).order_by(PolicyNewsState.id.asc())
        )).scalars().all()
    return {
        "sources": [{
            "source": state.source,
            "last_attempt_at": state.last_attempt_at.isoformat() if state.last_attempt_at else None,
            "last_success_at": state.last_success_at.isoformat() if state.last_success_at else None,
            "last_error": state.last_error,
            "item_count": state.item_count,
        } for state in states],
        "items": await get_latest(MAX_RETENTION_ITEMS, per_source=False),
    }


async def clear_policies() -> None:
    """清理政策条目并重置来源状态。"""
    async with async_session_factory() as db:
        await db.execute(delete(PolicyNewsItem))
        await db.execute(delete(PolicyNewsState))
        await db.commit()
