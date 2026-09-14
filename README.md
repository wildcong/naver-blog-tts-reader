# 개인 블로그 오디오 리더

## 실행 및 배포

Python 3.14와 `requirements.txt`의 고정된 버전을 사용합니다.

```sh
python -m pip install -r requirements.txt
streamlit run app.py
```

배포 시작 파일도 **`app.py`**입니다. 공식 `st.App`의 미들웨어가 검색 제외
헤더를 적용하고 공개 음성·업로드 경로를 차단합니다. `reader.py`는
리더 화면을 담으며 본문 조회·음성 생성 전에 서버에서 인증을 검증합니다.

Streamlit Community Cloud의 앱 Settings → Secrets에 아래 서버 설정을
저장해야 합니다. 로컬에서는 `.streamlit/secrets.toml`에 저장합니다.

```toml
APP_COOKIE_SECRET = "독립적으로 생성한 32자 이상의 임의 비밀키"
# 선택: 기본 비밀번호를 변경할 때만 추가합니다.
# APP_PASSWORD = "새 비밀번호"
```

비밀키는 예시 문구를 복사하지 말고 비밀번호 관리 도구 또는
`python -c 'import secrets; print(secrets.token_urlsafe(48))'`로 생성합니다.
비밀키가 없거나 올바르지 않으면 앱은 접근을 허용하지 않습니다.
서버 비밀키를 유지하면 앱 재시작·재배포 후에도 로그인 기록을 검증할 수
있습니다. 비밀번호 또는 비밀키를 바꾸면 기존 로그인이 해제됩니다.

## 접근 및 검색 차단

- 비밀번호 확인은 서버에서 수행하며 기본 비밀번호의 검증값만 코드에 저장합니다.
- Streamlit Cloud는 임의의 쿠키를 서버로 전달하지 않으므로 공식 v2
  컴포넌트로 서명된 로그인 토큰만 브라우저 `localStorage`에 저장합니다.
  비밀번호와 서버 비밀키는 컴포넌트로 전달하지 않습니다.
- 로그인 기록은 로그인 시점부터 정확히 30일간 유효합니다. 재방문으로
  만료일을 연장하지 않으며 서버가 서명과 만료 시점을 검증합니다.
- 사이트 데이터를 지우거나 시크릿 창·다른 브라우저를 사용하면 다시 로그인해야 합니다.
- 로그아웃은 브라우저의 로그인 기록을 삭제합니다. 서버 프로세스가 실행 중인 동안
  해당 토큰의 재사용도 차단합니다.
- 로그인 시도 제한은 서버 프로세스 전체에 적용되며 프로세스 재시작 시 초기화됩니다.
- 모든 HTTP 응답에 `X-Robots-Tag: noindex, nofollow, noarchive, nosnippet`과
  `Cache-Control: private, no-store`를 적용합니다. 앱 서버의 검색 제외 정책은
  로그인 전 첫 HTML에도 메타태그로 포함되어 Cloud가 헤더를 제거해도 유지됩니다.
  Cloud가 별도로 제공하는 외부 프레임에는
  브라우저 컴포넌트가 같은 출처의 상위 문서까지 검색 제외 메타태그를 넣습니다.
  검색봇이 `noindex`를 확인할 수 있도록 `robots.txt`는
  크롤링을 허용하되 본문·음성은 인증으로 차단합니다.
- 앱의 빈 초기 화면과 실시간 연결은 공개되지만, 인증되지 않은 실행은
  로그인 화면에서 중단되어 본문 조회와 음성 생성이 실행되지 않습니다.
- 음성과 다운로드는 인증된 연결로 받은 바이트를 브라우저 메모리에서
  재생·저장합니다. 외부에서 열 수 있는 공개 `/media` 주소를 생성하지 않습니다.
- GitHub Actions의 앱 깨우기는 비밀번호 화면을 정상 응답으로 취급합니다.
- 현재 배포처럼 도메인의 루트 경로(`/`)에서 실행합니다.

기존 검색 결과는 검색엔진이 다시 방문한 뒤 삭제되므로 즉시 사라지지는
않습니다. 앱의 검색 차단과 GitHub 저장소의 공개 여부는 별도 설정입니다.
Streamlit 호스팅 자체를 비공개로 바꾸면 Streamlit 계정 로그인도 요구됩니다.

## 검증

```sh
python -m pip install -r requirements-dev.txt
python -m pytest -q
```

인증 전 실행 중단, 로그인 기록 변조와 만료, 재방문, 로그아웃, 음성 주소
차단을 검증합니다.

참고: [Streamlit st.App](https://docs.streamlit.io/1.59.0/develop/api-reference/server/st.app),
[Google 검색 제외 규칙](https://developers.google.com/search/docs/crawling-indexing/block-indexing).
