"""Registry of data collectors. Add a provider by adding it here and to docs/data-sources.md."""

from cryptoagent.collectors.base import Collector
from cryptoagent.collectors.coinmetrics import CoinMetricsCollector
from cryptoagent.collectors.exchange import ExchangeCollector

COLLECTORS: dict[str, Collector] = {
    c.name: c
    for c in (
        ExchangeCollector("coinbase", "coinbaseexchange", "Coinbase Exchange daily candles, every USD spot market",
                          page_limit=300),
        ExchangeCollector("bitstamp", "bitstamp", "Bitstamp BTC/USD daily candles (BTC history from 2011-08-18)",
                          page_limit=1000, symbols=["BTC/USD"]),
        CoinMetricsCollector(),
    )
}
