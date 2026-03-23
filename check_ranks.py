import os

directory = r"C:\Users\Maksim\Desktop\code\ai\mmr-counter\media\Dota2RanksIcons_png"
files = sorted(os.listdir(directory))
print("Total files:", len(files))
print("Files:", files)
