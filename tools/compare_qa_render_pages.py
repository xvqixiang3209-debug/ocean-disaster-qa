"""Compare final page pixels to an already visually inspected rendering."""
import argparse,hashlib,json,re
from pathlib import Path
from PIL import Image

def page_map(path):
    return {int(re.search(r'page-(\d+)',p.name).group(1)):p for p in path.glob('page-*.png')}

def pixels(path):
    with Image.open(path) as image:
        rgb=image.convert('RGB')
        return rgb.size,hashlib.sha256(rgb.tobytes()).hexdigest()

def main():
    ap=argparse.ArgumentParser(description=__doc__)
    ap.add_argument('before',type=Path);ap.add_argument('after',type=Path)
    ap.add_argument('--output',type=Path)
    args=ap.parse_args()
    before=page_map(args.before);after=page_map(args.after)
    identical=[];changed=[];rows=[]
    for page in sorted(set(before)|set(after)):
        old=pixels(before[page]) if page in before else None
        new=pixels(after[page]) if page in after else None
        same=old is not None and old==new
        (identical if same else changed).append(page)
        rows.append({'page':page,'identical_pixels':same,'before_size':old[0] if old else None,'after_size':new[0] if new else None,'before_pixel_sha256':old[1] if old else None,'after_pixel_sha256':new[1] if new else None})
    result={'before':str(args.before),'after':str(args.after),'before_pages':len(before),'after_pages':len(after),
            'identical_pages':identical,'changed_pages':changed,'changed_grid_numbers':sorted({(p-1)//4+1 for p in changed}),'pages':rows}
    output=args.output or args.after/'pixel_comparison.json'
    output.write_text(json.dumps(result,ensure_ascii=False,indent=2),encoding='utf-8')
    print(json.dumps({k:v for k,v in result.items() if k!='pages'},ensure_ascii=False))

if __name__=='__main__':main()
