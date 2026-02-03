import pandas as pd
import folium

# -----------------------------
# Paths to your CSV files
# -----------------------------
LOCALITIES_CSV = "/Users/suhaibsawalha/Documents/Quantum&AI_Hackathon/data/localities.csv"
ROADS_CSV = "/Users/suhaibsawalha/Documents/Quantum&AI_Hackathon/data/roads_between_localities.csv"

# -----------------------------
# Load CSV files robustly
# -----------------------------
localities_df = pd.read_csv(LOCALITIES_CSV, sep=',', quotechar='"', on_bad_lines='skip')
roads_df = pd.read_csv(ROADS_CSV, sep=',', quotechar='"', on_bad_lines='skip')

# -----------------------------
# Create a map centered on Ramallah
# -----------------------------
m = folium.Map(location=[31.9038, 35.2034], zoom_start=11, tiles="CartoDB positron")

# -----------------------------
# Add localities as markers
# -----------------------------
for _, row in localities_df.iterrows():
    folium.CircleMarker(
        location=[row['latitude'], row['longitude']],
        radius=5,
        color='blue',
        fill=True,
        fill_opacity=0.7,
        popup=f"{row['name']}\nType: {row['locality_type']}"
    ).add_to(m)

# -----------------------------
# Color map for road types
# -----------------------------
road_color = {
    'primary': 'red',
    'secondary': 'orange',
    'residential': 'gray'
}

# Build a mapping: locality ID -> (lat, lon)
id_to_coords = {row['id']: (row['latitude'], row['longitude']) for _, row in localities_df.iterrows()}

# Add roads as polylines using mapped coordinates
for _, row in roads_df.iterrows():
    try:
        from_id = int(row['from_locality_id'])
        to_id = int(row['to_locality_id'])
        road_type = str(row.get('road_type', 'residential')).strip().lower()
        
        from_coord = id_to_coords.get(from_id)
        to_coord = id_to_coords.get(to_id)
        
        if from_coord and to_coord:
            folium.PolyLine(
                locations=[from_coord, to_coord],
                color=road_color.get(road_type, 'gray'),
                weight=2,
                opacity=0.6,
                tooltip=f"{road_type} - {row.get('distance_km', '?')} km"
            ).add_to(m)
        else:
            print(f"⚠ Missing coordinates for {from_id} -> {to_id}")
    except Exception as e:
        print(f"⚠ Skipping road due to error: {e}")


# -----------------------------
# Save map as HTML
# -----------------------------
output_file = "/Users/suhaibsawalha/Documents/Quantum&AI_Hackathon/graph/road_network_map.html"
m.save(output_file)
print(f"✅ Interactive road network map saved as {output_file}")
