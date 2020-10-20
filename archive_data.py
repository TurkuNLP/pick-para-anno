import json
import glob

def read_files():
    json_files = glob.glob("*.json")
    return sorted(json_files)
    
    
def is_completed(fname):

    with open(fname, "rt", encoding="utf-8") as f:
        data = json.load(f)
        
    # data is a list of examples
    if "annotation_ready" in data and data["annotation_ready"] == True:
        return True
    else:
        return False


def main():

    files = read_files()
    completed_files = []
    for fname in files:
        completed = is_completed(fname)
        if completed == True:
            completed_files.append(fname)
    print(f"Completed {len(completed_files)} out of {len(files)} total files.")
    print("Archive completed:")
    print()
    print(f"mv {' '.join(completed_files)} archived/ ; git add {' '.join(completed_files)} ; git add archived/* ; git status ; git commit -m 'Jenna, commit completed'")
    print()
    

                
main()


# cd home/ginter/pick_ann_data_live_new/batches-JennaK (etc.)
# python3 /home/jmnybl/git_checkout/pick-para-anno/archive_data.py




