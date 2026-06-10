"""Shared physiological constants.

Single-user test app values — replace with per-user columns (bodyweight,
measured BMR) when accounts grow beyond the owner. BMR_KCAL is also echoed
in API responses so the iOS client renders energy balance with the same
number the backend flags against.
"""

BMR_KCAL = 1700
BODYWEIGHT_KG = 75.0
PROTEIN_FLOOR_G_PER_KG = 1.2
CALORIE_BALANCE_THRESHOLD_KCAL = 300
