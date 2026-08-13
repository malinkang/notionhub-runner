"""Update Daily shortcut properties without rebuilding the journal page body."""

from __future__ import annotations

import json
import math
import os
import time as time_module
from dataclasses import dataclass
from datetime import date, datetime, time, timedelta, timezone
from email.utils import parsedate_to_datetime
from typing import Any, Mapping

import requests


SHANGHAI = timezone(timedelta(hours=8))
WEEKDAY = ["一", "二", "三", "四", "五", "六", "日"]
NOTION_API_BASE = "https://api.notion.com/v1"
NOTION_VERSION = "2026-03-11"
DEFAULT_RETRY_SECONDS = 60
MAX_FALLBACK_RETRY_SECONDS = 300


@dataclass(frozen=True)
class DailyShortcutConfig:
    notion_token: str
    journal_data_source_id: str


class NotionClient:
    def __init__(self, token: str):
        if not token:
            raise ValueError("缺少 Notion token")
        self.token = token
        self.session = requests.Session()
        self.last_request_at = 0.0

    def request(self, method: str, path: str, *, json_body: dict[str, Any] | None = None) -> dict[str, Any]:
        rate_limit_count = 0
        while True:
            self._pace()
            response = self.session.request(
                method,
                f"{NOTION_API_BASE}{path}",
                headers={
                    "Authorization": f"Bearer {self.token}",
                    "Notion-Version": NOTION_VERSION,
                    "Content-Type": "application/json",
                },
                json=json_body,
                timeout=120,
            )
            if response.status_code != 429:
                break
            wait_seconds = notion_retry_after_seconds(
                response.headers.get("Retry-After"),
                rate_limit_count,
            )
            rate_limit_count += 1
            print(
                "[notion] 超出 Notion 请求限制，请等待 "
                f"{format_notion_wait(wait_seconds)} 后继续（第 {rate_limit_count} 次限流）",
                flush=True,
            )
            response.close()
            time_module.sleep(wait_seconds)
        if not response.ok:
            try:
                message = str(response.json().get("message") or "")
            except (ValueError, AttributeError):
                message = ""
            raise RuntimeError(f"Notion API {response.status_code}: {message or '请求失败'}")
        return response.json() if response.text else {}

    def _pace(self) -> None:
        elapsed = time_module.time() - self.last_request_at
        if elapsed < 0.35:
            time_module.sleep(0.35 - elapsed)
        self.last_request_at = time_module.time()

    def query_data_source(self, data_source_id: str, body: dict[str, Any]) -> list[dict[str, Any]]:
        results: list[dict[str, Any]] = []
        payload = dict(body)
        while True:
            response = self.request("POST", f"/data_sources/{data_source_id}/query", json_body=payload)
            results.extend(response.get("results") or [])
            if not response.get("has_more") or not response.get("next_cursor"):
                return results
            payload["start_cursor"] = response["next_cursor"]

    def create_page(self, data_source_id: str, properties: dict[str, Any], *, icon: dict[str, Any]) -> str:
        response = self.request("POST", "/pages", json_body={
            "parent": {"type": "data_source_id", "data_source_id": data_source_id},
            "properties": properties,
            "icon": icon,
        })
        return str(response["id"])

    def update_page(
        self,
        page_id: str,
        *,
        properties: dict[str, Any],
        icon: dict[str, Any] | None = None,
    ) -> None:
        body: dict[str, Any] = {"properties": properties}
        if icon:
            body["icon"] = icon
        self.request("PATCH", f"/pages/{page_id}", json_body=body)


def notion_retry_after_seconds(value: str | None, rate_limit_count: int) -> int:
    text = str(value or "").strip()
    if text:
        try:
            return max(1, math.ceil(float(text)))
        except ValueError:
            try:
                retry_at = parsedate_to_datetime(text)
                if retry_at.tzinfo is None:
                    retry_at = retry_at.replace(tzinfo=timezone.utc)
                return max(1, math.ceil((retry_at - datetime.now(timezone.utc)).total_seconds()))
            except (TypeError, ValueError, OverflowError):
                pass
    return min(DEFAULT_RETRY_SECONDS * (rate_limit_count + 1), MAX_FALLBACK_RETRY_SECONDS)


def format_notion_wait(seconds: int) -> str:
    total = max(0, int(seconds))
    return f"{total // 60} 分钟 {total % 60} 秒"


