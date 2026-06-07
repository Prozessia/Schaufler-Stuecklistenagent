"""Read regression results and output compact summary."""

import sys

lines = open("scripts/reg_result.txt", "r", encoding="utf-8").readlines()
for line in lines:
    s = line.strip()
    if s and ("OK" in s or "EMPTY" in s or "ERROR" in s or s.startswith("OK:")):
        print(s)
