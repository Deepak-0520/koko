"""Ask KOKO: a Claude agent that answers questions about places in Japan using KOKO's own data as tools.

Claude never sees the database directly. It calls two tools (address search and the risk report),
and the system prompt requires every number in an answer to come from a tool result.
The endpoint streams Server-Sent Events: text deltas, tool activity, and a final 'done'.
"""
from __future__ import annotations

import asyncio
import json
import logging
import os
from collections.abc import AsyncIterator

import anthropic
import httpx

from .report import OutsideCoverage, build_report

log = logging.getLogger(__name__)

MODEL = "claude-opus-5-5"
MAX_TOOL_ROUNDS = 8
GSI_SEARCH = "https://msearch.gsi.go.jp/address-search/AddressSearch"

SYSTEM = """You are KOKO (ここ), a disaster-risk assistant for people living in or moving to Japan, \
many of them foreign residents who do not read Japanese.

You answer questions about specific places using two tools:
- search_address: turns a Japanese address or place name into coordinates.
- get_risk_report: returns official hazard data for a point: river flood depth and duration, \
house-collapse zones, the building at that point (PLATEAU: storeys, height, flood depth), which floor \
stays dry, landslide / storm surge / tsunami zones, 30-year earthquake shaking probabilities (J-SHIS), \
and the nearest evacuation sites rated for each hazard.

Rules:
- Every number, zone or place name in your answer must come from a tool result in this conversation. \
Never estimate or recall hazard figures from memory. If a tool says data is missing for an area, say so.
- "Not in a designated zone" is not "no risk", and "no published data" is not "safe". Say which one applies.
- Name the dataset behind each key fact briefly (for example: MLIT flood zones, PLATEAU, J-SHIS, GSI shelters).
- You explain official hazard assumptions; you do not predict disasters. For a disaster happening now, \
tell the user to follow JMA warnings and their municipality's evacuation orders, and not to wait for you.
- Reply in the language the user writes in. If that is unclear (for example a bare address), use the \
interface language given in their message. Use plain words: explain Japanese terms (e.g. 浸水想定区域, \
震度6弱) the first time they appear. Keep answers short and practical: lead with the answer, then the \
two or three facts behind it, then one concrete action.
- When comparing places, call get_risk_report for each, then compare like for like.
- If the user refers to "this place" or "here", use the map selection given in their message.
Latency-sensitive: begin your visible answer immediately once you have the data."""

TOOLS = [
    {
        "name": "search_address",
        "description": "Find coordinates for a Japanese address, station, or place name using the "
                       "GSI (Geospatial Information Authority of Japan) address search. Returns up to 5 "
                       "candidates with name, lat and lon. Japanese input works best (e.g. 大阪市此花区西九条).",
        "input_schema": {
            "type": "object",
            "properties": {"query": {"type": "string", "description": "Address or place name"}},
            "required": ["query"],
            "additionalProperties": False,
        },
        "eager_input_streaming": True,
    },
    {
        "name": "get_risk_report",
        "description": "Official hazard report for one point in Japan: flood, building / safe floor, "
                       "landslide, storm surge, tsunami, earthquake probabilities, nearest evacuation sites, "
                       "and which datasets are available for the area.",
        "input_schema": {
            "type": "object",
            "properties": {
                "lat": {"type": "number", "description": "Latitude (WGS84)"},
                "lon": {"type": "number", "description": "Longitude (WGS84)"},
            },
            "required": ["lat", "lon"],
            "additionalProperties": False,
        },
        "eager_input_streaming": True,
    },
]


def is_configured() -> bool:
    return bool(os.environ.get("ANTHROPIC_API_KEY") or os.environ.get("ANTHROPIC_AUTH_TOKEN"))


def _compact_report(rep: dict) -> dict:
    """Drop fields the model doesn't need (source URLs, raw building risk lists) to save tokens."""
    out = {k: v for k, v in rep.items() if k not in ("sources",)}
    if out.get("building"):
        out["building"] = {k: v for k, v in out["building"].items() if k != "risks"}
    return out


async def _search_address(query: str) -> dict:
    async with httpx.AsyncClient(timeout=15) as client:
        r = await client.get(GSI_SEARCH, params={"q": query})
        r.raise_for_status()
        hits = r.json()[:5]
    return {"candidates": [{"name": h["properties"]["title"], "lon": h["geometry"]["coordinates"][0],
                            "lat": h["geometry"]["coordinates"][1]} for h in hits]}


