import pandas as pd
from vnstock import Quote, Listing

# Check if we can get VNINDEX
try:
    q = Quote(symbol="VNINDEX")
    df = q.history(start="2024-01-01", end="2024-01-10", interval="1D")
    print("VNINDEX history:")
    print(df)
except Exception as e:
    print("Error VNINDEX:", e)

# Test listing
try:
    listing = Listing(source="vci")
    hose_df = listing.symbols_by_exchange(exchange="HOSE", to_df=True)
    print("HOSE symbols count:", len(hose_df))
    print(hose_df.head())
except Exception as e:
    print("Error Listing:", e)
