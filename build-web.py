#!/usr/bin/env python3
"""Artifact용 본문(diary.html)을 독립 웹앱 index.html 로 감싼다.

Artifact는 <head>를 대신 채워 주지만 배포판은 직접 채워야 한다.
본문 맨 위의 <title>·글꼴 <link> 를 머리로 옮기고, PWA 태그와
서비스워커 등록을 붙인다.
"""
import io, re, sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
SRC = Path(sys.argv[1]) if len(sys.argv) > 1 else HERE / "web" / "diary.html"
OUT = HERE / "web" / "index.html"

body = io.open(SRC, encoding="utf-8").read()

title = re.search(r"<title>(.*?)</title>", body).group(1)
links = re.findall(r'^<link [^>]*>$', body, re.M)

# 머리로 옮긴 것은 본문에서 뺀다
body = re.sub(r"<title>.*?</title>\n", "", body, count=1)
for ln in links:
    body = body.replace(ln + "\n", "", 1)
body = body.strip()

head_links = "\n".join("  " + ln for ln in links)

page = f"""<!DOCTYPE html>
<html lang="ko">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1, viewport-fit=cover">
<title>{title}</title>
<meta name="description" content="비밀번호와 잠금 패턴으로 여는 개인 일기장. 기록은 이 기기 안에서 암호화되어 보관됩니다.">
<meta name="robots" content="noindex, nofollow">
<meta name="color-scheme" content="light dark">
<meta name="theme-color" content="#b3372c" media="(prefers-color-scheme: light)">
<meta name="theme-color" content="#12161c" media="(prefers-color-scheme: dark)">
<meta name="apple-mobile-web-app-capable" content="yes">
<meta name="mobile-web-app-capable" content="yes">
<meta name="apple-mobile-web-app-status-bar-style" content="default">
<meta name="apple-mobile-web-app-title" content="{title}">
<link rel="manifest" href="./manifest.webmanifest">
<link rel="apple-touch-icon" href="./apple-touch-icon.png">
<link rel="icon" type="image/png" sizes="192x192" href="./icon-192.png">
<link rel="icon" type="image/png" sizes="32x32" href="./favicon-32.png">
{head_links}
</head>
<body>
{body}

<script>
/* 껍데기를 담아 두어 인터넷 없이도, 홈 화면에서도 열리게 한다 */
if("serviceWorker" in navigator){{
  window.addEventListener("load", function(){{
    navigator.serviceWorker.register("./sw.js").catch(function(){{}});
  }});
}}
</script>
</body>
</html>
"""

io.open(OUT, "w", encoding="utf-8").write(page)
print(f"index.html 생성  {len(page):,} 바이트  (제목: {title}, 머리로 옮긴 link {len(links)}개)")
