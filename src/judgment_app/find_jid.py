from urllib.parse import parse_qs, urljoin, urlparse

import requests
from bs4 import BeautifulSoup


SEARCH_URL = "https://judgment.judicial.gov.tw/FJUD/Default_AD.aspx"
TIMEOUT = (10, 30)

def main():
    results = search_judgments(
        court="TPD",
        year=114,
        case="訴",
        number=219,
    )

    print(f"共找到 {len(results)} 筆裁判")
    for result in results:
        print(f"JID：{result['jid']}")
        print()


def get_soup(response):
    response.raise_for_status()
    return BeautifulSoup(response.content, "html.parser")


def search_judgments(
    court: str,
    year: int,
    case: str,
    number: int,
) -> list[dict[str, str]]:
    results = []
    seen_jids = set()
    visited_pages = set()

    with requests.Session() as session:
        # 1. 取得 ASP.NET 動態欄位及 Session Cookie。
        response = session.get(SEARCH_URL, timeout=TIMEOUT)
        soup = get_soup(response)

        form = soup.find("form")
        if form is None:
            raise RuntimeError("搜尋頁沒有表單，請確認網站是否正常。")

        # 只收集 hidden，避免誤送未勾選的 jud_sys checkbox。
        payload = {
            field["name"]: field.get("value", "")
            for field in form.select('input[type="hidden"][name]')
        }

        payload.update({
            "jud_court": court,
            "jud_sys": "M",
            "jud_year": str(year),
            "sel_judword": "",
            "jud_case": case,
            "jud_no": str(number),
            "jud_no_end": "",
            "dy1": "",
            "dm1": "",
            "dd1": "",
            "dy2": "",
            "dm2": "",
            "dd2": "",
            "jud_title": "",
            "jud_jmain": "",
            "jud_kw": "",
            "KbStart": "",
            "KbEnd": "",
            "judtype": "JUDBOOK",
            "whosub": "1",
            "ctl00$cp_content$btnQry": "送出查詢",
        })

        session.cookies.set(
            "fjud_ad",
            "1",
            domain="judgment.judicial.gov.tw",
            path="/",
        )

        # 2. POST 後，q 會出現在結果 iframe 的 src。
        post_url = urljoin(response.url, form.get("action") or response.url)
        response = session.post(
            post_url,
            data=payload,
            headers={"Referer": response.url},
            timeout=TIMEOUT,
        )
        soup = get_soup(response)

        iframe = soup.select_one("iframe#iframe-data[src]")
        if iframe is None:
            raise RuntimeError(
                "搜尋回應沒有結果 iframe，可能是網站格式變更或搜尋失敗。"
            )

        page_url = urljoin(response.url, iframe["src"])
        query = parse_qs(urlparse(page_url).query)

        if query.get("err") == ["Q003"]:
            return []

        if urlparse(page_url).path.lower().endswith("/errorpage.aspx"):
            raise RuntimeError(f"搜尋失敗：{page_url}")

        # 3. 讀取結果及後續分頁。
        while page_url:
            if page_url in visited_pages:
                raise RuntimeError("分頁連結重複，停止以避免無限迴圈。")
            visited_pages.add(page_url)

            response = session.get(page_url, timeout=TIMEOUT)
            soup = get_soup(response)

            page_jids = []
            for link in soup.select("a[href]"):
                link_url = urlparse(urljoin(response.url, link["href"]))

                if not link_url.path.lower().endswith("/data.aspx"):
                    continue

                # parse_qs 已經處理 URL decode，不需再 unquote。
                params = parse_qs(link_url.query)
                if params.get("ty") != ["JD"]:
                    continue

                jid = params.get("id", [""])[0]
                fields = jid.split(",")
                if len(fields) < 5:
                    raise RuntimeError(f"無法解析 JID：{jid!r}")

                page_jids.append(jid)
                if jid not in seen_jids:
                    seen_jids.add(jid)
                    results.append({
                        "jid": jid,
                        "date": fields[4],
                    })

            if not page_jids:
                raise RuntimeError(
                    f"結果頁沒有裁判連結，請檢查網站回應：{response.url}"
                )

            # 找「下一頁」，不跟隨排序或最後一頁連結。
            page_url = None
            for link in soup.select("a[href]"):
                label = " ".join([
                    link.get_text(" ", strip=True),
                    link.get("title", ""),
                    link.get("aria-label", ""),
                    *[
                        img.get("alt", "")
                        for img in link.select("img[alt]")
                    ],
                ])

                if "下一頁" not in label:
                    continue
                if (
                    link.has_attr("disabled")
                    or link.get("aria-disabled") == "true"
                    or "disabled" in link.get("class", [])
                ):
                    continue

                href = link["href"].strip()
                if not href or href == "#":
                    continue
                if href.lower().startswith("javascript:"):
                    raise RuntimeError(
                        "下一頁使用 JavaScript postback，需調整分頁處理。"
                    )

                page_url = urljoin(response.url, href)
                break

    return results


if __name__ == "__main__":
    main()