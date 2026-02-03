import random
import csv
from datetime import date

NUM_LOCALITIES = 67
TODAY = date.today().isoformat()

# ----------------------------------
# Helpers
# ----------------------------------

def rand_ratio(min_v, max_v):
    return random.uniform(min_v, max_v)

def clamp(val, min_v=0):
    return max(int(val), min_v)

# ----------------------------------
# Base locality populations
# ----------------------------------

localities = []

for i in range(1, NUM_LOCALITIES + 1):
    population = random.randint(1500, 45000)
    localities.append({
        "id": i,
        "population": population
    })

# ----------------------------------
# 1️⃣ Profession-Level Table
# ----------------------------------

professions = [
    "doctors",
    "nurses",
    "teachers",
    "engineers_computer",
    "engineers_civil",
    "engineers_electrical",
    "employees_public",
    "employees_private",
    "agriculture_workers",
    "construction_workers",
    "technicians",
    "freelancers",
    "business_owners",
    "students_university",
    "unemployed"
]

with open("profession_stats.csv", "w", newline="") as f:
    writer = csv.writer(f)
    writer.writerow(["locality_id", "profession", "count", "last_updated"])

    for loc in localities:
        pop = loc["population"]
        workforce = int(pop * rand_ratio(0.35, 0.5))

        distribution = {
            "doctors": workforce * rand_ratio(0.002, 0.006),
            "nurses": workforce * rand_ratio(0.004, 0.01),
            "teachers": workforce * rand_ratio(0.05, 0.08),
            "engineers_computer": workforce * rand_ratio(0.01, 0.025),
            "engineers_civil": workforce * rand_ratio(0.008, 0.02),
            "engineers_electrical": workforce * rand_ratio(0.005, 0.015),
            "employees_public": workforce * rand_ratio(0.12, 0.18),
            "employees_private": workforce * rand_ratio(0.18, 0.25),
            "agriculture_workers": workforce * rand_ratio(0.03, 0.1),
            "construction_workers": workforce * rand_ratio(0.06, 0.12),
            "technicians": workforce * rand_ratio(0.05, 0.08),
            "freelancers": workforce * rand_ratio(0.04, 0.07),
            "business_owners": workforce * rand_ratio(0.02, 0.05),
            "students_university": pop * rand_ratio(0.07, 0.12),
            "unemployed": workforce * rand_ratio(0.25, 0.4)
        }

        for prof, val in distribution.items():
            writer.writerow([
                loc["id"],
                prof,
                clamp(val),
                TODAY
            ])

# ----------------------------------
# 2️⃣ Education Distribution Table
# ----------------------------------

education_levels = [
    "no_schooling",
    "primary",
    "secondary",
    "diploma",
    "bachelor",
    "master",
    "phd"
]

with open("education_distribution.csv", "w", newline="") as f:
    writer = csv.writer(f)
    writer.writerow(["locality_id", "education_level", "count", "last_updated"])

    for loc in localities:
        pop = loc["population"]

        edu_dist = {
            "no_schooling": pop * rand_ratio(0.03, 0.07),
            "primary": pop * rand_ratio(0.18, 0.25),
            "secondary": pop * rand_ratio(0.25, 0.32),
            "diploma": pop * rand_ratio(0.08, 0.12),
            "bachelor": pop * rand_ratio(0.12, 0.2),
            "master": pop * rand_ratio(0.02, 0.05),
            "phd": pop * rand_ratio(0.002, 0.008)
        }

        for level, val in edu_dist.items():
            writer.writerow([
                loc["id"],
                level,
                clamp(val),
                TODAY
            ])

# ----------------------------------
# 3️⃣ Gender & Youth Employment Table
# ----------------------------------

with open("employment_gender_youth.csv", "w", newline="") as f:
    writer = csv.writer(f)
    writer.writerow([
        "locality_id",
        "male_employed",
        "female_employed",
        "male_unemployed",
        "female_unemployed",
        "youth_employed_18_29",
        "youth_unemployed_18_29",
        "last_updated"
    ])

    for loc in localities:
        pop = loc["population"]
        workforce = int(pop * rand_ratio(0.35, 0.5))

        male_ratio = rand_ratio(0.6, 0.68)
        female_ratio = 1 - male_ratio

        employed = workforce * rand_ratio(0.6, 0.75)
        unemployed = workforce - employed

        male_emp = employed * male_ratio
        female_emp = employed * female_ratio

        male_unemp = unemployed * male_ratio
        female_unemp = unemployed * female_ratio

        youth_workforce = workforce * rand_ratio(0.35, 0.45)
        youth_employed = youth_workforce * rand_ratio(0.45, 0.6)
        youth_unemployed = youth_workforce - youth_employed

        writer.writerow([
            loc["id"],
            clamp(male_emp),
            clamp(female_emp),
            clamp(male_unemp),
            clamp(female_unemp),
            clamp(youth_employed),
            clamp(youth_unemployed),
            TODAY
        ])

print("✅ Synthetic datasets generated successfully!")