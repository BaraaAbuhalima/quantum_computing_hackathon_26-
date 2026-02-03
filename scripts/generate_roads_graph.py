import pandas as pd
import networkx as nx
import matplotlib.pyplot as plt

# -----------------------------
# Paths to CSV files
# -----------------------------
LOCALITIES_CSV = "/Users/suhaibsawalha/Documents/Quantum&AI_Hackathon/data/localities.csv"
ROADS_CSV = "/Users/suhaibsawalha/Documents/Quantum&AI_Hackathon/data/roads_between_localities.csv"

# -----------------------------
# Robust CSV loading
# -----------------------------
def load_csv(path):
    try:
        df = pd.read_csv(path, sep=',', quotechar='"', encoding='utf-8', on_bad_lines='skip')
        print(f"✅ Loaded {path} ({df.shape[0]} rows, {df.shape[1]} columns)")
        return df
    except Exception as e:
        print(f"❌ Failed to load {path}: {e}")
        return pd.DataFrame()

localities_df = load_csv(LOCALITIES_CSV)
roads_df = load_csv(ROADS_CSV)

# -----------------------------
# Build the graph
# -----------------------------
G = nx.Graph()

# Add nodes with coordinates
for _, row in localities_df.iterrows():
    try:
        G.add_node(
            row["id"],
            name=row["name"],
            pos=(float(row["longitude"]), float(row["latitude"]))
        )
    except Exception as e:
        print(f"⚠ Skipping node due to error: {e}")

# Add edges (roads)
for _, row in roads_df.iterrows():
    try:
        G.add_edge(
            int(row["from_locality_id"]),
            int(row["to_locality_id"]),
            distance=float(row.get("distance_km", 1.0)),
            road_type=row.get("road_type", "residential")
        )
    except Exception as e:
        print(f"⚠ Skipping edge due to error: {e}")

# -----------------------------
# Draw the graph
# -----------------------------
pos = nx.get_node_attributes(G, "pos")
labels = nx.get_node_attributes(G, "name")

plt.figure(figsize=(12, 12))
nx.draw_networkx_nodes(G, pos, node_size=200, node_color="skyblue")
nx.draw_networkx_edges(G, pos, width=1, alpha=0.7)
nx.draw_networkx_labels(G, pos, labels, font_size=8)

plt.title("Road Network Graph - Ramallah & Surroundings")
plt.axis("off")
plt.tight_layout()
plt.show()

# -----------------------------
# Save figure
# -----------------------------
plt.savefig("/Users/suhaibsawalha/Documents/Quantum&AI_Hackathon/csv/road_network_graph.png", dpi=300)
print("✅ Graph drawn and saved as road_network_graph.png")