async def _run_tool(pool, name: str, args) -> tuple[str, bool]:
    """Run one tool call. Returns (JSON text, is_error). Inputs are validated here because eager
    input streaming means the API does not validate them for us."""
    try:
        if name == "search_address":
            if not (isinstance(args, dict) and isinstance(args.get("query"), str) and args["query"].strip()):
                return json.dumps({"INVALID_INPUT": args}), True
            return json.dumps(await _search_address(args["query"]), ensure_ascii=False), False
        if name == "get_risk_report":
            lat, lon = (args or {}).get("lat"), (args or {}).get("lon")
            if not (isinstance(lat, (int, float)) and isinstance(lon, (int, float))
                    and 20 <= lat <= 46 and 122 <= lon <= 154):
                return json.dumps({"INVALID_INPUT": args, "hint": "lat 20-46, lon 122-154"}), True

            def run():
                with pool.connection() as conn:
                    return build_report(conn, float(lat), float(lon))
            rep = await asyncio.to_thread(run)
            return json.dumps(_compact_report(rep), ensure_ascii=False, default=str), False
        return json.dumps({"error": f"unknown tool {name}"}), True
    except OutsideCoverage as e:
        return json.dumps({"error": str(e)}), True
    except httpx.HTTPError as e:
        return json.dumps({"error": f"address search failed: {e.__class__.__name__}"}), True


def _sse(event: dict) -> str:
    return f"data: {json.dumps(event, ensure_ascii=False)}\n\n"


def _error(code: str, message: str) -> str:
    """Errors carry a code the browser translates; `message` is the English fallback for API clients."""
    return _sse({"type": "error", "code": code, "message": message})


LANG_NAMES = {"en": "English", "ja": "Japanese"}


async def chat_stream(pool, history: list[dict], selection: dict | None, lang: str = "en") -> AsyncIterator[str]:
    """Stream one assistant turn. `history` is plain-text turns [{role, content}] from the browser."""
    if not is_configured():
        yield _error("no_key", "Ask KOKO needs an Anthropic API key. Add ANTHROPIC_API_KEY "
                               "to the .env file in the project folder and restart the server.")
        return

    messages = [{"role": m["role"], "content": m["content"]} for m in history if m.get("content")]
    if messages and messages[-1]["role"] == "user":
        ctx = f"[Interface language: {LANG_NAMES.get(lang, 'English')}]\n"
        if selection:
            ctx += f"[Map selection: lat {selection['lat']:.5f}, lon {selection['lon']:.5f}" + \
                   (f", {selection['label']}" if selection.get("label") else "") + "]\n"
        messages[-1] = {"role": "user", "content": ctx + messages[-1]["content"]}

    client = anthropic.AsyncAnthropic()
    try:
        for _ in range(MAX_TOOL_ROUNDS):
            async with client.beta.messages.stream(
                model=MODEL,
                max_tokens=32000,
                system=SYSTEM,
                tools=TOOLS,
                messages=messages,
                output_config={"effort": "medium"},
                cache_control={"type": "ephemeral"},  # system + tools are stable: cache them
                betas=["server-side-fallback-2026-07-01"],
                fallbacks="default",
            ) as stream:
                async for event in stream:
                    if event.type == "text":
                        yield _sse({"type": "text", "text": event.text})
                response = await stream.get_final_message()

            if response.stop_reason == "refusal":
                yield _error("refusal", "The assistant declined this request.")
                return
            if response.stop_reason == "pause_turn":
                messages.append({"role": "assistant", "content": response.content})
                continue
            tool_uses = [b for b in response.content if b.type == "tool_use"]
            if not tool_uses:
                break
            if response.stop_reason == "max_tokens":
                yield _error("cut_off", "The answer was cut off. Please ask a shorter question.")
                return

            yield _sse({"type": "text", "text": "\n\n"})
            for b in tool_uses:
                yield _sse({"type": "tool", "name": b.name, "input": b.input})
            results = await asyncio.gather(*(_run_tool(pool, b.name, b.input) for b in tool_uses))
            messages.append({"role": "assistant", "content": response.content})
            # all results go back in one user message, so Claude keeps making parallel calls
            messages.append({"role": "user", "content": [
                {"type": "tool_result", "tool_use_id": b.id, "content": text, "is_error": err}
                for b, (text, err) in zip(tool_uses, results)]})
        else:
            yield _error("too_many", "Stopped after too many lookups. Try a narrower question.")
            return
        yield _sse({"type": "done"})
    except ValueError:
        # tool input JSON the SDK could not parse at all (eager input streaming)
        yield _error("bad_tool", "The assistant produced an unreadable tool call. Please retry.")
    except anthropic.AuthenticationError:
        yield _error("auth", "The Anthropic API key was rejected. Check ANTHROPIC_API_KEY in .env.")
    except anthropic.RateLimitError:
        yield _error("rate", "Rate limited by the Anthropic API. Wait a minute and retry.")
    except anthropic.APIStatusError as e:
        log.exception("Anthropic API error")
        yield _error("api", f"Anthropic API error {e.status_code}.")
    except anthropic.APIConnectionError:
        yield _error("conn", "Could not reach the Anthropic API. Check the internet connection.")
