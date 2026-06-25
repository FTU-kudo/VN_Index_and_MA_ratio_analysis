from vnstock import Listing

try:
    listing = Listing(source="kbs")
    hose_df = listing.symbols_by_exchange(exchange="HOSE", to_df=True)
    if 'type' in hose_df.columns:
        hose_df = hose_df[hose_df['type'].str.lower() == 'stock']
    symbols = hose_df['symbol'].tolist()
    symbols = [s for s in symbols if len(str(s)) == 3 and str(s).isalpha()]
    print("HOSE 3-letter stock symbols count:", len(symbols))
except Exception as e:
    print("Error:", e)
