import pandas as pd
import struct
from collections import Counter

FILE = "/home/administrator/nse_fo_system/build/1367_packets.csv"

df = pd.read_csv(FILE)

print("\nLoaded packets:", len(df))

# ----------------------------------------
# parse hex bytes
# ----------------------------------------
packets = []

for hx in df["hex"]:
    try:
        packets.append(bytes.fromhex(hx))
    except:
        pass

print("Valid packets:", len(packets))

# ----------------------------------------
# check offsets
# ----------------------------------------
print("\n===================================")
print("OFFSET STABILITY / VALUE ANALYSIS")
print("===================================")

for off in range(0, 96, 4):

    vals = []

    for p in packets:
        if len(p) >= off + 4:
            v = struct.unpack(">I", p[off:off+4])[0]
            vals.append(v)

    if not vals:
        continue

    uniq = len(set(vals))
    common = Counter(vals).most_common(5)

    print(f"\nOFF {off:3d}")
    print("Unique:", uniq)
    print("Top values:", common[:5])

# ----------------------------------------
# price candidates
# ----------------------------------------
print("\n===================================")
print("PRICE-LIKE OFFSETS")
print("===================================")

for off in range(0, 96, 4):

    vals = []

    for p in packets:
        if len(p) >= off + 4:
            v = struct.unpack(">I", p[off:off+4])[0] / 100.0
            if 1 <= v <= 100000:
                vals.append(v)

    if len(vals) > 300:
        print(
            f"OFF {off:3d} -> count={len(vals)} "
            f"min={min(vals):.2f} "
            f"max={max(vals):.2f}"
        )

# ----------------------------------------
# token-like candidates
# ----------------------------------------
print("\n===================================")
print("TOKEN-LIKE OFFSETS")
print("===================================")

for off in range(0, 96, 4):

    vals = []

    for p in packets:
        if len(p) >= off + 4:
            v = struct.unpack(">I", p[off:off+4])[0]
            if 1000 <= v <= 50000000:
                vals.append(v)

    uniq = len(set(vals))

    if uniq > 50:
        print(f"OFF {off:3d} -> unique token-like values = {uniq}")