def load_daily_shortcut_config(env: Mapping[str, str] | None = None) -> DailyShortcutConfig:
    source = env or os.environ
    config = parse_json_object(source.get("DAILY_CONFIG"), "DAILY_CONFIG", allow_empty=True)
    notion_data = parse_json_object(
        config.get("DAILY_NOTION_DATA") or source.get("DAILY_NOTION_DATA"),
        "DAILY_NOTION_DATA",
        allow_empty=True,
    )
    journal_payload = parse_json_object(
        config.get("DAILY_JOURNAL_PAYLOAD_JSON") or source.get("DAILY_JOURNAL_PAYLOAD_JSON"),
        "DAILY_JOURNAL_PAYLOAD_JSON",
        allow_empty=True,
    )
    payload_notion = journal_payload.get("notion") if isinstance(journal_payload.get("notion"), dict) else {}
    notion_token = first_non_empty(
        config.get("DAILY_NOTION_TOKEN"),
        source.get("DAILY_NOTION_TOKEN"),
        notion_data.get("access_token"),
        payload_notion.get("access_token"),
    )
    data_source_id = first_non_empty(
        notion_data.get("journal_data_source_id"),
        payload_notion.get("journal_data_source_id"),
        config.get("DAILY_JOURNAL_DATA_SOURCE_ID"),
        source.get("DAILY_JOURNAL_DATA_SOURCE_ID"),
    )
    if not notion_token:
        raise ValueError("DAILY_CONFIG 缺少 Daily Notion token")
    if not data_source_id:
        raise ValueError("DAILY_CONFIG 缺少 journal_data_source_id")
    return DailyShortcutConfig(notion_token=notion_token, journal_data_source_id=data_source_id)


def parse_shortcut_payload(value: str) -> dict[str, Any]:
    payload = parse_json_object(value, "快捷指令参数")
    if not payload:
        raise ValueError("快捷指令参数不能为空")
    return payload


def target_date_from_payload(payload: Mapping[str, Any], now: datetime | None = None) -> date:
    raw = str(payload.get("date") or "").strip()
    if raw:
        try:
            return date.fromisoformat(raw[:10])
        except ValueError as error:
            raise ValueError("date 必须使用 YYYY-MM-DD 格式") from error
    return (now or datetime.now(SHANGHAI)).astimezone(SHANGHAI).date()


def weather_emoji(weather: str) -> str:
    if "晴" in weather:
        return "☀️"
    if "雨" in weather:
        return "🌧"
    if "雪" in weather:
        return "❄️"
    if "云" in weather:
        return "☁️"
    if "雾" in weather:
        return "🌫"
    return "☀️"


def weather_properties(payload: Mapping[str, Any]) -> tuple[dict[str, Any], str]:
    weather = required_text(payload, "weather")
    highest = required_text(payload, "highest")
    lowest = required_text(payload, "lowest")
    raw_aqi = payload.get("aqi")
    try:
        aqi = float(str(raw_aqi).strip())
    except (TypeError, ValueError) as error:
        raise ValueError("aqi 必须是数字") from error
    if not math.isfinite(aqi):
        raise ValueError("aqi 必须是有限数字")
    normalized_aqi: int | float = int(aqi) if aqi.is_integer() else aqi
    return {
        "天气": {"rich_text": rich_text(weather)},
        "最高温度": {"rich_text": rich_text(highest)},
        "最低温度": {"rich_text": rich_text(lowest)},
        "空气质量": {"number": normalized_aqi},
    }, weather_emoji(weather)


def location_properties(payload: Mapping[str, Any]) -> dict[str, Any]:
    return {"位置": {"rich_text": rich_text(required_text(payload, "location"))}}


