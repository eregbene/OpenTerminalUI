"""Round 17 data fetch: IP, CPI yoy, unemployment rate -> data/macro/econ/econ_tidy.csv
Sources: OECD SDMX (IP: DF_INDSERV 4.3 PRVM BTE SA; CPI: DF_PRICES_ALL GY quarterly for AUS/NZL;
unemployment: DF_IALFS_UNE_M SA total 15+), Eurostat une_rt_m (euro area EA21, SA), BIS WS_LONG_CPI (monthly yoy, 6 ccys).
Tidy columns: ccy, series (ip|cpi|une), freq (M|Q), ref (YYYY-MM = LAST month of the reference period), value, source."""
import io, urllib.request, pandas as pd
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
OUT = ROOT / 'data/macro/econ'; OUT.mkdir(parents=True, exist_ok=True)
AREA = {'USA': 'USD', 'EA20': 'EUR', 'JPN': 'JPY', 'GBR': 'GBP', 'CAN': 'CAD', 'CHE': 'CHF', 'AUS': 'AUD', 'NZL': 'NZD'}
KEYS = '+'.join(AREA)

def get(url, name):
    raw = urllib.request.urlopen(urllib.request.Request(url, headers={'User-Agent': 'Mozilla/5.0'}), timeout=300).read()
    (OUT / name).write_bytes(raw)
    return pd.read_csv(io.BytesIO(raw))

def ref_month(p):  # '2026-Q2' -> '2026-06'; '2026-08' -> '2026-08'
    if '-Q' in p:
        y, q = p.split('-Q'); return f'{y}-{int(q) * 3:02d}'
    return p

rows = []
def add(df, ccy_col, series, src):
    for _, r in df.iterrows():
        rows.append((r[ccy_col], series, r['FREQ'], ref_month(str(r['TIME_PERIOD'])), float(r['OBS_VALUE']), src))

# IP: monthly where it covers the whole sample, else quarterly (CHE monthly only from 2010-10; AUS/NZL quarterly only)
ip = get(f'https://sdmx.oecd.org/public/rest/data/OECD.SDD.STES,DSD_STES@DF_INDSERV,4.3/{KEYS}.M+Q.PRVM.IX.BTE.Y.._Z.?startPeriod=1980&format=csvfile', 'raw_oecd_ip.csv')
ip['ccy'] = ip.REF_AREA.map(AREA)
want = {c: ('Q' if c in ('CHF', 'AUD', 'NZD') else 'M') for c in AREA.values()}
ip = ip[ip.apply(lambda r: r.FREQ == want[r.ccy], axis=1)]
add(ip, 'ccy', 'ip', 'OECD DF_INDSERV PRVM BTE SA')

# CPI yoy: BIS monthly for USD EUR JPY GBP CAD CHF; OECD quarterly for AUD NZD
b = pd.read_csv(ROOT / 'data/macro/cpi/WS_LONG_CPI_g8.csv', index_col=0)
for c in ['USD', 'EUR', 'JPY', 'GBP', 'CAD', 'CHF']:
    for m, v in b[c].dropna().items():
        rows.append((c, 'cpi', 'M', m, float(v), 'BIS WS_LONG_CPI yoy'))
cq = get('https://sdmx.oecd.org/public/rest/data/OECD.SDD.TPS,DSD_PRICES@DF_PRICES_ALL,1.0/AUS+NZL.Q.N.CPI.PA._T.N.GY?startPeriod=1980&format=csvfile', 'raw_oecd_cpi_q.csv')
cq['ccy'] = cq.REF_AREA.map(AREA)
add(cq, 'ccy', 'cpi', 'OECD DF_PRICES_ALL CPI GY')

# Unemployment rate SA, total 15+: OECD monthly (CHE, NZL quarterly), euro area from Eurostat
u = get(f'https://sdmx.oecd.org/public/rest/data/OECD.SDD.TPS,DSD_LFS@DF_IALFS_UNE_M,1.0/{KEYS}.UNE_LF_M.PT_LF_SUB._Z.Y._T.Y_GE15._Z.M+Q?startPeriod=1980&format=csvfile', 'raw_oecd_une.csv')
u['ccy'] = u.REF_AREA.map(AREA)
u = u[u.apply(lambda r: r.FREQ == ('Q' if r.ccy in ('CHF', 'NZD') else 'M'), axis=1)]
add(u, 'ccy', 'une', 'OECD DF_IALFS_UNE_M SA 15+')
e = get('https://ec.europa.eu/eurostat/api/dissemination/sdmx/2.1/data/une_rt_m/M.SA.TOTAL.PC_ACT.T.EA21?format=SDMX-CSV&startPeriod=1980', 'raw_eurostat_une_ea21.csv')
e['FREQ'] = 'M'; e['ccy'] = 'EUR'
add(e, 'ccy', 'une', 'Eurostat une_rt_m EA21 SA')

t = pd.DataFrame(rows, columns=['ccy', 'series', 'freq', 'ref', 'value', 'source']).sort_values(['ccy', 'series', 'ref'])
t = t.drop_duplicates(['ccy', 'series', 'ref'])
t.to_csv(OUT / 'econ_tidy.csv', index=False)
cov = t.groupby(['ccy', 'series', 'freq', 'source']).ref.agg(['min', 'max', 'count'])
cov.to_csv(OUT / 'coverage.csv'); print(cov.to_string())
