from vnstock import Quote
import pandas as pd

# Test list
try:
    q = Quote(symbol=["VCI", "SSI"])
    df = q.history(start="2024-01-01", end="2024-01-10", interval="1D")
    print("Batch with list:")
    print(df)
except Exception as e:
    print("Error with list:", type(e), e)

# Test string
try:
    q = Quote(symbol="VCI,SSI")
    df = q.history(start="2024-01-01", end="2024-01-10", interval="1D")
    print("Batch with comma string:")
    print(df)
except Exception as e:
    print("Error with comma string:", type(e), e)
