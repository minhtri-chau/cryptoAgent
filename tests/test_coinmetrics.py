"""The reference price is only useful as an independent check if it is complete and
never duplicated: follow every page, resume from the last stored day."""

import json
from urllib.parse import parse_qs, urlparse

from cryptoagent.collectors.coinmetrics import CoinMetricsCollector


class FakeCoinMetrics:
    def __init__(self, days):
        self.days, self.calls = days, []

    def __call__(self, url):
        self.calls.append(url)
        q = parse_qs(urlparse(url).query)
        start = q.get("start_time", [""])[0]
        rows = [{"asset": "btc", "time": f"{d}T00:00:00.000000000Z", "PriceUSD": str(p)}
                for d, p in self.days if d >= start]
        page = int(q.get("page", ["0"])[0])
        body = {"data": rows[page * 2:(page + 1) * 2]}
        if (page + 1) * 2 < len(rows):
            body["next_page_url"] = url.split("&page=")[0] + f"&page={page + 1}"
        return json.dumps(body).encode()


DAYS = [("2024-01-01", 42000.5), ("2024-01-02", 44000.0), ("2024-01-03", 43000.0)]


def test_follows_pages_and_resumes_without_duplicates(conn, settings):
    fake = FakeCoinMetrics(DAYS)
    first = CoinMetricsCollector(fetch=fake, delay_s=0).collect(conn, settings)
    assert first["values_added"] == 3 and len(fake.calls) == 2
    assert "paging_from=start" in fake.calls[0]
    fake.days = DAYS + [("2024-01-04", 45000.0)]
    second = CoinMetricsCollector(fetch=fake, delay_s=0).collect(conn, settings)
    assert second["values_added"] == 1
    assert "start_time=2024-01-03" in fake.calls[2]
    rows = conn.execute("SELECT day, value FROM reference_prices ORDER BY day").fetchall()
    assert [tuple(r) for r in rows][-1] == ("2024-01-04", 45000.0) and len(rows) == 4
