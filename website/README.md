# VN-Index & MA Market Breadth Website (GitHub Pages)

## What this website does
- Loads `website/data/market_breadth_daily.json`
- Lets users toggle MA10/MA20/MA50/MA200 checkboxes
- Shows VN-Index + selected MA breadth lines simultaneously

## Setup for GitHub Pages
1. Commit/push this `website/` folder to your repo.
2. Create a GitHub Pages workflow/config to publish from the `website/` directory.

## Expected data file
`website/data/market_breadth_daily.json`

Schema (array of objects):
- `time`: YYYY-MM-DD
- `VNINDEX`: number
- `pct_MA10`, `pct_MA20`, `pct_MA50`, `pct_MA200`: numbers

## Generating the JSON
- Run `analysis.py --build` first to generate `hose_6years_data.csv`.
- Then run `python website/build_data.py`.

> Note: `build_data.py` expects `vnindex.csv` to exist if you want real VNINDEX values.
> If missing, VNINDEX will be filled with 0 (chart still works, but left axis is not meaningful).

