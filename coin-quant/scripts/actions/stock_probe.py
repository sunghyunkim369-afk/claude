"""GitHub Actions 에서 주식 데이터·뉴스 출처가 열리는지 확인 (결과는 로그로만)."""
import time
import requests

UA = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124 Safari/537.36"}
URLS = {
    "google news ko": "https://news.google.com/rss/search?q=%EC%BD%94%EC%8A%A4%ED%94%BC&hl=ko&gl=KR&ceid=KR:ko",
    "google news en": "https://news.google.com/rss/search?q=stock+market&hl=en-US&gl=US&ceid=US:en",
    "yna economy": "https://www.yna.co.kr/rss/economy.xml",
    "hankyung finance": "https://www.hankyung.com/feed/finance",
    "mk stock": "https://www.mk.co.kr/rss/50200011/",
    "cnbc markets": "https://search.cnbc.com/rs/search/combinedcms/view.xml?partnerId=wrss01&id=20409666",
    "marketwatch": "https://feeds.content.dowjones.io/public/rss/mw_topstories",
    "nasdaq earnings": "https://api.nasdaq.com/api/calendar/earnings?date=2026-10-27",
    "nasdaq econ": "https://api.nasdaq.com/api/calendar/economicevents?date=2026-10-27",
    "stooq spy": "https://stooq.com/q/d/l/?s=spy.us&i=d",
    "naver chart": "https://fchart.stock.naver.com/sise.nhn?symbol=005930&timeframe=day&count=5&requestType=0",
    "krx": "https://data.krx.co.kr/comm/bldAttendant/getJsonData.cmd",
    "fred": "https://fred.stlouisfed.org/graph/fredgraph.csv?id=DGS10",
    "sp500 list": "https://raw.githubusercontent.com/datasets/s-and-p-500-companies/main/data/constituents.csv",
    "investing calendar": "https://www.investing.com/economic-calendar/",
}
for k, u in URLS.items():
    t = time.time()
    try:
        r = requests.get(u, headers={**UA, "Accept": "application/json, text/plain, */*"}, timeout=20)
        print(f"{k:20s} {r.status_code} {len(r.content):>8d}B {time.time() - t:.1f}s  {r.text[:120]!r}")
    except Exception as e:
        print(f"{k:20s} ERR {type(e).__name__}: {str(e)[:100]}")

try:
    import yfinance as yf
    d = yf.download(["^GSPC", "^IXIC", "^KS11", "^KQ11", "005930.KS", "AAPL", "^VIX", "KRW=X"], period="10d",
                    progress=False, auto_adjust=True, threads=True)
    print("yfinance:", d["Close"].tail(2).round(2).to_string())
    h = yf.download("^GSPC", period="max", progress=False, auto_adjust=True)
    print("yfinance ^GSPC max:", h.index[0], len(h))
    k = yf.download("^KS11", period="max", progress=False, auto_adjust=True)
    print("yfinance ^KS11 max:", k.index[0], len(k))
except Exception as e:
    print("yfinance ERR", type(e).__name__, e)
try:
    import FinanceDataReader as fdr
    ls = fdr.StockListing("KOSPI")
    print("FDR KOSPI listing:", ls.shape, list(ls.columns)[:12])
    print(ls.head(3).to_string())
    print("FDR 005930:", fdr.DataReader("005930", "2026-10-01").tail(2).to_string())
    dl = fdr.StockListing("KRX-DELISTING")
    print("FDR delisting:", dl.shape)
except Exception as e:
    print("FDR ERR", type(e).__name__, e)
try:
    from pykrx import stock
    print("pykrx cap:", stock.get_market_cap("20261026").head(3).to_string())
except Exception as e:
    print("pykrx ERR", type(e).__name__, e)