class DailyShortcutUpdater:
    def __init__(self, client: NotionClient, data_source_id: str):
        self.client = client
        self.data_source_id = data_source_id
        self.schema = self.client.request("GET", f"/data_sources/{data_source_id}")
        self.schema_properties = self.schema.get("properties") or {}

    def update(
        self,
        target_date: date,
        properties: Mapping[str, Any],
        *,
        icon: str | None = None,
    ) -> dict[str, Any]:
        self.validate_properties(properties)
        page = self.find_page(target_date)
        created = page is None
        icon_payload = {"type": "emoji", "emoji": icon} if icon else None
        if page:
            page_id = str(page["id"])
            self.client.update_page(page_id, properties=dict(properties), icon=icon_payload)
        else:
            create_properties = self.build_create_properties(target_date)
            create_properties.update(properties)
            page_id = self.client.create_page(
                self.data_source_id,
                create_properties,
                icon=icon_payload or {"type": "emoji", "emoji": "☀️"},
            )
        return {"pageId": page_id, "date": target_date.isoformat(), "created": created}

    def validate_properties(self, properties: Mapping[str, Any]) -> None:
        missing: list[str] = []
        incompatible: list[str] = []
        for name, value in properties.items():
            schema = self.schema_properties.get(name)
            if not isinstance(schema, dict):
                missing.append(name)
                continue
            expected_type = next(iter(value), "")
            if schema.get("type") != expected_type:
                incompatible.append(f"{name}（需要 {expected_type}，当前为 {schema.get('type') or 'unknown'}）")
        if missing:
            raise ValueError(f"日记数据库缺少属性：{', '.join(missing)}")
        if incompatible:
            raise ValueError(f"日记数据库属性类型不兼容：{', '.join(incompatible)}")

    def find_page(self, target_date: date) -> dict[str, Any] | None:
        slug_property = self.property_name("rich_text", "slug", "Slug")
        if slug_property:
            page = self.query_first({
                "property": slug_property,
                "rich_text": {"equals": target_date.isoformat()},
            })
            if page:
                return page
        title_property = self.property_name("title", "title", "Name", "标题", "名称")
        if not title_property:
            raise ValueError("日记数据库缺少 title 类型的标题属性")
        return self.query_first({
            "property": title_property,
            "title": {"equals": format_date_with_week(target_date)},
        })

    def query_first(self, filter_value: dict[str, Any]) -> dict[str, Any] | None:
        results = self.client.query_data_source(
            self.data_source_id,
            {"filter": filter_value, "page_size": 2},
        )
        return results[0] if results else None

    def build_create_properties(self, target_date: date) -> dict[str, Any]:
        title_property = self.property_name("title", "title", "Name", "标题", "名称")
        if not title_property:
            raise ValueError("日记数据库缺少 title 类型的标题属性")
        result: dict[str, Any] = {
            title_property: {"title": rich_text(format_date_with_week(target_date))},
        }
        start = datetime.combine(target_date, time.min, tzinfo=SHANGHAI).isoformat()
        self.add_optional(result, "date", {"date": {"start": start, "end": None}}, "date", "日期", "Date")
        self.add_optional(result, "rich_text", {"rich_text": rich_text(target_date.isoformat())}, "slug", "Slug")
        self.add_optional(
            result,
            "multi_select",
            {"multi_select": [
                {"name": str(target_date.year)},
                {"name": f"{target_date.month}月"},
                {"name": f"第{target_date.isocalendar().week:02d}周"},
            ]},
            "tags",
            "Tags",
            "标签",
        )
        self.add_optional(result, "select", {"select": {"name": "Post"}}, "type", "Type", "类型")
        status_name = self.property_name("status", "status", "Status", "状态")
        if status_name:
            result[status_name] = {"status": {"name": "Published"}}
        else:
            self.add_optional(result, "select", {"select": {"name": "Published"}}, "status", "Status", "状态")
        return result

    def add_optional(
        self,
        result: dict[str, Any],
        property_type: str,
        value: dict[str, Any],
        *candidates: str,
    ) -> None:
        name = self.property_name(property_type, *candidates)
        if name:
            result[name] = value

    def property_name(self, property_type: str, *candidates: str) -> str | None:
        for candidate in candidates:
            schema = self.schema_properties.get(candidate)
            if isinstance(schema, dict) and schema.get("type") == property_type:
                return candidate
        if property_type == "title":
            for name, schema in self.schema_properties.items():
                if isinstance(schema, dict) and schema.get("type") == "title":
                    return str(name)
        return None


def update_weather(content: str, env: Mapping[str, str] | None = None) -> dict[str, Any]:
    payload = parse_shortcut_payload(content)
    config = load_daily_shortcut_config(env)
    properties, icon = weather_properties(payload)
    updater = DailyShortcutUpdater(NotionClient(config.notion_token), config.journal_data_source_id)
    return updater.update(target_date_from_payload(payload), properties, icon=icon)


def update_location(content: str, env: Mapping[str, str] | None = None) -> dict[str, Any]:
    payload = parse_shortcut_payload(content)
    config = load_daily_shortcut_config(env)
    updater = DailyShortcutUpdater(NotionClient(config.notion_token), config.journal_data_source_id)
    return updater.update(target_date_from_payload(payload), location_properties(payload))


def parse_json_object(value: Any, label: str, *, allow_empty: bool = False) -> dict[str, Any]:
    if value is None or value == "":
        if allow_empty:
            return {}
        raise ValueError(f"{label}不能为空")
    if isinstance(value, dict):
        return value
    try:
        parsed = json.loads(str(value))
    except json.JSONDecodeError as error:
        raise ValueError(f"{label}不是有效的 JSON") from error
    if not isinstance(parsed, dict):
        raise ValueError(f"{label}必须是 JSON 对象")
    return parsed


def required_text(payload: Mapping[str, Any], key: str) -> str:
    value = str(payload.get(key) or "").strip()
    if not value:
        raise ValueError(f"缺少 {key}")
    return value


def format_date_with_week(value: date) -> str:
    return f"{value.month:02d}月{value.day:02d}日 星期{WEEKDAY[value.weekday()]}"


def rich_text(value: str) -> list[dict[str, Any]]:
    return [{"type": "text", "text": {"content": value}}]


def first_non_empty(*values: Any) -> str:
    for value in values:
        normalized = str(value or "").strip()
        if normalized:
            return normalized
    return ""
