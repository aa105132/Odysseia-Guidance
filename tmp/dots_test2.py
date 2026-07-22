#!/usr/bin/env python3
"""Test dots-ai API directly without importing from src."""
import asyncio, json, aiohttp, re

API_URL = "http://169.58.50.21:8010/v1/chat/completions"

# Read API key from .env
import os
env_path = "/app/.env"
api_key = ""
with open(env_path) as f:
    for line in f:
        if "IMAGE_SEARCH_API_KEY" in line and "=" in line:
            api_key = line.split("=", 1)[1].strip().strip('"').strip("'")
            break

if not api_key:
    print("WARNING: API key not found, trying without auth")
    api_key = "dummy"

print(f"API_KEY found: {bool(api_key) and api_key != 'dummy'}")

async def test_prompt(system_prompt, user_prompt, label):
    payload = {
        "model": "dots-ai",
        "messages": [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_prompt},
        ],
        "stream": False,
    }
    headers = {"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"}
    timeout = aiohttp.ClientTimeout(total=90)
    try:
        async with aiohttp.ClientSession(timeout=timeout) as session:
            async with session.post(API_URL, headers=headers, json=payload) as resp:
                text = await resp.text()
                print(f"\n=== {label} (status={resp.status}) ===")
                try:
                    data = json.loads(text)
                    msg = data.get("choices", [{}])[0].get("message", {})
                    content = msg.get("content", "")
                    if isinstance(content, list):
                        for i, part in enumerate(content):
                            ptype = part.get("type", "unknown")
                            print(f"  part[{i}] type={ptype}, keys={list(part.keys())}")
                            if ptype in ("image_url", "input_image"):
                                iu = part.get("image_url", {})
                                url = iu.get("url", "") if isinstance(iu, dict) else str(iu)
                                is_b64 = url.startswith("data:image")
                                print(f"    image_url is_base64={is_b64}, len={len(url)}")
                                if is_b64:
                                    print(f"    BASE64 FOUND! prefix={url[:80]}")
                    elif isinstance(content, str):
                        print(f"  content type=str, len={len(content)}")
                        has_b64 = "data:image" in content
                        print(f"  has_base64={has_b64}")
                        print(f"  first 500: {content[:500]}")
                        if has_b64:
                            matches = re.findall(r"data:image/[^;]+;base64,[A-Za-z0-9+/=]+", content)
                            print(f"  base64 matches: {len(matches)}")
                            for m in matches[:3]:
                                print(f"    b64 len={len(m)}")
                        has_img = "<img" in content.lower()
                        print(f"  has_img_tag={has_img}")
                    for k in ["inline_data", "images", "image", "image_data", "image_url"]:
                        if k in msg:
                            print(f"  msg has key={k}")
                    usage = data.get("usage", {})
                    if usage:
                        print(f"  usage: {json.dumps(usage)}")
                except Exception as e:
                    print(f"  parse error: {e}")
                    print(f"  raw[:500]: {text[:500]}")
    except Exception as e:
        print(f"\n=== {label} ERROR: {e} ===")


async def main():
    # P1: direct base64
    await test_prompt(
        "You are an image search API. When given a query, you MUST return actual image data as base64-encoded data URLs. Return ONLY data URLs, one per line. Format: data:image/png;base64,<base64data>",
        "梅凝 凡人修仙传",
        "P1: direct base64"
    )

    # P2: OpenAI vision format
    await test_prompt(
        "You are an image search tool. Return results as OpenAI message content array with image_url parts. Each part: {type: image_url, image_url: {url: data:image/png;base64,...}}. Also include a text part describing each image.",
        "搜索: 梅凝 凡人修仙传动漫",
        "P2: OpenAI vision format"
    )

    # P3: simple
    await test_prompt(
        "You are a helpful assistant with image search capabilities.",
        "请搜索并返回一张 凡人修仙传 梅凝 的图片，直接以 base64 data URL 格式返回",
        "P3: simple ask"
    )

    # P4: HTML with base64
    await test_prompt(
        "You are an image search API. Return HTML with <img> tags where src is a base64 data URL. Example: <img src=\"data:image/png;base64,iVBOR...\" alt=\"description\">. Return ONLY HTML, no other text.",
        "梅凝 凡人修仙传",
        "P4: HTML base64 src"
    )

    # P5: capability check
    await test_prompt(
        "You are dots-ai. Describe your capabilities. Can you search the web? Can you return images? Can you return base64 data?",
        "What can you do?",
        "P5: capability check"
    )

asyncio.run(main())
