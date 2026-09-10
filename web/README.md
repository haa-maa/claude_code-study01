# 오늘의 기록 — 배포용 파일

이 폴더를 그대로 올리면 웹앱이 동작합니다. 빌드 과정이 없습니다.

## 올리는 법

**A. Netlify 끌어다 놓기 (가장 쉬움)**

1. https://app.netlify.com/drop 접속
2. 이 폴더(또는 `오늘의기록-웹앱.zip`)를 끌어다 놓기

**B. 이미 만들어 둔 Netlify 사이트로**

`oneuli-girok` 사이트가 이미 만들어져 있습니다.
이 폴더에서 아래를 실행하면 그 사이트로 올라갑니다.

```
npx netlify-cli deploy --prod --dir . --site oneuli-girok
```

**C. GitHub Pages**

이 폴더의 내용을 저장소에 올리고, Settings → Pages 에서
브랜치와 폴더를 지정하면 됩니다.

## 파일

```
index.html            앱 전체 (화면 + 기능, 외부 의존성 없음)
manifest.webmanifest  홈 화면에 추가할 때 쓰는 정보
sw.js                 껍데기를 담아 두어 인터넷 없이도 열리게 함
icon-*.png            앱 아이콘 (인장 빨강 + 잠금 패턴 9점)
_headers              Netlify 보안·캐시 설정
robots.txt            검색 노출 차단 (개인 일기장)
```

## 알아두기

- 기록은 **여는 사람의 브라우저 안에서 암호화되어** 보관됩니다.
  서버로 올라가지 않으므로, 기기를 바꾸면 이전 기록이 보이지 않습니다.
- 비밀번호와 잠금 패턴을 모두 잊으면 되살릴 방법이 없습니다.
- 휴대폰에서 브라우저 메뉴 → **홈 화면에 추가** 를 하면 앱처럼 열립니다.

## 다시 만들 때

`web/diary.html`(본문)을 고친 뒤 저장소 뿌리에서:

```
python3 build-web.py web/diary.html
```
