import os
import shutil

src_dir = r"C:\Users\Maksim\Desktop\code\ai\mmr-counter\media\Dota2RanksIcons_png"
dest_dir = r"C:\Users\Maksim\Desktop\code\ai\mmr-counter\media\ranks"

os.makedirs(dest_dir, exist_ok=True)

# Collect all rank files: 004 to 052
files = sorted([f for f in os.listdir(src_dir) if f.endswith(".png")])
rank_files = [f for f in files if "004" <= f[:3] <= "052"]

for i, f in enumerate(rank_files, start=1):
    src_path = os.path.join(src_dir, f)
    dest_path = os.path.join(dest_dir, f"{i}.png")
    shutil.copy2(src_path, dest_path)
    print(f"Copied {f} to {i}.png")
